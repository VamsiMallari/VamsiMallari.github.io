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

# Construct document with PGN instead of FEN
puzzle_doc = {
    "title": data["puzzle"]["id"],
    "description": f"Daily puzzle from Lichess ({datetime.utcnow().isoformat()})",
    "firstMove": data["puzzle"]["initialPly"],
    "board": {
        "pgn": data["game"]["pgn"],
    },
    "createdBy": "lichess",
    "hasSolutions": True,
    "solutions": data["puzzle"]["solution"],
    "createdAt": firestore.SERVER_TIMESTAMP
}

# Upload to Firestore
db.collection("puzzles").add(puzzle_doc)
print(f"✅ Uploaded puzzle: {data['puzzle']['id']}")
