import os
import sys
import json
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

def log(msg):
    """Helper function for logging messages."""
    print(f"[DailyPuzzleUploader] {msg}")

# Check for credentials file argument
if len(sys.argv) != 2:
    log("Usage: python upload_daily_puzzle.py <credentials_file>")
    sys.exit(1)

cred_path = sys.argv[1]
if not os.path.isfile(cred_path):
    log(f"Credentials file not found: {cred_path}")
    sys.exit(1)

# Setup Firebase
try:
    cred = credentials.Certificate(cred_path)
    # Initialize Firebase app
    app = firebase_admin.initialize_app(cred)
    db = firestore.client()
    # Get the project ID from the initialized app
    project_id = app.project_id
    log(f"Firebase initialized for project: {project_id}")
except Exception as e:
    log(f"Error setting up Firebase: {e}")
    sys.exit(1)

# Define the full Firestore collection path for public puzzles
# This matches the client-side path: artifacts/{appId}/public/data/puzzles
PUZZLES_COLLECTION_PATH = f"artifacts/{project_id}/public/data/puzzles"
log(f"Targeting Firestore collection: {PUZZLES_COLLECTION_PATH}")

# Delete puzzles older than 1 month
try:
    # Use the full collection path for cleanup
    puzzle_ref = db.collection(PUZZLES_COLLECTION_PATH)
    docs = puzzle_ref.stream()
    cutoff_date = datetime.datetime.utcnow().date() - datetime.timedelta(days=30)

    for doc in docs:
        data = doc.to_dict()
        created = data.get("createdAt")
        created_date = None
        if created:
            if hasattr(created, 'date'):
                created_date = created.date()
            else:
                try:
                    # Attempt to parse ISO format string if it's not a Firestore Timestamp
                    created_date = datetime.datetime.fromisoformat(str(created).replace('Z', '+00:00')).date()
                except Exception as e:
                    log(f"Could not parse createdAt: {created} - {e}")
                    continue
        
        if created_date and created_date < cutoff_date:
            log(f"Deleting old puzzle: {doc.id} created at {created_date}")
            doc.reference.delete()
except Exception as e:
    log(f"Error during cleanup: {e}")

# Fetch puzzle from Lichess API
lichess_data = None
try:
    response = requests.get("https://lichess.org/api/puzzle/daily")
    response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
    lichess_data = response.json()
    log("Successfully fetched daily puzzle from Lichess API.")
except requests.exceptions.RequestException as e:
    log(f"Failed to fetch puzzle from Lichess: {e}")
    sys.exit(1)
except json.JSONDecodeError as e:
    log(f"Failed to decode Lichess API response as JSON: {e}")
    sys.exit(1)

# Check if 'game' and 'fen' keys exist in the Lichess data
if not lichess_data or 'game' not in lichess_data or 'fen' not in lichess_data['game']:
    log("Error: Lichess API response is missing 'game' or 'fen' key. Skipping puzzle upload.")
    sys.exit(0) # Exit successfully if no puzzle data is available

game = lichess_data['game']
puzzle = lichess_data['puzzle']

# Convert FEN to board format (list of lists)
fen = game["fen"].split()[0]
rows = fen.split("/")
board = [] # Initialize as a list
for row_str in rows:
    board_row = []
    for ch in row_str:
        if ch.isdigit():
            board_row.extend([''] * int(ch)) # Use extend for multiple empty squares
        else:
            board_row.append(ch)
    board.append(board_row) # Append the row list to the board list

# Construct Firestore-compatible puzzle format
# Assuming puzzle["solution"] is already a list of moves from Lichess API
puzzle_data = {
    "title": puzzle.get("name", "Daily Puzzle"),
    "description": "Solve the puzzle from the given position.",
    "firstMove": "white" if puzzle.get("initialPly", 0) % 2 == 0 else "black",
    "board": board, # Now a list of lists
    "createdBy": "LichessAPI",
    "hasSolutions": True,
    "solutions": puzzle["solution"], # Directly use the list from Lichess
    "createdAt": SERVER_TIMESTAMP # Use Firestore server timestamp
}

# Upload puzzle to Firestore
try:
    # Use the full collection path for adding the document
    db.collection(PUZZLES_COLLECTION_PATH).add(puzzle_data)
    log("Daily puzzle uploaded successfully!")
except Exception as e:
    log(f"Error uploading puzzle: {e}")
    sys.exit(1)
