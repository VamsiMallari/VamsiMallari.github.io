import os
import json
import base64
import requests
import datetime
from firebase_admin import credentials, firestore, initialize_app

# Step 1: Decode base64 Firebase credentials
encoded_creds = os.environ["FIREBASE_CREDENTIALS"]
decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
creds_dict = json.loads(decoded_creds)

# Step 2: Initialize Firebase
cred = credentials.Certificate(creds_dict)
initialize_app(cred)
db = firestore.client()

# Step 3: Get daily puzzle from Lichess
response = requests.get("https://lichess.org/api/puzzle/daily")
data = response.json()

game = data["game"]
puzzle = data["puzzle"]

# Step 4: Extract FEN from PGN
def extract_fen_from_pgn(pgn):
    for line in pgn.split("\n"):
        if line.startswith("[FEN "):
            return line.split('"')[1]
    return None

fen = extract_fen_from_pgn(game["pgn"])
if fen is None:
    raise ValueError("FEN not found in PGN")

# Step 5: Title, description, solution
puzzle_id = puzzle["id"]
solution = puzzle["solution"]
first_move = solution[0]
today = datetime.datetime.now().strftime("%B %d, %Y")
title = f"Daily Puzzle - {today}"
description = f"Lichess puzzle of the day. Puzzle ID: {puzzle_id}"

# Step 6: Firestore upload
doc = {
    "title": title,
    "description": description,
    "board": {"fen": fen},
    "firstMove": first_move,
    "solutions": [solution],
    "hasSolutions": True,
    "createdBy": "Lichess API",
    "createdAt": firestore.SERVER_TIMESTAMP,
}

db.collection("puzzles").add(doc)
print(f"✅ Puzzle uploaded with title: {title}")
