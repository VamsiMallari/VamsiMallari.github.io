#!/usr/bin/env python3
"""
Uploads one chess puzzle to Firestore.

- Uses Lichess Daily puzzle JSON (stable & public).
- Converts UCI solution to SAN with python-chess.
- Builds a meaningful title & description from PGN headers.
- Stores a flat (non-nested) array of SAN moves to avoid
  Firestore's "nested arrays not supported" error.
- Works with base64-encoded Firebase credentials in the
  FIREBASE_CREDENTIALS secret (GitHub Actions).
"""

import base64
import io
import json
import os
from datetime import datetime, timezone

import requests
import chess
import chess.pgn

import firebase_admin
from firebase_admin import credentials, firestore


# ---------- Firebase init (from base64 secret) ----------
def init_firebase_from_b64_env(env_key: str = "FIREBASE_CREDENTIALS") -> firestore.Client:
    b64 = os.getenv(env_key)
    if not b64:
        raise RuntimeError(f"{env_key} is not set")

    path = "firebase_credentials.json"
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))

    cred = credentials.Certificate(path)
    app = firebase_admin.initialize_app(cred)
    return firestore.client(app)


# ---------- Puzzle fetch & transform ----------
def fetch_lichess_daily() -> dict:
    url = "https://lichess.org/api/puzzle/daily"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def pgn_headers(pgn_text: str) -> dict:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return {}
    return {k: v for k, v in game.headers.items()}


def board_at_initial_fen(pgn_text: str, initial_ply: int) -> chess.Board:
    """Return board position just before the puzzle's first solution move."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return chess.Board()  # fallback

    board = game.board()
    # We want state after (initial_ply - 1) half-moves.
    target = max(0, int(initial_ply) - 1)
    for i, mv in enumerate(game.mainline_moves()):
        if i >= target:
            break
        board.push(mv)
    return board


def uci_to_san_list(board: chess.Board, uci_moves: list[str]) -> list[str]:
    """Convert a list of UCI strings into a flat list of SAN strings."""
    san = []
    b = board.copy()
    for u in uci_moves:
        mv = chess.Move.from_uci(u)
        if mv not in b.legal_moves:
            # Some puzzles include promotions without '='; normalize if needed
            # Try to infer promotion to Queen if missing.
            if b.is_pseudo_legal(mv):
                pass  # allow pseudo-legal; SAN will throw if really illegal
        san.append(b.san(mv))
        b.push(mv)
    return san


def human_title(headers: dict, side_to_move: chess.Color, pid: str) -> str:
    w = headers.get("White", "White")
    b = headers.get("Black", "Black")
    ev = headers.get("Event", "").strip()
    year = headers.get("Date", "")[:4]
    stm = "White" if side_to_move else "Black"
    parts = [f"{w} vs {b}".strip()]
    if ev:
        parts.append(f"— {ev}")
    if year and year != "????":
        parts.append(f"({year})")
    left = " ".join(parts).strip()
    return f"{left} • {stm} to move • Lichess Daily #{pid}"


def human_description(headers: dict, dt_utc: datetime) -> str:
    site = headers.get("Site", "")
    result = headers.get("Result", "")
    ev = headers.get("Event", "")
    when = dt_utc.strftime("%Y-%m-%d")
    bits = [f"Daily puzzle {when}"]
    if ev:
        bits.append(f"Event: {ev}")
    if site:
        bits.append(f"Site: {site}")
    if result:
        bits.append(f"Game result: {result}")
    return " • ".join(bits)


# ---------- Main ----------
def main():
    # 1) Firebase
    db = init_firebase_from_b64_env()

    # 2) Fetch daily puzzle
    data = fetch_lichess_daily()
    # Expected keys:
    # data["puzzle"]["id"], ["puzzle"]["initialPly"], ["puzzle"]["solution"] (list of UCI)
    # data["game"]["pgn"]

    pid = data["puzzle"]["id"]
    initial_ply = int(data["puzzle"]["initialPly"])
    pgn = data["game"]["pgn"]
    solution_uci = data["puzzle"]["solution"]

    # 3) Build board at puzzle start & derive SAN moves (flat list)
    start_board = board_at_initial_fen(pgn, initial_ply)
    san_moves = uci_to_san_list(start_board, solution_uci)
    initial_fen = start_board.fen()
    side = "w" if start_board.turn else "b"

    # 4) Human-friendly title/description from PGN headers
    hdr = pgn_headers(pgn)
    now = datetime.now(timezone.utc)
    title = human_title(hdr, start_board.turn, pid)
    description = human_description(hdr, now)

    # 5) Firestore document (no nested arrays)
    doc = {
        "puzzleId": pid,
        "title": title,
        "description": description,
        "date": now.strftime("%Y-%m-%d"),
        "createdAt": firestore.SERVER_TIMESTAMP,
        "createdBy": "lichess",
        "hasSolutions": True,
        "firstMove": initial_ply,                 # for your existing UI
        "solutions": san_moves,                   # flat list of SAN strings
        "sideToMove": side,                       # "w" or "b"
        "board": {
            "pgn": pgn,                           # keep PGN for replay
            "initialFen": initial_fen,            # render starting position quickly
            "orientation": side                   # UI can orient the board
        }
    }

    # 6) Store under /puzzles (let Firestore assign id)
    db.collection("puzzles").add(doc)
    print(f"✅ Uploaded Lichess Daily #{pid} with {len(san_moves)} solution moves.")


if __name__ == "__main__":
    main()
