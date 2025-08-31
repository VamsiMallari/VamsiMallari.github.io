import os
import json
import datetime
from google.cloud import firestore
from google.oauth2 import service_account
import requests
import chess

# 1. Connect to Your Database ☁️
def get_firestore_client():
    # In GitHub Actions, secrets are passed as environment variables.
    firebase_credentials = os.environ.get("FIREBASE_CREDENTIALS")
    if not firebase_credentials:
        raise Exception("FIREBASE_CREDENTIALS not found in environment variables. Make sure you set it in GitHub Secrets.")
    creds_dict = json.loads(firebase_credentials)
    credentials = service_account.Credentials.from_service_account_info(creds_dict)
    return firestore.Client(credentials=credentials, project=creds_dict["project_id"])

# 2. Clean Up Old Puzzles 🗑️
def delete_old_puzzles(db):
    cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    puzzles_ref = db.collection('puzzles')
    old_puzzles = puzzles_ref.where('created_at', '<', cutoff_date).stream()
    for puzzle_doc in old_puzzles:
        puzzle_id = puzzle_doc.id
        print(f"Deleting puzzle {puzzle_id}...")
        db.collection('puzzles').document(puzzle_id).delete()
        db.collection('solutions').document(puzzle_id).delete()
        db.collection('results').document(puzzle_id).delete()

# 3. Fetch the Daily Puzzle from Lichess 🧩
def fetch_lichess_puzzle():
    url = "https://lichess.org/api/puzzle/daily"
    response = requests.get(url)
    response.raise_for_status()
    puzzle_data = response.json()
    solution_moves = puzzle_data.get('solution', [])
    if not solution_moves:
        raise Exception("No solution moves found in puzzle data.")
    if len(solution_moves) > 6:
        raise Exception("Puzzle solution has more than 6 half-moves.")
    return puzzle_data

# 4. Process and Validate the Puzzle ⚙️
def process_puzzle(puzzle_data):
    fen = puzzle_data['game']['fen']
    moves_uci = puzzle_data['solution']
    board = chess.Board(fen)
    moves_san = []
    for move_uci in moves_uci:
        move = chess.Move.from_uci(move_uci)
        san = board.san(move)
        moves_san.append(san)
        board.push(move)
    if len(moves_uci) > 6:
        raise Exception("Solution too long, skipping puzzle.")
    return {
        "fen": fen,
        "moves_uci": moves_uci,
        "moves_san": moves_san,
        "initial_board": fen,
    }

# 5. Generate a Title and Description 📝
def get_next_gm_name(db):
    metadata_ref = db.collection('metadata').document('gm_sequence')
    metadata_doc = metadata_ref.get()
    gm_names = metadata_doc.get('names', [])
    last_used_index = metadata_doc.get('last_index', -1)
    next_index = (last_used_index + 1) % len(gm_names)
    next_gm = gm_names[next_index]
    metadata_ref.update({'last_index': next_index})
    return next_gm

def generate_description(moves_san):
    if moves_san and '#' in moves_san[-1]:
        return f"Find the forced mate in {len(moves_san)//2} moves."
    else:
        return "Find the best move to gain a decisive advantage."

# 6. Upload to Firestore 🚀
def upload_to_firestore(db, puzzle_info, gm_title, description):
    puzzle_id = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    puzzle_doc = {
        "title": gm_title,
        "description": description,
        "fen": puzzle_info["fen"],
        "created_at": datetime.datetime.utcnow(),
        "initial_board": puzzle_info["initial_board"],
    }
    solution_doc = {
        "moves": [puzzle_info["moves_san"]]
    }
    db.collection('puzzles').document(puzzle_id).set(puzzle_doc)
    db.collection('solutions').document(puzzle_id).set(solution_doc)
    print(f"Puzzle {puzzle_id} uploaded successfully.")

def main():
    db = get_firestore_client()
    delete_old_puzzles(db)
    puzzle_data = fetch_lichess_puzzle()
    puzzle_info = process_puzzle(puzzle_data)
    gm_title = get_next_gm_name(db)
    description = generate_description(puzzle_info["moves_san"])
    upload_to_firestore(db, puzzle_info, gm_title, description)
    print("Daily puzzle upload completed successfully.")

if __name__ == "__main__":
    main()
