import os
import base64
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import chess.pgn
import io
import time

# Decode Firebase credentials from GitHub secret
b64_cred = os.getenv("FIREBASE_CREDENTIALS")
if not b64_cred:
    raise ValueError("FIREBASE_CREDENTIALS secret not set!")
with open("firebase_credentials.json", "wb") as f:
    f.write(base64.b64decode(b64_cred))

# Initialize Firebase
cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Get daily puzzle from Lichess API
url = "https://lichess.org/api/puzzle/daily"
res = requests.get(url)
res.raise_for_status()
data = res.json()

puzzle_id = data["puzzle"]["id"]
pgn_str = data["game"]["pgn"]
solutions_raw = data["puzzle"]["solution"]

# Flatten solutions to avoid nested arrays in Firestore
solutions = []
for s in solutions_raw:
    if isinstance(s, list):
        solutions.extend(s)
    else:
        solutions.append(s)

# Create meaningful title/description
title = f"Lichess Puzzle {puzzle_id}"
description = f"Puzzle from Lichess daily feed ({datetime.utcnow().strftime('%Y-%m-%d')})"

puzzle_doc = {
    "title": title,
    "description": description,
    "firstMove": data["puzzle"]["initialPly"],
    "board": {
        "pgn": pgn_str
    },
    "createdBy": "lichess",
    "hasSolutions": True,
    "solutions": solutions,
    "createdAt": firestore.SERVER_TIMESTAMP
}

# Upload to Firestore
db.collection("puzzles").add(puzzle_doc)
print(f"✅ Uploaded puzzle {puzzle_id} with {len(solutions)} solution moves.")
