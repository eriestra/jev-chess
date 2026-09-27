"""Puzzle rating from the Lichess puzzle database (CC0, database.lichess.org).

In a Lichess puzzle the FEN is the position before the opponent's move; the first
move of the solution is that move, then solver and opponent alternate. A puzzle
counts as solved only if the player finds every solver move; a checkmating move is
always accepted (Lichess accepts any mate in the final position).

The rating R maximises the likelihood of the observed solves given each puzzle's
Lichess rating: P(solve) = 1 / (1 + 10^((Rpuzzle - R) / 400)).
"""

import csv
import io
import json
import math
import random
from pathlib import Path

import chess
import numpy as np
import zstandard
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/lichess_db_puzzle.csv.zst"


def sample(per_bin: int = 40, lo: int = 600, hi: int = 2600, width: int = 200, seed: int = 3, scan: int = 600_000) -> list[dict]:
    """Stratified sample: `per_bin` well-established puzzles in each rating band."""
    rng = random.Random(seed)
    bins = {b: [] for b in range(lo, hi, width)}
    with open(SOURCE, "rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        rows = csv.DictReader(io.TextIOWrapper(reader, encoding="utf-8"))
        for i, row in enumerate(rows):
            if i >= scan:
                break
            r = int(row["Rating"])
            if int(row["RatingDeviation"]) > 80 or int(row["NbPlays"]) < 1000 or int(row["Popularity"]) < 80:
                continue
            b = lo + (r - lo) // width * width
            if b in bins:
                bins[b].append({k: row[k] for k in ("PuzzleId", "FEN", "Moves", "Rating", "RatingDeviation", "NbPlays", "Themes", "GameUrl")})
    out = []
    for b, rows in bins.items():
        out += rng.sample(rows, min(per_bin, len(rows)))
    return out


def solve(player_pick, puzzle: dict) -> dict:
    """player_pick(board) -> chess.Move. Returns solved flag and the moves tried."""
    board = chess.Board(puzzle["FEN"])
    moves = puzzle["Moves"].split()
    board.push_uci(moves[0])
    tried = []
    for i in range(1, len(moves), 2):
        expected = chess.Move.from_uci(moves[i])
        move = player_pick(board)
        tried.append(move.uci())
        if move != expected:
            board.push(move)
            if board.is_checkmate():
                return {"solved": True, "tried": tried}
            return {"solved": False, "tried": tried, "failed_at": i}
        board.push(move)
        if i + 1 < len(moves):
            board.push_uci(moves[i + 1])
    return {"solved": True, "tried": tried}


def rating(results: list[tuple[int, bool]]) -> dict:
    """MLE puzzle rating with a 95% interval from the curvature of the likelihood."""
    ratings = np.array([r for r, _ in results], dtype=float)
    solved = np.array([s for _, s in results], dtype=float)

    def nll(R):
        p = 1 / (1 + 10 ** ((ratings - R) / 400))
        p = np.clip(p, 1e-9, 1 - 1e-9)
        return -np.sum(solved * np.log(p) + (1 - solved) * np.log(1 - p))

    best = minimize_scalar(nll, bounds=(-500, 3500), method="bounded").x
    p = 1 / (1 + 10 ** ((ratings - best) / 400))
    info = np.sum(p * (1 - p)) * (math.log(10) / 400) ** 2
    se = 1 / math.sqrt(info) if info > 0 else float("inf")
    return {"rating": round(best), "ci95": [round(best - 1.96 * se), round(best + 1.96 * se)], "n": len(results), "solved": int(solved.sum())}


if __name__ == "__main__":
    s = sample()
    dest = ROOT / "data/puzzles_sample.json"
    dest.write_text(json.dumps(s, indent=1))
    print(dest, len(s))
