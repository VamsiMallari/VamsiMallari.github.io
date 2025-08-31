import os
import json
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import chess
import chess.pgn

# ==============================
# 1. Connect to Firestore ☁️
# ==============================
cred_json = os.getenv("FIREBASE_CREDENTIALS")
if not cred_json:
    raise RuntimeError("FIREBASE_CREDENTIALS not set in GitHub Secrets!")

cred_dict = json.loads(cred_json)
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

PUZZLES_COLLECTION = "puzzles"
SOLUTIONS_COLLECTION = "solutions"
METADATA_COLLECTION = "metadata"

# ==============================
# 2. Clean Up Old Puzzles 🗑️
# ==============================
def delete_old_puzzles():
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    puzzles_ref = db.collection(PUZZLES_COLLECTION)
    old_puzzles = puzzles_ref.where("createdAt", "<", cutoff).stream()

    for puzzle in old_puzzles:
        puzzle_id = puzzle.id
        puzzles_ref.document(puzzle_id).delete()
        db.collection(SOLUTIONS_COLLECTION).document(puzzle_id).delete()
        print(f"Deleted old puzzle {puzzle_id}")

# ==============================
# 3. Fetch Lichess Daily Puzzle 🧩
# ==============================
def fetch_daily_puzzle():
    url = "https://lichess.org/api/puzzle/daily"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()

# ==============================
# 4. Process and Validate ⚙️
# ==============================
def process_puzzle(data):
    puzzle = data["puzzle"]
    game = data["game"]

    fen = game["fen"]
    moves = puzzle["solution"]

    board = chess.Board(fen)

    # Convert UCI to SAN
    solution_san = []
    for uci in moves:
        move = board.parse_uci(uci)
        solution_san.append(board.san(move))
        board.push(move)

    # Only allow puzzles <= 6 half-moves (3 moves each side)
    if len(solution_san) > 6:
        raise ValueError("Puzzle too long, skipping.")

    return {
        "fen": fen,
        "uci_solution": moves,
        "san_solution": solution_san,
        "rating": puzzle.get("rating"),
        "puzzle_id": puzzle["id"],
    }

# ==============================
# 5. Title & Description 📝
# ==============================
def generate_title_and_description(san_solution):
    # Get next GM name in sequence
    metadata_ref = db.collection(METADATA_COLLECTION).document("title_tracker")
    metadata = metadata_ref.get().to_dict() or {"last_index": -1, "names": [
        "Magnus Carlsen", "Viswanathan Anand", "Garry Kasparov", "Hikaru Nakamura",
        "Bobby Fischer", "Judith Polgar", "Vladimir Kramnik"
    ]}

    names = metadata["names"]
    last_index = metadata["last_index"]
    next_index = (last_index + 1) % len(names)
    title = names[next_index]

    metadata_ref.set({"last_index": next_index, "names": names})

    # Description
    if san_solution[-1].endswith("#"):
        desc = f"Find the forced mate in {len(san_solution)//2} moves."
    else:
        desc = "Find the best move to gain a decisive advantage."

    return title, desc

# ==============================
# 6. Upload to Firestore 🚀
# ==============================
def upload_puzzle(puzzle):
    title, description = generate_title_and_description(puzzle["san_solution"])

    doc_data = {
        "title": title,
        "description": description,
        "board": {"fen": puzzle["fen"]},
        "firstMove": puzzle["san_solution"][0],
        "createdAt": datetime.datetime.utcnow(),
        "createdBy": "lichess-daily",
        "hasSolutions": True,
    }

    # Store puzzle
    db.collection(PUZZLES_COLLECTION).document(puzzle["puzzle_id"]).set(doc_data)

    # Store solution
    db.collection(SOLUTIONS_COLLECTION).document(puzzle["puzzle_id"]).set({
        "solutions": [puzzle["san_solution"]]
    })

    print(f"✅ Uploaded puzzle {puzzle['puzzle_id']}")

# ==============================
# Main
# ==============================
if __name__ == "__main__":
    delete_old_puzzles()
    data = fetch_daily_puzzle()
    try:
        puzzle = process_puzzle(data)
        upload_puzzle(puzzle)
    except Exception as e:
        print(f"⚠️ Skipped puzzle: {e}")
