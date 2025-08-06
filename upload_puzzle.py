import requests
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Initialize Firebase Admin SDK
cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Get the daily puzzle from Lichess
response = requests.get("https://lichess.org/api/puzzle/daily")
puzzle_data = response.json()

# Structure the puzzle data
puzzle_doc = {
    "title": puzzle_data['puzzle']['id'],
    "description": f"Daily puzzle from Lichess ({datetime.utcnow().isoformat()})",
    "firstMove": puzzle_data['puzzle']['initialPly'],
    "board": {
        "fen": puzzle_data['game']['fen'],
    },
    "createdBy": "lichess",
    "hasSolutions": True,
    "solutions": puzzle_data['puzzle']['solution'],
    "createdAt": firestore.SERVER_TIMESTAMP
}

# Upload to Firestore
db.collection("puzzles").add(puzzle_doc)

print("Puzzle uploaded successfully.")
