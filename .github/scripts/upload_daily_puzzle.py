import os
import json
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# Setup Firebase
creds = json.loads(os.environ.get("FIREBASE_CREDENTIALS", "{}"))
cred = credentials.Certificate(creds)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# Delete puzzles older than 1 month
puzzle_ref = db.collection("puzzles")
docs = puzzle_ref.stream()
cutoff_date = datetime.datetime.now().date() - datetime.timedelta(days=30)

for doc in docs:
    data = doc.to_dict()
    created = data.get("createdAt")
    if created:
        if hasattr(created, 'date'):
            created_date = created.date()
        else:
            try:
                created_date = datetime.datetime.fromisoformat(str(created)).date()
            except Exception as e:
                print(f"Could not parse date for doc {doc.id}: {e}")
                continue
        if created_date < cutoff_date:
            doc.reference.delete()

# Fetch puzzle from Lichess API
response = requests.get("https://lichess.org/api/puzzle/daily")
if response.status_code != 200:
    raise Exception(f"Failed to fetch puzzle from Lichess: {response.status_code} {response.text}")

lichess_data = response.json()
game = lichess_data.get("game", {})
puzzle = lichess_data.get("puzzle", {})

# Check for 'fen' key
fen = game.get("fen")
if not fen:
    print("Error: 'fen' key missing in the 'game' data from Lichess API. Skipping upload.")
    exit(1)

# Convert FEN to board format
rows = fen.split("/")[0:8]
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
    "solutions": { "0": puzzle.get("solution", []) },
    "createdAt": firestore.SERVER_TIMESTAMP
}

db.collection("puzzles").add(puzzle_data)
print("Daily puzzle uploaded successfully!")
