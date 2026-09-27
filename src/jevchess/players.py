"""Players for games. Each exposes `name` and `pick(board, clocks) -> (move, record)`."""

import random
import time

import chess
import chess.engine

from .jev import Jev
from .llm import LLM


class JevPlayer:
    def __init__(self, jev: Jev, board_format: str = "grid", option_style: str = "san"):
        self.jev = jev
        self.board_format = board_format
        self.option_style = option_style
        self.name = "Jev"

    def start(self):
        pass

    def stop(self):
        pass

    def pick(self, board: chess.Board, clocks):
        r = self.jev.choose_move(board, self.board_format, self.option_style)
        top = sorted(r["probabilities"].items(), key=lambda kv: -kv[1])[:3]
        return r["move"], {"conf": r["confidence"], "top": top, "tok": r["input_tokens"], "ms": round(r["latency_ms"])}


class LLMPlayer:
    def __init__(self, llm: LLM, board_format: str = "grid", option_style: str = "san", label: str | None = None):
        self.llm = llm
        self.board_format = board_format
        self.option_style = option_style
        self.name = label or llm.model

    def start(self):
        pass

    def stop(self):
        pass

    def pick(self, board: chess.Board, clocks):
        r = self.llm.choose_move(board, self.board_format, self.option_style)
        move = r["move"]
        record = {"usd": r["usd"], "out_tok": r["output_tokens"], "ms": r["latency_ms"], "legal": r["legal_reply"]}
        if move is None:  # an unusable reply forfeits the choice to a random legal move, and is counted
            move = random.choice(list(board.legal_moves))
            record["fallback"] = r["reply"]
        return move, record


class RandomPlayer:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.name = "Random"

    def start(self):
        pass

    def stop(self):
        pass

    def pick(self, board: chess.Board, clocks):
        return self.rng.choice(sorted(board.legal_moves, key=lambda m: m.uci())), {}


FAST_TC = (10.0, 0.1)
CALIBRATION_TC = (120.0, 1.0)


class StockfishPlayer:
    """Stockfish on its own clock. `elo` uses UCI_LimitStrength; `epsilon` mixes in random moves.

    `tc` is (base seconds, increment). Names carry "@120+1" when playing at the
    calibration clock; the default is the fast clock.
    """

    def __init__(self, elo: int | None = None, epsilon: float = 0.0, seed: int = 0, hash_mb: int = 16, tc=FAST_TC):
        self.elo = elo
        self.epsilon = epsilon
        self.rng = random.Random(seed)
        self.hash_mb = hash_mb
        self.tc = tc
        self.name = f"SF{elo}" if elo else "SF"
        if epsilon:
            self.name += f"-rand{int(round(epsilon * 100))}"
        if tc != FAST_TC:
            self.name += f"@{tc[0]:g}+{tc[1]:g}"
        self.engine = None

    def start(self):
        self.engine = chess.engine.SimpleEngine.popen_uci("stockfish")
        opts = {"Threads": 1, "Hash": self.hash_mb}
        if self.elo:
            opts.update({"UCI_LimitStrength": True, "UCI_Elo": self.elo})
        self.engine.configure(opts)

    def stop(self):
        if self.engine:
            self.engine.quit()
            self.engine = None

    def pick(self, board: chess.Board, clocks):
        if self.epsilon and self.rng.random() < self.epsilon:
            return self.rng.choice(sorted(board.legal_moves, key=lambda m: m.uci())), {"random": True}
        w, b, winc, binc = clocks
        t0 = time.perf_counter()
        result = self.engine.play(board, chess.engine.Limit(white_clock=w, black_clock=b, white_inc=winc, black_inc=binc))
        return result.move, {"ms": round((time.perf_counter() - t0) * 1000)}
