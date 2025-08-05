import os
import sys
import json
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

def log(msg):
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
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Delete puzzles older than 1 month
try:
    puzzle_ref = db.collection("puzzles")
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
                    created_date = datetime.datetime.fromisoformat(str(created)).date()
                except Exception as e:
                    log(f"Could not parse createdAt: {created}")
                    continue
            if created_date and created_date < cutoff_date:
                log(f"Deleting old puzzle: {doc.id} created at {created_date}")
                doc.reference.delete()
except Exception as e:
    log(f"Error during cleanup: {e}")

# Fetch puzzle from Lichess API
response = requests.get("https://lichess.org/api/puzzle/daily")
if response.status_code != 200:
    raise Exception(f"Failed to fetch puzzle from Lichess: {response.status_code} {response.text}")

lichess_data = response.json()
game = lichess_data['game']
puzzle = lichess_data['puzzle']

# Convert FEN to board format
fen = game["fen"].split()[0]
rows = fen.split("/")
board = {}
for i, row in enumerate(rows):
    board_row = []
    for ch in row:
        if ch.isdigit():
            board_row += [''] * int(ch)
        else:
            board_row.append(ch)
    board[i] = board_row

# Construct Firestore-compatible puzzle format
puzzle_data = {
    "title": puzzle.get("name", "Daily Puzzle"),
    "description": "Solve the puzzle from the given position.",
    "firstMove": "white" if puzzle.get("initialPly", 0) % 2 == 0 else "black",
    "board": board,
    "createdBy": "LichessAPI",
    "hasSolutions": True,
    "solutions": { "0": puzzle["solution"] },
    "createdAt": SERVER_TIMESTAMP
}

try:
    db.collection("puzzles").add(puzzle_data)
    log("Daily puzzle uploaded successfully!")
except Exception as e:
    log(f"Error uploading puzzle: {e}")
    sys.exit(1)