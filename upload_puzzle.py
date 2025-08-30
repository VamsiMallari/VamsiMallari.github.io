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
from datetime import datetime, timedelta, timezone

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

    # Initialize the app if it hasn't been initialized yet
    if not firebase_admin._apps:
        cred = credentials.Certificate(path)
        firebase_admin.initialize_app(cred)
        
    return firestore.client()


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
    # We need to go back one move from the start of the puzzle to get the correct position
    target = max(0, int(initial_ply) - 1)
    
    # Iterate through moves to set up the board state
    node = game
    for _ in range(target):
        node = node.next()
    
    return node.board()


def uci_to_san_list(board: chess.Board, uci_moves: list[str]) -> list[str]:
    """Converts a list of UCI strings into a flat list of SAN strings."""
    san = []
    b = board.copy()
    for u in uci_moves:
        mv = chess.Move.from_uci(u)
        if b.is_legal(mv):
            san.append(b.san(mv))
            b.push(mv)
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

    num_moves = (len(san_moves) + 1) // 2
    last_move = san_moves[-1]
    if last_move.endswith('#'):
        return f"Find the forced mate in {num_moves} moves."
    else:
        return f"Find the best move to gain a decisive advantage."


def serialize_board_to_string(board: chess.Board) -> str:
    """Converts a python-chess board object to a 64-character string representation."""
    board_str = board.board_fen().replace('/', '')
    for i in range(8, 0, -1):
        board_str = board_str.replace(str(i), ' ' * i)
    return board_str

def sanitize_title_for_doc_id(title: str) -> str:
    """Converts a string to a valid Firestore document ID."""
    sanitized = title.lower().replace(" ", "-")
    sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)
    return sanitized

def delete_old_puzzles(db: firestore.Client):
    """Deletes puzzles and their solutions that are older than one month."""
    one_month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    puzzles_collection = db.collection("puzzles")
    old_puzzles_query = puzzles_collection.where("createdAt", "<", one_month_ago)
    
    old_puzzles = old_puzzles_query.stream()
    
    deleted_count = 0
    for puzzle_doc in old_puzzles:
        puzzle_id = puzzle_doc.id
        puzzle_doc.reference.delete()
        db.collection("solutions").document(puzzle_id).delete()
        db.collection("results").document(puzzle_id).delete()
        deleted_count += 1
        print(f"🗑️ Deleted old puzzle with ID: {puzzle_id}")
    
    print(f"✅ Finished deleting {deleted_count} old puzzles.")


# ---------- Main ----------
def main():
    db = init_firebase_from_b64_env()
    delete_old_puzzles(db)

    data = fetch_lichess_daily()

    pid = data["puzzle"]["id"]
    initial_ply = int(data["puzzle"]["initialPly"])
    pgn = data["game"]["pgn"]
    solution_uci = data["puzzle"]["solution"]

    start_board = board_at_initial_fen(pgn, initial_ply)
    san_moves = uci_to_san_list(start_board, solution_uci)
    
    # We only want short puzzles
    if len(san_moves) > 6:
        print(f"ℹ️ Skipping Lichess Daily #{pid}: Solution has {len(san_moves)} moves, which is more than the allowed 6.")
        return

    serialized_board = serialize_board_to_string(start_board)
    side = "white" if start_board.turn == chess.WHITE else "black"

    title = generate_puzzle_title(db)
    description = generate_puzzle_description(san_moves)
    doc_id = sanitize_title_for_doc_id(title)

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

    #
    # --- THIS IS THE CORRECTED PART ---
    # The 'solutions' field now correctly wraps the 'san_moves' list in another list
    # to match the data structure your web application expects.
    #
    solution_doc = {
        "solutions": [san_moves],
        "lastUpdated": firestore.SERVER_TIMESTAMP,
        "puzzleId": pid
    }

    # Store both documents
    puzzle_doc_ref = db.collection("puzzles").document(doc_id)
    puzzle_doc_ref.set(puzzle_doc)
    
    solution_doc_ref = db.collection("solutions").document(doc_id)
    solution_doc_ref.set(solution_doc)

    print(f"✅ Uploaded Lichess Daily #{pid} with {len(san_moves)} solution moves. Title: {title}")


if __name__ == "__main__":
    main()
