import os
import json
import random
import firebase_admin
from firebase_admin import credentials, firestore
import chess
import chess.engine
from stockfish import Stockfish

STOCKFISH_PATH = "/usr/games/stockfish"  # Update if needed (GitHub Actions runner has stockfish installed here)

def get_firestore_client():
    service_account_info = os.environ.get('FIREBASE_CREDENTIALS')
    if not service_account_info:
        raise RuntimeError("FIREBASE_CREDENTIALS environment variable is missing")
    service_account_dict = json.loads(service_account_info)
    cred = credentials.Certificate(service_account_dict)
    firebase_admin.initialize_app(cred)
    return firestore.client()

def get_grandmaster_names(db):
    gm_names = []
    try:
        metadata_ref = db.collection('metadata')
        docs = metadata_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            name = data.get('name')
            if name:
                gm_names.append(name)
    except Exception as e:
        print(f"Error fetching grandmaster names: {e}")
    return gm_names

def generate_mate_in_n_puzzle(stockfish, n):
    """
    Randomly generates positions until a mate in n puzzle is found.
    Returns FEN and solution moves.
    """
    tries = 0
    while tries < 1000:
        board = chess.Board()
        # Make random moves for both sides
        for _ in range(random.randint(6, 20)):
            legal_moves = list(board.legal_moves)
            if not legal_moves or board.is_game_over():
                break
            board.push(random.choice(legal_moves))
        if board.is_game_over():
            tries += 1
            continue
        # Ask Stockfish for mate in n
        stockfish.set_fen_position(board.fen())
        info = stockfish.get_best_move_time(300)
        if info is None:
            tries += 1
            continue
        # Check for mate in n
        stockfish.set_fen_position(board.fen())
        analysis = stockfish.get_top_moves(1)
        if analysis and 'Mate' in analysis[0]:
            mate_type = analysis[0]['Mate']
            if mate_type == n and board.turn:  # Only consider puzzles where it's White to move
                solution_moves = []
                for i in range(n):
                    move = stockfish.get_best_move()
                    if move is None:
                        break
                    solution_moves.append(move)
                    board.push(chess.Move.from_uci(move))
                    stockfish.set_fen_position(board.fen())
                # Validate if checkmate is reached
                if board.is_checkmate():
                    return board.fen(), solution_moves
        tries += 1
    raise Exception("Failed to generate a mate in {} puzzle after many tries.".format(n))

def generate_title_description(mate_type, gm_names):
    gm_name = random.choice(gm_names) if gm_names else "Unknown Grandmaster"
    title = f"{gm_name} - {mate_type.capitalize()}"
    description = f"A chess puzzle ({mate_type}) inspired by {gm_name}."
    return title, description

def upload_puzzle_and_solution():
    db = get_firestore_client()
    gm_names = get_grandmaster_names(db)

    # Setup Stockfish engine
    stockfish = Stockfish(path=STOCKFISH_PATH, parameters={"Threads": 2, "Minimum Thinking Time": 30})

    # Generate puzzle
    n = random.choice([1, 2, 3])
    mate_type = f"mate in {n}"
    try:
        fen, solution_moves = generate_mate_in_n_puzzle(stockfish, n)
    except Exception as e:
        print(f"Error generating puzzle: {e}")
        return

    title, description = generate_title_description(mate_type, gm_names)

    puzzle_doc = {
        'title': title,
        'description': description,
        'fen': fen,
        'mate_type': mate_type,
        'created_by': 'github-action',
        'source': 'stockfish',
    }
    try:
        puzzle_ref = db.collection('puzzles').add(puzzle_doc)
        puzzle_id = puzzle_ref[1].id
        print(f"Puzzle uploaded with ID: {puzzle_id}")
    except Exception as e:
        print(f"Error uploading puzzle: {e}")
        return

    solution_doc = {
        'puzzle_id': puzzle_id,
        'solution_moves': solution_moves,
        'mate_type': mate_type,
        'fen': fen,
    }
    try:
        db.collection('solutions').add(solution_doc)
        print(f"Solution uploaded for puzzle ID: {puzzle_id}")
    except Exception as e:
        print(f"Error uploading solution: {e}")

if __name__ == "__main__":
    upload_puzzle_and_solution()
