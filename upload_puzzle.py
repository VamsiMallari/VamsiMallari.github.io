#!/usr/bin/env python3
"""
Uploads a daily chess puzzle to Firestore with rotating themes and grandmaster titles.

- Fetches puzzles from a reliable Lichess API endpoint by rating to ensure consistency.
- Uses a rotating list of grandmaster names for puzzle titles.
- Intelligently determines the puzzle's theme (e.g., mate in X) by analyzing the solution.
- Ensures puzzle solutions are no longer than 3 full moves (6 half-moves).
- Deletes puzzles and solutions older than 30 days.
- Works with base64-encoded Firebase credentials for use in GitHub Actions.
"""

import base64
import io
import os
import re
from datetime import datetime, timedelta, timezone
import time

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

# Maximum number of retries to find a suitable puzzle
MAX_FETCH_ATTEMPTS = 15
# Puzzle rating range - puzzles in this range are more likely to have short solutions
PUZZLE_RATING_MIN = 1200
PUZZLE_RATING_MAX = 1600


# ---------- Firebase Initialization ----------
def init_firebase_from_b64_env(env_key: str = "FIREBASE_CREDENTIALS") -> firestore.Client:
    """Initializes Firebase from a base64-encoded service account key."""
    try:
        if os.path.exists("firebase_credentials.json"):
            cred = credentials.Certificate("firebase_credentials.json")
        else:
            b64 = os.getenv(env_key)
            if not b64:
                raise RuntimeError(f"The {env_key} environment variable is not set.")

            path = "firebase_credentials.json"
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64))
            cred = credentials.Certificate(path)

        if not firebase_admin._apps:
            app = firebase_admin.initialize_app(cred)
        else:
            app = firebase_admin.get_app()
        return firestore.client(app)
    except Exception as e:
        print(f"❌ Firebase initialization failed: {e}")
        raise


# ---------- Puzzle Fetching and Processing ----------
def fetch_puzzle_by_rating() -> dict | None:
    """Fetches a random puzzle from the Lichess API within a specified rating range."""
    url = f"https://lichess.org/api/puzzle/rated?lowerBound={PUZZLE_RATING_MIN}&upperBound={PUZZLE_RATING_MAX}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Error fetching puzzle from Lichess API: {e}")
        return None

def uci_to_san_list(board: chess.Board, uci_moves: list[str]) -> list[str]:
    """Converts a list of UCI moves to a list of SAN moves."""
    san_moves = []
    temp_board = board.copy()
    for uci in uci_moves:
        try:
            move = chess.Move.from_uci(uci)
            if temp_board.is_legal(move):
                san_moves.append(temp_board.san(move))
                temp_board.push(move)
            else:
                print(f"⚠️ Skipping illegal move in solution: {uci}")
                return []
        except Exception as e:
            print(f"⚠️ Error processing UCI move '{uci}': {e}")
            return []
    return san_moves

def generate_puzzle_title(db: firestore.Client) -> str:
    """Gets the next grandmaster name from the rotating list in Firestore."""
    metadata_ref = db.collection("metadata").document("grandmasters")
    doc = metadata_ref.get()
    last_index = doc.to_dict().get("last_index", -1) if doc.exists else -1
    new_index = (last_index + 1) % len(GRANDMASTERS)
    title = GRANDMASTERS[new_index]
    metadata_ref.set({"last_index": new_index}, merge=True)
    return title

def generate_puzzle_description(san_moves: list[str]) -> str:
    """Generates a dynamic puzzle description by analyzing the solution."""
    if not san_moves:
        return "Find the best move to solve the puzzle."

    num_moves = (len(san_moves) + 1) // 2
    last_move = san_moves[-1]

    if last_move.endswith('#'):
        return f"Find the forced mate in {num_moves} moves."
    else:
        return f"Find the best move to gain a decisive advantage."

def serialize_board_to_string(board: chess.Board) -> str:
    """Converts a python-chess board object to a 64-character string for Firestore."""
    board_str = board.board_fen().replace('/', '')
    for i in range(8, 0, -1):
        board_str = board_str.replace(str(i), ' ' * i)
    return board_str

def sanitize_title_for_doc_id(title: str) -> str:
    """Creates a URL-friendly and unique document ID from a title."""
    sanitized = title.lower().replace(" ", "-")
    sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)
    return f"{sanitized}-{int(time.time())}"

def delete_old_puzzles(db: firestore.Client):
    """Deletes puzzles, solutions, and results older than 30 days."""
    print("🗑️ Checking for old puzzles to delete...")
    one_month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    puzzles_query = db.collection("puzzles").where("createdAt", "<", one_month_ago)
    old_puzzles = list(puzzles_query.stream())
    
    if not old_puzzles:
        print("✅ No old puzzles found.")
        return

    deleted_count = 0
    for puzzle_doc in old_puzzles:
        puzzle_id = puzzle_doc.id
        puzzle_doc.reference.delete()
        db.collection("solutions").document(puzzle_id).delete()
        db.collection("results").document(puzzle_id).delete()
        deleted_count += 1
        print(f"   - Deleted old puzzle with ID: {puzzle_id}")
    
    print(f"✅ Finished deleting {deleted_count} old puzzle(s).")


# ---------- Main Execution ----------
def main():
    """Main function to fetch, process, and upload a daily chess puzzle."""
    try:
        db = init_firebase_from_b64_env()
        delete_old_puzzles(db)

        puzzle_data = None
        print("\n🔍 Searching for a suitable puzzle...")
        for attempt in range(MAX_FETCH_ATTEMPTS):
            print(f"   Attempt {attempt + 1}/{MAX_FETCH_ATTEMPTS}...")
            data = fetch_puzzle_by_rating()
            if data and 'puzzle' in data and 'solution' in data['puzzle']:
                solution_uci = data["puzzle"]["solution"]
                # A "move" is one white and one black action. Max 3 moves = 6 half-moves (ply).
                if 1 < len(solution_uci) <= 6:
                    puzzle_data = data
                    print(f"   ✅ Found suitable puzzle #{data['puzzle']['id']} with {len(solution_uci)} half-moves.")
                    break
                else:
                    print(f"   ℹ️ Skipping puzzle #{data['puzzle']['id']}: Solution length ({len(solution_uci)}) is not within the desired range (2-6).")
            time.sleep(2) # Be respectful to the API

        if not puzzle_data:
            print("\n❌ Could not find a suitable puzzle after multiple attempts. Exiting.")
            return

        print("\n⚙️ Processing puzzle...")
        pid = puzzle_data["puzzle"]["id"]
        pgn_text = puzzle_data["game"]["pgn"]
        solution_uci = puzzle_data["puzzle"]["solution"]

        game = chess.pgn.read_game(io.StringIO(pgn_text))
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
        
        # The board is at the position where the puzzle starts.
        # We need the FEN of the board *before* the first move of the solution.
        board.pop() 
        
        san_moves = uci_to_san_list(board.copy(), solution_uci)
        if not san_moves:
             print("\n❌ Failed to convert UCI to SAN. Aborting upload.")
             return

        serialized_board = serialize_board_to_string(board)
        side_to_move = "white" if board.turn == chess.WHITE else "black"

        title = generate_puzzle_title(db)
        description = generate_puzzle_description(san_moves)
        doc_id = sanitize_title_for_doc_id(title)

        print(f"   - Title: {title}")
        print(f"   - Description: {description}")

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

        solution_doc = {
            "solutions": [san_moves], # Stored as an array of arrays for consistency with your app
            "lastUpdated": firestore.SERVER_TIMESTAMP,
            "puzzleId": pid
        }

        print("\n☁️ Uploading puzzle to Firestore...")
        db.collection("puzzles").document(doc_id).set(puzzle_doc)
        db.collection("solutions").document(doc_id).set(solution_doc)

        print(f"✅ Successfully uploaded puzzle with doc_id: '{doc_id}'")

    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
