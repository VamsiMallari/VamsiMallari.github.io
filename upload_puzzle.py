import os
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import chess

# ==============================
# 1. Connect to Firestore ☁️
# ==============================
cred = credentials.Certificate("firebase_credentials.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

PUZZLES_COLLECTION = "puzzles"
SOLUTIONS_COLLECTION = "solutions"
METADATA_COLLECTION = "metadata"

# ==============================
# 2. Cleanup old puzzles 🗑️
# ==============================
def delete_old_puzzles():
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    puzzles_ref = db.collection(PUZZLES_COLLECTION)
    old_puzzles = puzzles_ref.where("createdAt", "<", cutoff).stream()

    for puzzle in old_puzzles:
        puzzles_ref.document(puzzle.id).delete()
        db.collection(SOLUTIONS_COLLECTION).document(puzzle.id).delete()
        print(f"Deleted old puzzle {puzzle.id}")

# ==============================
# 3. Fetch Lichess Daily Puzzle 🧩
# ==============================
def fetch_daily_puzzle():
    url = "https://lichess.org/api/puzzle/daily"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()

# ==============================
# 4. Process Puzzle ⚙️
# ==============================
def process_puzzle(data):
    puzzle = data["puzzle"]
    game = data["game"]

    fen = game["fen"]
    moves = puzzle["solution"]

    board = chess.Board(fen)

    # Convert UCI -> SAN
    solution_san = []
    for uci in moves:
        move = board.parse_uci(uci)
        solution_san.append(board.san(move))
        board.push(move)

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
    metadata_ref = db.collection(METADATA_COLLECTION).document("title_tracker")
    metadata = metadata_ref.get().to_dict() or {"last_index": -1, "names": [
        "magnus-carlsen", "viswanathan-anand", "garry-kasparov", "hikaru-nakamura",
        "bobby-fischer", "judith-polgar", "vladimir-kramnik", "ding-liren",
        "fabiano-caruana", "levon-aronian", "anatoly-karpov"
    ]}

    names = metadata["names"]
    last_index = metadata["last_index"]
    next_index = (last_index + 1) % len(names)
    gm_name = names[next_index]

    metadata_ref.set({"last_index": next_index, "names": names})

    if san_solution[-1].endswith("#"):
        desc = f"Find the forced mate in {len(san_solution)//2} moves."
    else:
        desc = "Find the best move to solve the puzzle."

    return gm_name, desc

# ==============================
# 6. Upload Puzzle 🚀
# ==============================
def upload_puzzle(puzzle):
    gm_name, description = generate_title_and_description(puzzle["san_solution"])

    # Puzzles collection
    doc_data = {
        "title": gm_name.replace("-", " ").title(),
        "description": description,
        "board": puzzle["fen"],
        "firstMove": "white",  # you can refine this if needed
        "createdAt": datetime.datetime.utcnow(),
        "createdBy": "lichess",
        "hasSolutions": True,
        "puzzleId": puzzle["puzzle_id"],
        "date": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
    }

    db.collection(PUZZLES_COLLECTION).document(gm_name).set(doc_data)

    # Solutions collection
    db.collection(SOLUTIONS_COLLECTION).document(gm_name).set({
        "puzzleId": puzzle["puzzle_id"],
        "lastUpdated": datetime.datetime.utcnow(),
        "solutions": puzzle["san_solution"]
    })

    print(f"✅ Uploaded puzzle {puzzle['puzzle_id']} under {gm_name}")

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
