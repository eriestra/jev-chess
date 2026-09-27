"""Controls that use no model.

Capture-first rule: play a mating move if there is one; otherwise capture the most
valuable piece available (least valuable attacker first); otherwise give check;
otherwise play a random legal move. It encodes the preference for forcing moves
seen in Jev's pilot picks, with no understanding of whether the target is defended.
"""

import random

import chess

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def capture_first(board: chess.Board, rng: random.Random) -> chess.Move:
    moves = sorted(board.legal_moves, key=lambda m: m.uci())
    for m in moves:
        board.push(m)
        mate = board.is_checkmate()
        board.pop()
        if mate:
            return m
    captures = [m for m in moves if board.is_capture(m)]
    if captures:
        def gain(m):
            victim = chess.PAWN if board.is_en_passant(m) else board.piece_type_at(m.to_square)
            return (VALUE[victim], -VALUE[board.piece_type_at(m.from_square)])
        top = max(gain(m) for m in captures)
        return rng.choice([m for m in captures if gain(m) == top])
    checks = [m for m in moves if board.gives_check(m)]
    if checks:
        return rng.choice(checks)
    return rng.choice(moves)


class CaptureFirstPlayer:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.name = "Capture-first"

    def start(self):
        pass

    def stop(self):
        pass

    def pick(self, board, clocks):
        return capture_first(board, self.rng), {}


def random_puzzle_solve_probability(puzzle: dict) -> float:
    """Exact chance that a uniformly random mover solves a puzzle (same rules as puzzles.solve)."""
    board = chess.Board(puzzle["FEN"])
    moves = puzzle["Moves"].split()
    board.push_uci(moves[0])
    return _from_solver_turn(board, moves[1:])


def _from_solver_turn(board: chess.Board, line: list[str]) -> float:
    """P(solve) with the solver to move; `line` starts with the expected solver move."""
    if not line:
        return 1.0
    legal = list(board.legal_moves)
    expected = chess.Move.from_uci(line[0])
    mates = set()
    for m in legal:
        board.push(m)
        if board.is_checkmate():
            mates.add(m)
        board.pop()
    if expected in mates:
        return len(mates) / len(legal)
    b = board.copy()
    b.push(expected)
    if len(line) > 1:
        b.push_uci(line[1])
    return len(mates) / len(legal) + _from_solver_turn(b, line[2:]) / len(legal)
