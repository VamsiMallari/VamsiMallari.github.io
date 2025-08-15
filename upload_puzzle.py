#!/usr/bin/env python3
"""
upload_puzzle.py

- Supports credentials via:
    1) CLI arg: path to service account JSON (old behavior)
    2) Environment variable FIREBASE_CREDENTIALS (base64-encoded JSON)

- Auto-installs missing Python packages (helps GitHub Actions when a package was omitted).
- Fetches Lichess daily puzzle, computes board position at initialPly using python-chess,
  converts board into a Firestore-safe mapping, flattens solutions, checks duplicates,
  and uploads to Firestore collection "puzzles".
- Deletes puzzles older than 30 days (same behavior as your previous script).

Dependencies (the script will try to install these automatically if missing):
  - python-chess
  - firebase-admin
  - requests
"""
#!/usr/bin/env python3
import os
import sys
import base64
import json
import tempfile
import traceback
from datetime import datetime, timezone, timedelta

import requests
import firebase_admin
from firebase_admin import credentials, firestore
import chess
import chess.pgn
from subprocess import check_call, CalledProcessError

# Helper to install packages on the fly
def ensure_package(pkg_name, import_name=None):
    if import_name is None:
        import_name = pkg_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"[install] Missing package '{pkg_name}'. Installing...", flush=True)
        try:
            check_call([sys.executable, "-m", "pip", "install", pkg_name])
            time.sleep(0.5)
            __import__(import_name)
            print(f"[install] Successfully installed '{pkg_name}'.", flush=True)
        except CalledProcessError as e:
            print(f"[install] Failed to install {pkg_name}: {e}", file=sys.stderr)
            raise
        except ImportError:
            print(f"[install] Import still failing for {pkg_name} after install.", file=sys.stderr)
            raise

# Ensure critical libs
ensure_package("requests")
ensure_package("firebase-admin")
ensure_package("python-chess", import_name="chess")

import requests
import firebase_admin
from firebase_admin import credentials, firestore
import chess
import chess.pgn

LICHESS_DAILY_URL = "https://lichess.org/api/puzzle/daily"
FIRESTORE_COLLECTION = "puzzles"
DELETE_OLDER_THAN_DAYS = 30

def load_firebase_cred(temp_write=True):
    """
    Load firebase credentials either from CLI arg or from FIREBASE_CREDENTIALS env var.
    Returns a credentials.Certificate object ready to use with firebase_admin.
    If temp_write True, writes to a temp file and returns credentials.Certificate(temp_file).
    """
    # 1) CLI arg path
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        path = sys.argv[1]
        print(f"[cred] Using credentials file from CLI arg: {path}")
        return credentials.Certificate(path)

    # 2) FIREBASE_CREDENTIALS env (base64 or raw JSON)
    b64 = os.getenv("FIREBASE_CREDENTIALS")
    if not b64:
        raise RuntimeError("No credentials provided. Provide path as first arg or set FIREBASE_CREDENTIALS (base64-encoded JSON).")

    # detect if looks like raw JSON vs base64 by first non-space char
    stripped = b64.strip()
    json_bytes = None
    if stripped.startswith("{"):
        # raw JSON
        json_bytes = stripped.encode("utf-8")
    else:
        # try base64 decode
        try:
            json_bytes = base64.b64decode(stripped)
        except Exception as e:
            raise RuntimeError("FIREBASE_CREDENTIALS not valid base64 or JSON.") from e

    # Write to temp file since credentials.Certificate accepts a filename reliably
    if temp_write:
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tf.write(json_bytes)
        tf.flush()
        tf.close()
        print(f"[cred] Wrote temp credentials to {tf.name}")
        return credentials.Certificate(tf.name)
    else:
        # If you prefer passing dict directly, firebase accepts dict as well
        try:
            creds_dict = json.loads(json_bytes.decode("utf-8"))
            return credentials.Certificate(creds_dict)
        except Exception:
            # fallback to temp file method
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
            tf.write(json_bytes)
            tf.flush()
            tf.close()
            return credentials.Certificate(tf.name)


def init_firestore():
    cred = load_firebase_cred()
    try:
        firebase_admin.initialize_app(cred)
    except ValueError:
        # already initialized in this process
        pass
    db = firestore.client()
    return db


def fetch_lichess_daily():
    print(f"[lichess] Fetching {LICHESS_DAILY_URL}")
    r = requests.get(LICHESS_DAILY_URL, timeout=20)
    r.raise_for_status()
    return r.json()

def pgn_to_board_mapping_and_fen(pgn_text, initial_ply):
    """
    Use python-chess to parse PGN and play moves up to initial_ply (half-moves).
    Returns (fen, mapping, turn) where mapping is { 'e4': 'P', ... }
    """
    # parse pgn into a Game
    try:
        pgn_io = chess.pgn.StringIO(pgn_text)
        game = chess.pgn.read_game(pgn_io)
    except Exception as e:
        raise RuntimeError("Failed to parse PGN with python-chess") from e

    if game is None:
        # fallback: try to interpret the PGN as SAN move list in a single line
        board = chess.Board()
        moves = []
        for token in pgn_text.strip().split():
            # skip move numbers like '1.' '2.'
            if token.endswith("."):
                continue
            # try push_san
            try:
                board.push_san(token)
                moves.append(token)
            except Exception:
                # ignore unparsable tokens
                pass
        # now we have board after all moves
    else:
        board = game.board()
        moves_list = list(game.mainline_moves())
        n = int(initial_ply) if initial_ply is not None else 0
        n = max(0, min(n, len(moves_list)))
        for mv in moves_list[:n]:
            board.push(mv)

    fen = board.fen()
    mapping = {}
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece:
            # piece.symbol(): 'P' or 'p' or 'K' etc. Keep it as-is.
            mapping[chess.square_name(sq)] = piece.symbol()

    turn = 'white' if board.turn == chess.WHITE else 'black'
    return fen, mapping, turn


def normalize_solutions(sol):
    if sol is None:
        return []
    if isinstance(sol, str):
        return [sol]
    if isinstance(sol, (list, tuple)):
        # flatten one level
        flattened = []
        for item in sol:
            if isinstance(item, (list, tuple)):
                for x in item:
                    flattened.append(str(x))
            else:
                flattened.append(str(item))
        return flattened
    return [str(sol)]


def puzzle_exists(db, source_id):
    if not source_id:
        return False
    try:
        docs = db.collection(FIRESTORE_COLLECTION).where("sourceId", "==", source_id).limit(1).get()
        return len(docs) > 0
    except Exception as e:
        print("[db] Duplicate check failed, continuing:", e)
        return False


def delete_old_puzzles(db, days=DELETE_OLDER_THAN_DAYS):
    try:
        print(f"[maintenance] Deleting puzzles older than {days} days (if any).")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for doc in db.collection(FIRESTORE_COLLECTION).stream():
            data = doc.to_dict() or {}
            created = data.get("createdAt")
            if not created:
                continue
            try:
                # created might be a datetime or Firestore Timestamp-like
                if hasattr(created, "to_datetime"):
                    created_dt = created.to_datetime()
                elif hasattr(created, "replace") and hasattr(created, "isoformat"):
                    created_dt = created
                else:
                    # try parse ISO string
                    created_dt = datetime.fromisoformat(str(created))
            except Exception:
                continue
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            if created_dt < cutoff:
                try:
                    doc.reference.delete()
                    print(f"[maintenance] Deleted old puzzle {doc.id}")
                except Exception as e:
                    print(f"[maintenance] Failed to delete {doc.id}: {e}")
    except Exception as e:
        print("[maintenance] Error deleting old puzzles:", e)


def build_document_from_lichess(data):
    puzzle = data.get("puzzle", {}) or {}
    game = data.get("game", {}) or {}

    pgn = game.get("pgn", "") or ""
    initial_ply = puzzle.get("initialPly", 0)

    # compute board via PGN if available
    fen = None
    mapping = {}
    turn = 'white'
    try:
        fen, mapping, turn = pgn_to_board_mapping_and_fen(pgn, initial_ply)
    except Exception as e:
        print("[build] PGN parsing failed:", e)
        # fallback if game has fen property (rare)
        if "fen" in game and game.get("fen"):
            try:
                # fen may contain side to move and extra fields; keep full fen
                f = game.get("fen")
                # Try to convert with python-chess anyway if available
                try:
                    board = chess.Board(f)
                    mapping = {}
                    for sq in chess.SQUARES:
                        piece = board.piece_at(sq)
                        if piece:
                            mapping[chess.square_name(sq)] = piece.symbol()
                    fen = f
                    turn = 'white' if board.turn == chess.WHITE else 'black'
                except Exception:
                    # best-effort fallback: store fen string and empty mapping
                    fen = f
            except Exception:
                pass

    solutions = normalize_solutions(puzzle.get("solution") or puzzle.get("solutions") or [])

    pid = puzzle.get("id") or puzzle.get("puzzleId") or None
    num_moves = len(solutions)
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    title = f"Lichess Puzzle {pid}" if pid else f"Lichess Puzzle {today}"
    description = f"{today} | {num_moves}-move puzzle from Lichess.org"

    doc = {
        "sourceId": pid,
        "title": title,
        "description": description,
        "pgn": pgn,
        "fen": fen,
        # board is a mapping (square -> piece symbol) to avoid nested arrays
        "board": mapping,
        "firstMove": turn,
        "solutions": solutions,
        "hasSolutions": True if solutions else False,
        "createdBy": "lichess",
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    return doc


def upload_document(db, doc):
    try:
        ref = db.collection(FIRESTORE_COLLECTION).add(doc)
        print(f"[upload] Uploaded to Firestore: doc_ref = {ref[1].update_time if len(ref)>1 and hasattr(ref[1],'update_time') else ref[0].id}")
        return True
    except Exception as e:
        print("[upload] Upload failed:", e, file=sys.stderr)
        print("[upload] Document preview keys:", list(doc.keys()))
        try:
            # show small preview for debugging
            preview = doc.copy()
            preview["pgn"] = (preview.get("pgn") or "")[:200]
            preview["board_preview_count"] = len(preview.get("board", {}))
            preview["solutions_preview"] = preview.get("solutions", [])[:10]
            print(json.dumps(preview, indent=2, default=str))
        except Exception:
            pass
        return False


def main():
    try:
        db = init_firestore()
    except Exception as e:
        print("[fatal] Firebase init failed:", e, file=sys.stderr)
        traceback.print_exc()
        return 1

    # delete older puzzles (try, but don't fail run if it errors)
    try:
        delete_old_puzzles(db, DELETE_OLDER_THAN_DAYS)
    except Exception as e:
        print("[warn] delete_old_puzzles failed:", e)

    # fetch lichess puzzle
    try:
        lichess_data = fetch_lichess_daily()
    except Exception as e:
        print("[fatal] Failed to fetch Lichess daily puzzle:", e, file=sys.stderr)
        traceback.print_exc()
        return 2

    # build document
    try:
        doc = build_document_from_lichess(lichess_data)
    except Exception as e:
        print("[fatal] Failed to build puzzle document:", e, file=sys.stderr)
        traceback.print_exc()
        return 3

    # duplicate check
    if doc.get("sourceId"):
        if puzzle_exists(db, doc["sourceId"]):
            print(f"[skip] Puzzle with sourceId {doc['sourceId']} already exists. Skipping upload.")
            return 0

    ok = upload_document(db, doc)
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
