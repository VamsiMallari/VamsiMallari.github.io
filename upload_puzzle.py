#!/usr/bin/env python3
"""
upload_puzzle.py

Fetch a puzzle from Lichess, convert to a Firestore-safe format, and upload.
Designed to use FIREBASE_CREDENTIALS (base64-encoded JSON) from env.

Dependencies:
  pip install firebase-admin requests python-chess

Environment:
  FIREBASE_CREDENTIALS    - base64-encoded Firebase service account JSON

Behavior:
  - Fetches Lichess daily puzzle (https://lichess.org/api/puzzle/daily)
  - Extracts PGN and puzzle.initialPly, uses python-chess to compute FEN at puzzle position
  - Builds board mapping (square -> piece symbol) and a document with metadata
  - Checks Firestore for duplicate using sourceId (lichess puzzle id)
  - Uploads the puzzle document to Firestore collection "puzzles"
"""

import os
import base64
import json
import tempfile
import requests
import sys
import traceback
from datetime import datetime, timezone

try:
    import chess
    import chess.pgn
except Exception as e:
    print("Missing python-chess. Install with: pip install python-chess", file=sys.stderr)
    raise

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except Exception as e:
    print("Missing firebase-admin. Install with: pip install firebase-admin", file=sys.stderr)
    raise

LICHESS_DAILY_URL = "https://lichess.org/api/puzzle/daily"
FIRESTORE_COLLECTION = "puzzles"


def decode_firebase_creds_from_env(env_var="FIREBASE_CREDENTIALS"):
    b64 = os.getenv(env_var)
    if not b64:
        raise RuntimeError(f"Environment variable {env_var} not set")
    try:
        raw = base64.b64decode(b64)
    except Exception as e:
        raise RuntimeError(f"Failed to base64-decode {env_var}: {e}")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        # If decode fails, write raw to file and try reading as JSON
        raise RuntimeError(f"Decoded {env_var} is not valid JSON: {e}")
    return obj, raw


def init_firebase(creds_json_bytes):
    # Write to temp file since credentials.Certificate expects a path or dict; using a temp file is robust.
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tf.write(creds_json_bytes)
    tf.flush()
    tf.close()
    cred = credentials.Certificate(tf.name)
    try:
        firebase_admin.initialize_app(cred)
    except ValueError:
        # Already initialized in this process - OK
        pass
    db = firestore.client()
    return db


def fetch_lichess_daily():
    resp = requests.get(LICHESS_DAILY_URL, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    # Basic validation
    if "puzzle" not in data or "game" not in data:
        raise RuntimeError("Unexpected Lichess response structure (missing 'puzzle' or 'game').")
    return data


def pgn_to_position_and_fen(pgn_text, initial_ply):
    """
    Returns (fen, board_mapping, turn) where:
      - fen is FEN string of the puzzle position
      - board_mapping is dict: square_name -> piece symbol ('P','p','K','k', etc.)
      - turn is 'white' or 'black' (who moves next)
    initial_ply is number of half-moves to play from the start to reach the puzzle position.
    """
    # Parse PGN
    pgn_io = chess.pgn.StringIO(pgn_text)
    game = chess.pgn.read_game(pgn_io)
    if game is None:
        # fallback: try to parse moves by creating a chess.Board and playing SAN moves if PGN is just moves
        board = chess.Board()
        # Try simple SAN parsing from pgn_text (last resort)
        tokens = pgn_text.strip().split()
        ply = 0
        for tok in tokens:
            try:
                board.push_san(tok)
                ply += 1
            except Exception:
                # skip token if can't parse
                continue
        # Now we have board after attempting all moves; return its FEN and mapping
        return board.fen(), board_to_mapping(board), ('white' if board.turn == chess.WHITE else 'black')

    # Walk moves up to initial_ply
    board = game.board()
    moves = list(game.mainline_moves())
    # initial_ply might be given as an integer equal to half-move count
    n = int(initial_ply) if initial_ply is not None else 0
    n = max(0, min(n, len(moves)))
    for m in moves[:n]:
        board.push(m)

    return board.fen(), board_to_mapping(board), ('white' if board.turn == chess.WHITE else 'black')


def board_to_mapping(board):
    """
    Convert a python-chess Board into a dict mapping square_name -> piece symbol.
    White pieces uppercase letters, black are lowercase single letter types:
      chess.Piece.symbol() returns 'P', 'p', 'k', ...
    We'll return piece.symbol() with uppercase for white, lowercase for black (python-chess does this).
    """
    mapping = {}
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece:
            mapping[chess.square_name(sq)] = piece.symbol()  # 'P','p','K',...
    return mapping


def normalize_solutions(sol):
    """
    Ensure solutions is a flat list of strings suitable for Firestore.
    Accepts a list (possibly nested) or string.
    """
    if sol is None:
        return []
    if isinstance(sol, str):
        return [sol]
    if isinstance(sol, (list, tuple)):
        # Flatten one level if nested arrays present
        flat = []
        for item in sol:
            if isinstance(item, (list, tuple)):
                for x in item:
                    flat.append(str(x))
            else:
                flat.append(str(item))
        return flat
    return [str(sol)]


def puzzle_exists(db, source_id):
    q = db.collection(FIRESTORE_COLLECTION).where("sourceId", "==", source_id).limit(1).get()
    return len(q) > 0


def upload_puzzle(db, doc):
    col = db.collection(FIRESTORE_COLLECTION)
    added = col.add(doc)
    return added


def build_puzzle_document(lichess_data):
    """
    Build Firestore document from the Lichess puzzle JSON.
    """
    puzzle_info = lichess_data.get("puzzle", {})
    game_info = lichess_data.get("game", {})

    pgn = game_info.get("pgn", "")
    initial_ply = puzzle_info.get("initialPly", 0)

    # Derive FEN and board mapping
    fen, board_map, turn = pgn_to_position_and_fen(pgn, initial_ply)

    # Solutions
    solutions = normalize_solutions(puzzle_info.get("solution") or puzzle_info.get("solutions") or [])

    # Title, description
    pid = puzzle_info.get("id") or puzzle_info.get("puzzleId") or None
    num_moves = len(solutions)
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    title = f"Lichess Puzzle {pid}" if pid else f"Lichess Puzzle {today}"
    description = f"{today} | {num_moves}-move puzzle from Lichess"

    doc = {
        "sourceId": pid,
        "title": title,
        "description": description,
        "pgn": pgn,
        "fen": fen,
        # store board as mapping (no nested arrays)
        "board": board_map,
        "firstMove": turn,
        "solutions": solutions,
        "hasSolutions": True if len(solutions) > 0 else False,
        "createdBy": "lichess",
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    return doc


def main():
    try:
        creds_obj, creds_raw = decode_firebase_creds_from_env()
    except Exception as e:
        print("Firebase credentials error:", e, file=sys.stderr)
        return 1

    try:
        db = init_firebase(creds_raw)
    except Exception as e:
        print("Failed to initialize Firebase:", e, file=sys.stderr)
        traceback.print_exc()
        return 2

    # Fetch puzzle(s)
    try:
        lichess_data = fetch_lichess_daily()
    except Exception as e:
        print("Failed to fetch Lichess puzzle:", e, file=sys.stderr)
        traceback.print_exc()
        return 3

    # Build document
    try:
        doc = build_puzzle_document(lichess_data)
    except Exception as e:
        print("Failed to build puzzle document:", e, file=sys.stderr)
        traceback.print_exc()
        return 4

    # Duplicate check
    if doc.get("sourceId"):
        try:
            if puzzle_exists(db, doc["sourceId"]):
                print("Puzzle already exists in Firestore (sourceId:", doc["sourceId"], "). Skipping upload.")
                return 0
        except Exception as e:
            print("Warning: failed duplicate check; continuing to upload. Error:", e, file=sys.stderr)

    # Upload
    try:
        res = upload_puzzle(db, doc)
        print("Uploaded puzzle successfully. Firestore response:", res)
    except Exception as e:
        print("Failed to upload puzzle to Firestore:", e, file=sys.stderr)
        traceback.print_exc()
        # In case of Firestore nested-arrays or other invalid data, print doc for debugging
        try:
            print("Document payload preview (keys):", list(doc.keys()))
            # remove large fields
            preview = doc.copy()
            preview["pgn"] = preview["pgn"][:200] + "..." if preview.get("pgn") and len(preview["pgn"]) > 200 else preview.get("pgn")
            preview["board_preview_count"] = len(preview.get("board", {}))
            preview["solutions_preview"] = preview.get("solutions", [])[:10]
            print(json.dumps(preview, indent=2, default=str))
        except Exception:
            pass
        return 5

    return 0


if __name__ == "__main__":
    sys.exit(main())
