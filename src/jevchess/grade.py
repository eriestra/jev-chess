"""Grade every legal move of a position with full-strength Stockfish.

One MultiPV search per position scores all legal moves at the same depth, so the
chosen move's rank, its centipawn loss and the expected loss of a uniformly random
move all come from one consistent analysis. Results are cached per FEN.
"""

import json
import math
import sqlite3
import threading
from pathlib import Path

import chess
import chess.engine

ROOT = Path(__file__).resolve().parents[2]
DEPTH = 16
CP_CAP = 1000  # evaluations are clipped to +-10 pawns before computing losses
MATE_CP = 10000


def win_percent(cp: float) -> float:
    """Lichess win% from White-or-mover centipawns (lichess.org/page/accuracy)."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(win_before: float, win_after: float) -> float:
    """Lichess per-move accuracy from the mover's win% before and after the move."""
    diff = max(0.0, win_before - win_after)
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * diff) - 3.1669))


def classify(win_before: float, win_after: float) -> str:
    """Lichess move labels use a drop in winning chances (-1..1) of 0.1 / 0.2 / 0.3,
    which is 5 / 10 / 15 points of win%."""
    drop = win_before - win_after
    if drop >= 15:
        return "blunder"
    if drop >= 10:
        return "mistake"
    if drop >= 5:
        return "inaccuracy"
    return "ok"


class Grader:
    def __init__(self, depth: int = DEPTH, threads: int = 1, cache_path: Path | None = None):
        self.depth = depth
        self.engine = chess.engine.SimpleEngine.popen_uci("stockfish")
        self.engine.configure({"Threads": threads, "Hash": 64})
        path = cache_path or ROOT / "results/cache/grades.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("create table if not exists grades (fen text, depth int, moves text, primary key (fen, depth))")
        self.lock = threading.Lock()

    def close(self):
        self.engine.quit()

    def scores(self, board: chess.Board) -> dict[str, int]:
        """Centipawn score of every legal move (UCI key), from the mover's point of view."""
        fen = board.fen()
        with self.lock:
            row = self.db.execute("select moves from grades where fen=? and depth=?", (fen, self.depth)).fetchone()
        if row:
            return json.loads(row[0])
        n = board.legal_moves.count()
        infos = self.engine.analyse(board, chess.engine.Limit(depth=self.depth), multipv=n)
        out = {}
        for info in infos:
            if "pv" not in info:
                continue
            out[info["pv"][0].uci()] = info["score"].pov(board.turn).score(mate_score=MATE_CP)
        # MultiPV can occasionally omit a move; score any missing ones directly.
        for move in board.legal_moves:
            if move.uci() not in out:
                info = self.engine.analyse(board, chess.engine.Limit(depth=self.depth), root_moves=[move])
                out[move.uci()] = info["score"].pov(board.turn).score(mate_score=MATE_CP)
        with self.lock:
            self.db.execute("insert or replace into grades values (?,?,?)", (fen, self.depth, json.dumps(out)))
            self.db.commit()
        return out


def summarize_choice(scores: dict[str, int], uci: str) -> dict:
    """Quality of one chosen move given all move scores."""
    capped = {m: max(-CP_CAP, min(CP_CAP, s)) for m, s in scores.items()}
    best = max(capped.values())
    chosen = capped[uci]
    ranked = sorted(scores.values(), reverse=True)
    rank = 1 + sum(1 for s in scores.values() if s > scores[uci])
    wb, wa = win_percent(best), win_percent(chosen)
    return {
        "cp_loss": best - chosen,
        "rank": rank,
        "n_moves": len(scores),
        "is_best": scores[uci] == ranked[0],
        "accuracy": move_accuracy(wb, wa),
        "label": classify(wb, wa),
    }


def random_expectation(scores: dict[str, int]) -> dict:
    """Expected quality of a uniformly random legal move."""
    per = [summarize_choice(scores, m) for m in scores]
    n = len(per)
    return {
        "cp_loss": sum(p["cp_loss"] for p in per) / n,
        "accuracy": sum(p["accuracy"] for p in per) / n,
        "is_best": sum(p["is_best"] for p in per) / n,
        "blunder": sum(p["label"] == "blunder" for p in per) / n,
    }
