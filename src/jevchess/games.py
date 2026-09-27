"""Play matches from the 8moves_v3 opening book, the book Stockfish's UCI_Elo was
calibrated with (official-stockfish/Stockfish PR #4341, 120+1).

Each opening is played twice with colours reversed. Only Stockfish is on a clock,
its own (`player.tc`); Jev and the LLM answer one position at a time with no time
limit. A game ends by checkmate, stalemate, insufficient material, threefold
repetition, the 50-move rule, or a 400-ply cap (draw). Stockfish losing on time
counts as a loss.
"""

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "data/8moves_v3.pgn"
NO_CLOCK = (120.0, 0.0)  # what Stockfish is told about a clockless opponent
MAX_PLIES = 400
_write_lock = threading.Lock()


def load_book(n: int, seed: int = 11) -> list[list[str]]:
    import random

    games = []
    with open(BOOK) as fh:
        while True:
            g = chess.pgn.read_game(fh)
            if g is None:
                break
            games.append([m.uci() for m in g.mainline_moves()])
    rng = random.Random(seed)
    return rng.sample(games, n)


def play(white, black, opening: list[str], meta: dict) -> dict:
    board = chess.Board()
    for u in opening:
        board.push_uci(u)
    players = {chess.WHITE: white, chess.BLACK: black}
    tcs = {c: getattr(p, "tc", NO_CLOCK) for c, p in players.items()}
    clocks = {c: tcs[c][0] for c in players}
    records = []
    result, termination = None, None
    for p in (white, black):
        p.start()
    try:
        while True:
            outcome = board.outcome(claim_draw=True)
            if outcome:
                result, termination = outcome.result(), outcome.termination.name.lower()
                break
            if board.ply() >= MAX_PLIES:
                result, termination = "1/2-1/2", "ply_cap"
                break
            side = board.turn
            player = players[side]
            t0 = time.perf_counter()
            move, rec = player.pick(board, (clocks[chess.WHITE], clocks[chess.BLACK], tcs[chess.WHITE][1], tcs[chess.BLACK][1]))
            elapsed = time.perf_counter() - t0
            if hasattr(player, "tc") and not rec.get("random"):
                clocks[side] = clocks[side] - elapsed + tcs[side][1]
                if clocks[side] < 0:
                    result = "0-1" if side == chess.WHITE else "1-0"
                    termination = "time_forfeit"
                    break
            if move not in board.legal_moves:
                raise RuntimeError(f"{player.name} returned illegal move {move} in {board.fen()}")
            rec.update({"ply": board.ply(), "side": "w" if side else "b", "san": board.san(move), "fen": board.fen()})
            records.append(rec)
            board.push(move)
    finally:
        for p in (white, black):
            p.stop()
    game = chess.pgn.Game.from_board(board)
    game.headers.update(
        {
            "Event": meta.get("match", "jev-chess"),
            "White": white.name,
            "Black": black.name,
            "Result": result,
            "Termination": termination,
            "TimeControl": " / ".join(f"{p.name} {p.tc[0]:g}+{p.tc[1]:g}" for p in (white, black) if hasattr(p, "tc")) or "-",
            "Opening": " ".join(opening),
        }
    )
    return {**meta, "white": white.name, "black": black.name, "result": result, "termination": termination,
            "plies": board.ply(), "book_plies": len(opening), "pgn": str(game), "moves": records}


def run_match(match: str, make_a, make_b, openings: list[list[str]], workers: int = 4, out: Path | None = None):
    """Play every opening twice, colours reversed; appends to results/games/<match>.jsonl and resumes."""
    """make_a/make_b(seed) build fresh players. Each opening is played with both colour assignments."""
    out = out or ROOT / "results/games" / f"{match}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            g = json.loads(line)
            done.add((g["opening_idx"], g["a_white"]))
    jobs = []
    for i, op in enumerate(openings):
        for a_white in (True, False):
            if (i, a_white) not in done:
                jobs.append((i, op, a_white))

    def one(job):
        i, op, a_white = job
        seed = i * 2 + int(a_white)
        a, b = make_a(seed), make_b(seed)
        white, black = (a, b) if a_white else (b, a)
        g = play(white, black, op, {"match": match, "opening_idx": i, "a_white": a_white, "a": a.name, "b": b.name})
        score_a = {"1-0": 1.0, "0-1": 0.0}.get(g["result"], 0.5)
        g["score_a"] = score_a if a_white else 1.0 - score_a
        with _write_lock:
            with open(out, "a") as fh:
                fh.write(json.dumps(g) + "\n")
        print(f"{match} op{i} {'A-white' if a_white else 'A-black'}: {g['white']} vs {g['black']} {g['result']} ({g['termination']}, {g['plies']} plies)", flush=True)
        return g

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, jobs))
    return [json.loads(l) for l in out.read_text().splitlines()]
