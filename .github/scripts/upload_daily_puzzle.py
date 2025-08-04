import os
import json
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# Setup Firebase
creds = json.loads(os.environ["FIREBASE_CREDENTIALS"])
cred = credentials.Certificate(creds)
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
            except:
                continue
        if created_date < cutoff_date:
            doc.reference.delete()

# Fetch puzzle from Lichess API
response = requests.get("https://lichess.org/api/puzzle/daily")
if response.status_code != 200:
    raise Exception("Failed to fetch puzzle from Lichess")

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
    "createdAt": firestore.SERVER_TIMESTAMP
}

db.collection("puzzles").add(puzzle_data)
print("Daily puzzle uploaded successfully!")
