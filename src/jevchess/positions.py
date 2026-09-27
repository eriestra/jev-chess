"""Sample test positions from rated Lichess games (January 2013 database, CC0).

One position per game, at a random ply between 8 and 120, keeping the move the
human actually played and both players' ratings for comparison.
"""

import io
import json
import random
from pathlib import Path

import chess
import chess.pgn
import zstandard

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/lichess_db_standard_rated_2013-01.pgn.zst"


def iter_games(path: Path = SOURCE):
    with open(path, "rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        text = io.TextIOWrapper(reader, encoding="utf-8")
        while True:
            game = chess.pgn.read_game(text)
            if game is None:
                return
            yield game


def sample(n: int, seed: int = 7, take: float = 0.02) -> list[dict]:
    """Keep each game with probability `take` so the sample spreads across the month."""
    rng = random.Random(seed)
    out = []
    for i, game in enumerate(iter_games()):
        if rng.random() > take:
            continue
        if not game.headers.get("TimeControl", "-").split("+")[0].isdigit():
            continue
        moves = list(game.mainline_moves())
        if len(moves) < 20:
            continue
        ply = rng.randint(8, min(len(moves) - 1, 120))
        board = game.board()
        for m in moves[:ply]:
            board.push(m)
        if board.legal_moves.count() < 2:
            continue
        mover = "White" if board.turn else "Black"
        try:
            rating = int(game.headers[f"{mover}Elo"])
        except (KeyError, ValueError):
            continue
        out.append(
            {
                "id": f"lichess-2013-01-{i}",
                "fen": board.fen(),
                "ply": ply,
                "human_move": moves[ply].uci(),
                "human_rating": rating,
                "time_control": game.headers.get("TimeControl"),
                "url": game.headers.get("Site"),
            }
        )
        if len(out) >= n:
            break
    return out


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    dest = ROOT / f"data/positions_{n}_s{seed}.json"
    dest.write_text(json.dumps(sample(n, seed), indent=1))
    print(dest)
