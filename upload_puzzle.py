import requests
import json
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Load Firebase credentials and initialize app
cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Fetch a random puzzle from Lichess
response = requests.get("https://lichess.org/api/puzzle/daily")
data = response.json()

puzzle = data["puzzle"]
game = data["game"]

# Generate fields
puzzle_id = puzzle["id"]
solution = puzzle["solution"]
pgn = game["pgn"]
initial_ply = puzzle["initialPly"]
num_moves = len(solution)

# Meaningful title and description
formatted_date = datetime.utcnow().strftime("%B %d, %Y")
title = f"Lichess Puzzle {puzzle_id.upper()}"
description = f"{formatted_date} | {num_moves}-move chess puzzle from Lichess.org"

# Convert PGN to board state
from chess.pgn import read_game
from io import StringIO
import chess

game_obj = read_game(StringIO(pgn))
board = game_obj.board()

for move in game_obj.mainline_moves()[:initial_ply]:
    board.push(move)

# Convert board to dict format
board_dict = {}
for square in chess.SQUARES:
    piece = board.piece_at(square)
    if piece:
        board_dict[chess.square_name(square)] = piece.symbol()

# Firestore document
doc = {
    "title": title,
    "description": description,
    "createdAt": firestore.SERVER_TIMESTAMP,
    "firstMove": solution[0],
    "board": board_dict,
    "solutions": solution,
    "hasSolutions": True,
    "createdBy": "lichess automation"
}

# Upload to Firestore
db.collection("puzzles").add(doc)
print(f"Uploaded puzzle {puzzle_id} successfully.")
