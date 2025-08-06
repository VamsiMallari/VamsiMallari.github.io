import os
import base64
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Get base64-encoded Firebase credentials
b64_cred = os.getenv("FIREBASE_CREDENTIALS")
if not b64_cred:
    raise ValueError("FIREBASE_CREDENTIALS is not set!")

# Decode and save as JSON file
with open("firebase_credentials.json", "wb") as f:
    f.write(base64.b64decode(b64_cred))

# Initialize Firebase
cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Get the daily puzzle from Lichess
res = requests.get("https://lichess.org/api/puzzle/daily")
res.raise_for_status()
data = res.json()

# Extract useful data
puzzle_id = data["puzzle"]["id"]
solution = data["puzzle"]["solution"]
num_moves = len(solution)
today = datetime.utcnow().strftime("%B %d, %Y")

# Construct document with improved title & description
puzzle_doc = {
    "title": f"Lichess Puzzle {puzzle_id}",
    "description": f"{today} | {num_moves}-move chess puzzle from Lichess.org",
    "firstMove": data["puzzle"]["initialPly"],
    "board": {
        "pgn": data["game"]["pgn"],
    },
    "createdBy": "lichess",
    "hasSolutions": True,
    "solutions": solution,
    "createdAt": firestore.SERVER_TIMESTAMP
}

# Upload to Firestore
db.collection("puzzles").add(puzzle_doc)
print(f"✅ Uploaded puzzle: {puzzle_doc['title']}")
