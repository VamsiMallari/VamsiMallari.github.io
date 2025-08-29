#!/usr/bin/env python3
"""
Uploads one chess puzzle to Firestore daily, cycling through different themes.

- Fetches puzzles from Lichess based on a sequence of themes (e.g., mate in 1, mate in 2).
- Ensures puzzle solutions are no longer than 3 full moves.
- Converts UCI solution to SAN using python-chess.
- Generates a meaningful title and description based on the puzzle theme.
- Stores a flat (non-nested) array of SAN moves.
- Works with base64-encoded Firebase credentials for use in GitHub Actions.
"""

import base64
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
import time

import requests
import chess
import chess.pgn

import firebase_admin
from firebase_admin import credentials, firestore

# Sequence of puzzle themes to cycle through
PUZZLE_THEMES = [
    "mateIn1",
    "mateIn2",
    "advantage",
    "mateIn3",
]

# Maximum number of retries to find a suitable puzzle
MAX_FETCH_ATTEMPTS = 10

# ---------- Firebase init (from base64 secret) ----------
def init_firebase_from_b64_env(env_key: str = "FIREBASE_CREDENTIALS") -> firestore.Client:
    """Initializes Firebase from a base64-encoded service account key."""
    if os.path.exists("firebase_credentials.json"):
        cred = credentials.Certificate("firebase_credentials.json")
    else:
        b64 = os.getenv(env_key)
        if not b64:
            raise RuntimeError(f"{env_key} is not set")

        path = "firebase_credentials.json"
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        cred = credentials.Certificate(path)

    if not firebase_admin._apps:
        app = firebase_admin.initialize_app(cred)
    else:
        app = firebase_admin.get_app()
    return firestore.client(app)


# ---------- Puzzle Fetch & Transform ----------
def get_next_puzzle_theme(db: firestore.Client) -> str:
    """Gets the next puzzle theme from the sequence by tracking the index in Firestore."""
    metadata_doc_ref = db.collection("metadata").document("puzzle_themes")
    doc = metadata_doc_ref.get()

    last_index = -1
    if doc.exists:
        data = doc.to_dict()
        last_index = data.get("last_theme_index", -1)

    new_index = (last_index + 1) % len(PUZZLE_THEMES)
    theme = PUZZLE_THEMES[new_index]

    metadata_doc_ref.set({"last_theme_index": new_index}, merge=True)

    print(f"Selected puzzle theme for today: {theme}")
    return theme

def fetch_puzzle_by_theme(theme: str) -> dict | None:
    """Fetches a random puzzle from Lichess API based on a given theme."""
    # Lichess API endpoint for puzzles by theme. We ask for 1 puzzle.
    url = f"https://lichess.org/api/puzzle/theme?theme={theme}&count=1"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching puzzle from Lichess API: {e}")
        return None

def uci_to_san_list(board: chess.Board, uci_moves: list[str]) -> list[str]:
    """Converts a list of UCI strings into a flat list of SAN strings."""
    san = []
    b = board.copy()
    for u in uci_moves:
        try:
            mv = chess.Move.from_uci(u)
            if b.is_legal(mv):
                san.append(b.san(mv))
                b.push(mv)
            else:
                print(f"Skipping illegal move: {u}")
                break
        except Exception as e:
            print(f"Error processing UCI move {u}: {e}")
            break
    return san

def generate_puzzle_title_and_description(theme: str, san_moves: list[str]) -> tuple[str, str]:
    """Generates a title and description based on the puzzle theme and solution."""
    num_moves = (len(san_moves) + 1) // 2
    
    if "mateIn" in theme:
        mate_in_num = int(theme.replace("mateIn", ""))
        title = f"Mate in {mate_in_num}"
        description = f"Find the forced checkmate in {mate_in_num} moves."
    elif theme == "advantage":
        title = f"Find the Best Move"
        description = f"Find the best sequence of moves to gain a decisive advantage."
    else:
        title = "Daily Chess Puzzle"
        description = "Find the best move to solve the puzzle."

    return title, description

def serialize_board_to_string(board: chess.Board) -> str:
    """Converts a python-chess board object to a 64-character string representation."""
    board_str = board.board_fen().replace('/', '')
    # Expand empty squares from numbers to spaces
    for i in range(8, 0, -1):
        board_str = board_str.replace(str(i), ' ' * i)
    return board_str

def sanitize_title_for_doc_id(title: str) -> str:
    """Converts a string to a valid Firestore document ID."""
    sanitized = title.lower().replace(" ", "-")
    sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)
    # Add a timestamp to ensure uniqueness
    sanitized = f"{sanitized}-{int(time.time())}"
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
        
        # Delete puzzle document
        puzzle_doc.reference.delete()
        
        # Delete corresponding solution document
        solution_doc_ref = db.collection("solutions").document(puzzle_id)
        solution_doc_ref.delete()

        # Delete corresponding results document
        results_doc_ref = db.collection("results").document(puzzle_id)
        results_doc_ref.delete()
        
        deleted_count += 1
        print(f"🗑️ Deleted old puzzle with ID: {puzzle_id}")
    
    if deleted_count > 0:
        print(f"✅ Finished deleting {deleted_count} old puzzles.")
    else:
        print("No old puzzles to delete.")


# ---------- Main ----------
def main():
    # 1) Initialize Firebase
    db = init_firebase_from_b64_env()

    # 2) Delete old puzzles first
    delete_old_puzzles(db)

    # 3) Get the next puzzle theme
    theme = get_next_puzzle_theme(db)

    # 4) Fetch a suitable puzzle
    puzzle_data = None
    for attempt in range(MAX_FETCH_ATTEMPTS):
        print(f"Attempt {attempt + 1}/{MAX_FETCH_ATTEMPTS}: Fetching puzzle with theme '{theme}'...")
        data = fetch_puzzle_by_theme(theme)
        if data:
            game = chess.pgn.read_game(io.StringIO(data["game"]["pgn"]))
            solution_uci = data["puzzle"]["solution"]
            
            # A "move" is one white and one black action. Max 3 moves = 6 half-moves (ply)
            if len(solution_uci) <= 6:
                puzzle_data = data
                print(f"Found suitable puzzle #{data['puzzle']['id']} with {len(solution_uci)} solution moves.")
                break
            else:
                print(f"ℹ️ Skipping Lichess puzzle #{data['puzzle']['id']}: Solution has {len(solution_uci)} moves, which is more than the allowed 6.")
        time.sleep(2) # Wait a bit before retrying

    if not puzzle_data:
        print("❌ Could not find a suitable puzzle after multiple attempts. Exiting.")
        return

    # 5) Process the puzzle data
    pid = puzzle_data["puzzle"]["id"]
    pgn_text = puzzle_data["game"]["pgn"]
    solution_uci = puzzle_data["puzzle"]["solution"]

    game = chess.pgn.read_game(io.StringIO(pgn_text))
    board = game.board()
    for move in game.mainline_moves():
        board.push(move)
    
    # The board is now at the position where the puzzle starts
    # We need to go back one move to get the board state before the puzzle's first move
    board.pop() 
    
    san_moves = uci_to_san_list(board.copy(), solution_uci)

    serialized_board = serialize_board_to_string(board)
    side_to_move = "white" if board.turn == chess.WHITE else "black"

    # 6) Generate title and description
    title, description = generate_puzzle_title_and_description(theme, san_moves)

    # 7) Sanitize the title for document ID
    doc_id = sanitize_title_for_doc_id(title)

    # 8) Create Firestore document for the puzzle
    puzzle_doc = {
        "puzzleId": pid,
        "title": title,
        "description": description,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "createdAt": firestore.SERVER_TIMESTAMP,
        "createdBy": "Lichess",
        "hasSolutions": True,
        "firstMove": side_to_move,
        "board": serialized_board
    }

    # 9) Create Firestore document for the solution
    solution_doc = {
        "solutions": [san_moves], # Storing as an array of arrays to match your app's data structure
        "lastUpdated": firestore.SERVER_TIMESTAMP,
        "puzzleId": pid
    }

    # 10) Store documents in Firestore
    try:
        puzzle_doc_ref = db.collection("puzzles").document(doc_id)
        puzzle_doc_ref.set(puzzle_doc)
        
        solution_doc_ref = db.collection("solutions").document(doc_id)
        solution_doc_ref.set(solution_doc)

        print(f"✅ Successfully uploaded puzzle #{pid} with title: '{title}' and doc_id: '{doc_id}'")
    except Exception as e:
        print(f"❌ Error uploading puzzle to Firestore: {e}")


if __name__ == "__main__":
    main()
