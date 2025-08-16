#!/usr/bin/env python3
"""
Uploads one chess puzzle to Firestore.

- Uses Lichess Daily puzzle JSON (stable & public).
- Converts UCI solution to SAN with python-chess.
- Builds a meaningful title & description from PGN headers.
- Stores a flat (non-nested) array of SAN moves to avoid
  Firestore's "nested arrays not supported" error.
- Works with base64-encoded Firebase credentials in the
  FIREBASE_CREDENTIALS secret (GitHub Actions).
"""

import base64
import io
import json
import os
import re
from datetime import datetime, timezone

import requests
import chess
import chess.pgn

import firebase_admin
from firebase_admin import credentials, firestore


# List of grandmasters for puzzle titles
GRANDMASTERS = [
    "Magnus Carlsen", "Garry Kasparov", "Bobby Fischer", "Anatoly Karpov",
    "Mikhail Tal", "Jose Raul Capablanca", "Paul Morphy", "Emanuel Lasker",
    "Viswanathan Anand", "Hikaru Nakamura", "Fabiano Caruana", "Wesley So",
    "Ding Liren", "Ian Nepomniachtchi", "Alireza Firouzja", "Levon Aronian"
]


# ---------- Firebase init (from base64 secret) ----------
def init_firebase_from_b64_env(env_key: str = "FIREBASE_CREDENTIALS") -> firestore.Client:
    """Initializes Firebase from a base64-encoded service account key."""
    b64 = os.getenv(env_key)
    if not b64:
        raise RuntimeError(f"{env_key} is not set")

    path = "firebase_credentials.json"
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))

    cred = credentials.Certificate(path)
    app = firebase_admin.initialize_app(cred)
    return firestore.client(app)


# ---------- Puzzle fetch & transform ----------
def fetch_lichess_daily() -> dict:
    """Fetches the daily puzzle from the Lichess API."""
    url = "https://lichess.org/api/puzzle/daily"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def pgn_headers(pgn_text: str) -> dict:
    """Parses PGN text to extract game headers."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return {}
    return {k: v for k, v in game.headers.items()}


def board_at_initial_fen(pgn_text: str, initial_ply: int) -> chess.Board:
    """Returns the board position just before the puzzle's first solution move."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return chess.Board()

    board = game.board()
    target = max(0, int(initial_ply) - 1)
    for i, mv in enumerate(game.mainline_moves()):
        if i >= target:
            break
        board.push(mv)
    return board


def uci_to_san_list(board: chess.Board, uci_moves: list[str]) -> list[str]:
    """Converts a list of UCI strings into a flat list of SAN strings."""
    san = []
    b = board.copy()
    for u in uci_moves:
        mv = chess.Move.from_uci(u)
        if b.is_legal(mv):
            san.append(b.san(mv))
            b.push(mv)
        elif b.is_pseudo_legal(mv):
            try:
                san.append(b.san(mv))
                b.push(mv)
            except Exception as e:
                print(f"Skipping pseudo-legal move: {e}")
                pass
        else:
            print(f"Skipping illegal move: {u}")
            break
    return san


def generate_puzzle_title(db: firestore.Client) -> str:
    """
    Generates a unique grandmaster name as a title by tracking used names in Firestore.
    """
    metadata_doc_ref = db.collection("metadata").document("grandmasters")
    doc = metadata_doc_ref.get()
    
    if doc.exists:
        data = doc.to_dict()
        last_index = data.get("last_index", -1)
    else:
        last_index = -1
    
    new_index = (last_index + 1) % len(GRANDMASTERS)
    title = GRANDMASTERS[new_index]
    
    metadata_doc_ref.set({"last_index": new_index}, merge=True)
    
    return title

def generate_puzzle_description(san_moves: list[str]) -> str:
    """
    Generates a descriptive puzzle text based on the solution moves.
    """
    if not san_moves:
        return "Find the best move to solve the puzzle."

    last_move = san_moves[-1]
    if last_move.endswith('#'):
        return f"Find the forced mate in {len(san_moves)} moves."
    elif last_move.endswith('+'):
        return "Find the winning move that puts the opponent in check."
    else:
        return "Find the best move to gain a decisive advantage."


def serialize_board_to_string(board: chess.Board) -> str:
    """Converts a python-chess board object to a 64-character string representation."""
    board_str = board.board_fen().replace('/', '')
    board_str = board_str.replace('1', ' ')
    board_str = board_str.replace('2', '  ')
    board_str = board_str.replace('3', '   ')
    board_str = board_str.replace('4', '    ')
    board_str = board_str.replace('5', '     ')
    board_str = board_str.replace('6', '      ')
    board_str = board_str.replace('7', '       ')
    board_str = board_str.replace('8', '        ')
    return board_str

def sanitize_title_for_doc_id(title: str) -> str:
    """Converts a string to a valid Firestore document ID."""
    sanitized = title.lower().replace(" ", "-")
    sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)
    return sanitized


# ---------- Main ----------
def main():
    # 1) Firebase
    db = init_firebase_from_b64_env()

    # 2) Fetch daily puzzle
    data = fetch_lichess_daily()

    pid = data["puzzle"]["id"]
    initial_ply = int(data["puzzle"]["initialPly"])
    pgn = data["game"]["pgn"]
    solution_uci = data["puzzle"]["solution"]

    # 3) Build board at puzzle start & derive SAN moves
    start_board = board_at_initial_fen(pgn, initial_ply)
    san_moves = uci_to_san_list(start_board, solution_uci)
    
    if len(san_moves) > 3:
        print(f"ℹ️ Skipping Lichess Daily #{pid}: Solution has {len(san_moves)} moves, which is more than the allowed 3.")
        return

    serialized_board = serialize_board_to_string(start_board)
    side = "w" if start_board.turn else "b"

    # 4) Generate title and description
    title = generate_puzzle_title(db)
    description = generate_puzzle_description(san_moves)

    # 5) Sanitize the title for document ID
    doc_id = sanitize_title_for_doc_id(title)

    # 6) Firestore document (for puzzles collection)
    puzzle_doc = {
        "puzzleId": pid,
        "title": title,
        "description": description,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "createdAt": firestore.SERVER_TIMESTAMP,
        "createdBy": "lichess",
        "hasSolutions": True,
        "firstMove": side,
        "board": serialized_board
    }

    # 7) Firestore document (for solutions collection)
    solution_doc = {
        "solutions": san_moves,
        "lastUpdated": firestore.SERVER_TIMESTAMP,
        "puzzleId": pid
    }

    # 8) Store documents with the new document ID
    puzzle_doc_ref = db.collection("puzzles").document(doc_id)
    puzzle_doc_ref.set(puzzle_doc)
    
    solution_doc_ref = db.collection("solutions").document(doc_id)
    solution_doc_ref.set(solution_doc)

    print(f"✅ Uploaded Lichess Daily #{pid} with {len(san_moves)} solution moves. Title: {title}")


if __name__ == "__main__":
    main()
