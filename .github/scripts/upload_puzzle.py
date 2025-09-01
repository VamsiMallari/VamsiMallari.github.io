name: Upload Chess Puzzle

on:
  workflow_dispatch:
  schedule:
    - cron: '0 7 * * *'

jobs:
  upload-puzzle:
    runs-on: ubuntu-latest
    env:
      FIREBASE_CREDENTIALS: ${{ secrets.FIREBASE_CREDENTIALS }}
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install firebase-admin python-chess stockfish
      - name: Run upload script
        run: python upload_chess_puzzle.py
