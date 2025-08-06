import os
import json
import base64
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Decode base64 secret and save as a temporary JSON file
b64_cred = os.getenv("FIREBASE_CREDENTIALS_B64")
decoded_bytes = base64.b64decode(b64_cred)
with open("firebase_credentials.json", "wb") as f:
    f.write(decoded_bytes)

# Initialize Firebase
cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Get the daily puzzle from Lichess
res = requests.get("https://lichess.org/api/puzzle/daily")
data = res.json()

# Prepare puzzle document
puzzle_doc = {
    "title": data["puzzle"]["id"],
    "description": f"Daily puzzle from Lichess ({datetime.utcnow().isoformat()})",
    "firstMove": data["puzzle"]["initialPly"],
    "board": {
        "fen": data["game"]["fen"],
    },
    "createdBy": "lichess",
    "hasSolutions": True,
    "solutions": data["puzzle"]["solution"],
    "createdAt": firestore.SERVER_TIMESTAMP
}

# Upload to Firestore
db.collection("puzzles").add(puzzle_doc)
print(f"Uploaded puzzle: {data['puzzle']['id']}")
