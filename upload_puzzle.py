import os
import json
import random
import firebase_admin
from firebase_admin import credentials, firestore

def get_firestore_client():
    # Get service account JSON from environment variable (GitHub secret)
    service_account_info = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if not service_account_info:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT environment variable is missing")
    service_account_dict = json.loads(service_account_info)
    cred = credentials.Certificate(service_account_dict)
    firebase_admin.initialize_app(cred)
    return firestore.client()

def get_grandmaster_names(db):
    """Fetch grandmaster names from metadata collection."""
    metadata_ref = db.collection('metadata')
    docs = metadata_ref.stream()
    gm_names = []
    for doc in docs:
        data = doc.to_dict()
        name = data.get('name')
        if name:
            gm_names.append(name)
    return gm_names

def generate_chess_puzzle():
    """
    Dummy generator - Replace with a real puzzle generator or API for production.
    Returns puzzle dict and solution dict.
    """
    mate_type = random.choice(['mate in 1', 'mate in 2', 'mate in 3'])
    fen_examples = {
        'mate in 1': '8/8/8/8/8/8/5K2/6k1 w - - 0 1',
        'mate in 2': '8/8/8/8/8/8/6K1/5k2 w - - 0 1',
        'mate in 3': '8/8/8/8/8/8/6K1/7k w - - 0 1'
    }
    solution_examples = {
        'mate in 1': ['Kg2#'],
        'mate in 2': ['Kg2', 'Kh1#'],
        'mate in 3': ['Kg2', 'Kh1', 'Kg1#']
    }
    fen = fen_examples[mate_type]
    solution = solution_examples[mate_type]
    return {
        'fen': fen,
        'mate_type': mate_type
    }, {
        'solution_moves': solution
    }

def generate_title_description(mate_type, gm_names):
    gm_name = random.choice(gm_names) if gm_names else "Unknown Grandmaster"
    title = f"{gm_name} - {mate_type.capitalize()}"
    description = f"A chess puzzle ({mate_type}) inspired by {gm_name}."
    return title, description

def upload_puzzle_and_solution():
    db = get_firestore_client()
    gm_names = get_grandmaster_names(db)
    puzzle, solution = generate_chess_puzzle()
    title, description = generate_title_description(puzzle['mate_type'], gm_names)

    # Upload puzzle
    puzzle_doc = {
        'title': title,
        'description': description,
        'fen': puzzle['fen'],
        'mate_type': puzzle['mate_type']
    }
    puzzle_ref = db.collection('puzzles').add(puzzle_doc)
    puzzle_id = puzzle_ref[1].id

    # Upload solution
    solution_doc = {
        'puzzle_id': puzzle_id,
        'solution_moves': solution['solution_moves']
    }
    db.collection('solutions').add(solution_doc)

    print(f"Uploaded puzzle '{title}' and its solution.")

if __name__ == "__main__":
    upload_puzzle_and_solution()
