#!/usr/bin/env python3
import os
import sys
import json
import base64
import hashlib
import datetime
import textwrap

# ---- small helper to import-or-install a dependency on CI runners ----
def ensure(pkg_name, import_name=None, version_spec=None):
    import importlib, subprocess
    mod_name = import_name or pkg_name
    try:
        return importlib.import_module(mod_name)
    except ModuleNotFoundError:
        pin = f"{pkg_name}{version_spec or ''}"
        print(f"[setup] Installing {pin} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pin])
        return importlib.import_module(mod_name)

requests = ensure("requests")
chess    = ensure("python-chess", import_name="chess", version_spec=">=1.999")
firebase_admin = ensure("firebase-admin", version_spec=">=6.5")

from firebase_admin import credentials, firestore

# ---------------------------------------------------------------------
# 1) Read base64 Firebase credentials from env and init Firestore
# ---------------------------------------------------------------------
B64 = os.getenv("FIREBASE_CREDENTIALS")
if not B64:
    raise RuntimeError("FIREBASE_CREDENTIALS env var is missing (base64-encoded JSON)")

CREDS_PATH = "firebase_credentials.json"
with open(CREDS_PATH, "wb") as f:
    f.write(base64.b64decode(B64))

if not firebase_admin._apps:
    cred = credentials.Certificate(CREDS_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ---------------------------------------------------------------------
# 2) Pull a puzzle from Chess.com Published Data API
#     - daily:  https://api.chess.com/pub/puzzle
#     - random: https://api.chess.com/pub/puzzle/random
# ---------------------------------------------------------------------
USE_RANDOM = False  # set True if you prefer a random daily instead of today's daily

PUZZLE_URL = "https://api.chess.com/pub/puzzle" + ("/random" if USE_RANDOM else "")
resp = requests.get(PUZZLE_URL, timeout=20)
resp.raise_for_status()
payload = resp.json()

# Expected (Chess.com): title, url, publish_time, fen, pgn, image, (sometimes) solution
# We’ll be defensive if any field is missing.
fen = payload.get("fen")
pgn = payload.get("pgn")
title_from_api = payload.get("title")
publish_ts = payload.get("publish_time")  # epoch seconds
puzzle_url = payload.get("url")
image_url = payload.get("image")

if not fen or not pgn:
    raise RuntimeError("Puzzle API did not return both FEN and PGN; cannot build a solvable puzzle.")

# ---------------------------------------------------------------------
# 3) Use python-chess to derive useful, frontend-friendly data
#    - Validate FEN
#    - Extract SAN & UCI solution (mainline only; flat list)
# ---------------------------------------------------------------------
try:
    board = chess.Board(fen=fen)
except Exception as e:
    raise RuntimeError(f"Invalid FEN from API: {fen}\n{e}")

# Parse PGN. Chess.com gives a single-game PGN string that starts from the FEN.
# We only keep the main line (no variations) and flatten to SAN + UCI arrays.
def parse_pgn_to_moves(pgn_str, start_board):
    from io import StringIO
    pgn_io = StringIO(pgn_str)
    game = chess.pgn.read_game(pgn_io)
    if game is None:
        # fallback: split SAN tokens heuristically (very rare)
        return [], []
    node = game
    # If the PGN specifies a starting FEN in headers, python-chess will handle it,
    # but we’ll still trust the API FEN for start position shown in UI.
    tmp_board = start_board.copy()
    san_moves, uci_moves = []
    [], []
    san_moves = []
    uci_moves = []
    while node.variations:
        node = node.variations[0]
        san = tmp_board.san(node.move)
        san_moves.append(san)
        uci_moves.append(node.move.uci())
        tmp_board.push(node.move)
    return san_moves, uci_moves

solutions_san, solutions_uci = parse_pgn_to_moves(pgn, board)

if not solutions_san or not solutions_uci:
    # If no moves extracted, we still publish the FEN + PGN so UI can replay,
    # but add a warning to description.
    warn = True
else:
    warn = False

# ---------------------------------------------------------------------
# 4) Build a meaningful title and description
# ---------------------------------------------------------------------
side = "White" if board.turn == chess.WHITE else "Black"

# Try to guess mate-in-N from SAN sequence (look for trailing '#')
mate_in = None
for idx, san in enumerate(solutions_san, start=1):
    if "#" in san:
        mate_in = idx  # rough guess
        break

today_iso = datetime.date.today().isoformat()
pretty_title = title_from_api or f"Daily Puzzle • {today_iso}"
if mate_in:
    pretty_title = f"Mate in {mate_in} • {side} to move"

desc_parts = [
    f"Source: Chess.com {'Random' if USE_RANDOM else 'Daily'} Puzzle.",
    f"Date: {today_iso}.",
    f"Side to move: {side}.",
]
if puzzle_url:
    desc_parts.append(f"Link: {puzzle_url}")
if warn:
    desc_parts.append("Note: solution moves could not be fully extracted; PGN is provided.")

description = " ".join(desc_parts)

# ---------------------------------------------------------------------
# 5) Ensure Firestore-friendly, flat document (no nested arrays of arrays)
#    (Your earlier error came from nested arrays in a field — this avoids that.)
# ---------------------------------------------------------------------
# Deterministic ID (avoid duplicates when you re-run a workflow)
puzzle_id = hashlib.sha1(f"{fen}|{pgn}".encode("utf-8")).hexdigest()[:16]

doc = {
    "puzzleId": puzzle_id,
    "title": pretty_title,
    "description": description,
    "createdBy": "chess.com",
    "date": today_iso,
    "createdAt": firestore.SERVER_TIMESTAMP,

    # Board payload your UI can use:
    "board": {
        "startFen": fen,     # << explicit start FEN (UI can draw the initial position instantly)
        "pgn": pgn,          # << full PGN for replay if your UI supports it
    },

    # Flat arrays (NO nested arrays)
    "solutionsSan": solutions_san,   # ["Qh7+", "Kxh7", "Rh1+", ...]
    "solutionsUci": solutions_uci,   # ["h5h7", "g8h7", ...]
    "hasSolutions": bool(solutions_san),

    # Optional extras for your website (all scalar or flat)
    "image": image_url or "",
    "sourceUrl": puzzle_url or PUZZLE_URL,
    "tags": ["daily", "chess.com", f"{side}-to-move"] + ([f"mate-in-{mate_in}"] if mate_in else []),
}

# ---------------------------------------------------------------------
# 6) Upsert to Firestore ("puzzles" collection)
# ---------------------------------------------------------------------
ref = db.collection("puzzles").document(puzzle_id)
ref.set(doc)
print(f"✅ Uploaded puzzle {puzzle_id}: {pretty_title}")
