import os
import json
import base64
import requests
import datetime
from firebase_admin import credentials, firestore, initialize_app

# Step 1: Decode Firebase credentials from base64
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

puzzle = data["game"]
puzzle_info = data["puzzle"]

puzzle_id = puzzle_info["id"]
solution = puzzle_info["solution"]
fen = puzzle["fen"]
first_move = solution[0]

# Step 4: Create meaningful title and description
today = datetime.datetime.now().strftime("%B %d, %Y")
title = f"Daily Puzzle - {today}"
description = f"Lichess puzzle of the day. Puzzle ID: {puzzle_id}"

# Step 5: Prepare Firestore document
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

# Step 6: Upload to Firestore
db.collection("puzzles").add(doc)
print(f"✅ Puzzle uploaded with title: {title}")
