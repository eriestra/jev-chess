import random

import chess
import pytest

from jevchess import elo, formats, grade, puzzles
from jevchess.llm import parse_reply

POSITIONS = [
    chess.STARTING_FEN,
    "r3k2r/pppq1ppp/2npbn2/4p3/2B1P3/2NP1N2/PPPQ1PPP/R3K2R w KQkq - 4 8",  # both sides can castle
    "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",  # en passant available
    "8/P6k/8/8/8/8/6K1/8 w - - 0 1",  # promotion
]


@pytest.mark.parametrize("fen", POSITIONS)
def test_options_are_exactly_the_legal_moves(fen):
    board = chess.Board(fen)
    for style in ("san", "described"):
        opts = formats.move_options(board, style)
        assert {board.parse_san(s) for s in opts} == set(board.legal_moves)
        assert len(opts) == board.legal_moves.count()


@pytest.mark.parametrize("fen", POSITIONS)
def test_every_board_format_reconstructs_the_position(fen):
    board = chess.Board(fen)
    grid = formats.board_state(board, "grid")
    rebuilt = chess.Board(None)
    for row in grid["board"][:8]:
        rank = int(row[0])
        for file, ch in enumerate(row[2:].split()):
            if ch != ".":
                rebuilt.set_piece_at(chess.square(file, rank - 1), chess.Piece.from_symbol(ch))
    assert rebuilt.board_fen() == board.board_fen()
    pieces = formats.board_state(board, "pieces")["pieces"]
    rebuilt = chess.Board(None)
    for color, table in pieces.items():
        for name, squares in table.items():
            ptype = {v: k for k, v in formats.PIECE_NAMES.items()}[name]
            for sq in squares:
                rebuilt.set_piece_at(chess.parse_square(sq), chess.Piece(ptype, color == "white"))
    assert rebuilt.board_fen() == board.board_fen()
    assert formats.board_state(board, "fen")["fen"] == board.fen()


def test_en_passant_and_castling_are_stated():
    board = chess.Board(POSITIONS[2])
    s = formats.board_state(board, "grid")
    assert s["en_passant_square"] == "f6"
    assert "White: kingside and queenside" in s["castling_rights"]


def test_described_move_mentions_capture_check_mate_promotion():
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    assert formats.describe_move(board, chess.Move.from_uci("a1a8")).endswith("checkmate")
    board = chess.Board(POSITIONS[3])
    assert "promotes to queen" in formats.describe_move(board, chess.Move.from_uci("a7a8q"))


def test_parse_reply():
    board = chess.Board()
    legal = {board.san(m): m for m in board.legal_moves}
    assert parse_reply("e4", legal) == chess.Move.from_uci("e2e4")
    assert parse_reply("**Nf3**", legal) == chess.Move.from_uci("g1f3")
    assert parse_reply("Qh5", legal) is None
    assert parse_reply("", legal) is None


def test_grading_math():
    scores = {"a": 50, "b": 20, "c": -600}
    best = grade.summarize_choice(scores, "a")
    assert best["cp_loss"] == 0 and best["rank"] == 1 and best["is_best"] and best["label"] == "ok"
    bad = grade.summarize_choice(scores, "c")
    assert bad["cp_loss"] == 650 and bad["rank"] == 3 and bad["label"] == "blunder"
    assert abs(grade.win_percent(0) - 50) < 1e-9
    assert grade.move_accuracy(60, 60) == pytest.approx(100.0, abs=0.01)
    r = grade.random_expectation(scores)
    assert r["cp_loss"] == pytest.approx((0 + 30 + 650) / 3)


def test_elo_recovers_known_ratings():
    rng = random.Random(0)
    truth = {"A": 1320, "B": 1000, "C": 700}
    games = []
    for a, b in [("A", "B"), ("B", "C"), ("A", "C")]:
        for _ in range(3000):
            p = 1 / (1 + 10 ** ((truth[b] - truth[a]) / 400))
            games.append((a, b, 1.0 if rng.random() < p else 0.0))
    got = elo.fit(games, {"A": 1320}, prior=0)
    assert abs(got["B"] - 1000) < 25
    assert abs(got["C"] - 700) < 35


def test_elo_is_finite_on_perfect_scores():
    games = [("A", "B", 1.0)] * 20
    got = elo.fit(games, {"A": 1320}, prior=2)
    assert 0 < 1320 - got["B"] < 1000


def test_puzzle_solve_rules():
    # Mate in one after the opponent's first move: the solution line is followed.
    p = {"FEN": "6k1/5ppp/8/8/8/8/5PPP/R5K1 b - - 0 1", "Moves": "g8h8 a1a8"}
    assert puzzles.solve(lambda b: chess.Move.from_uci("a1a8"), p)["solved"]
    assert not puzzles.solve(lambda b: chess.Move.from_uci("g1f1"), p)["solved"]


def test_puzzle_rating_mle():
    rng = random.Random(1)
    true = 1400
    results = []
    for _ in range(4000):
        r = rng.randint(600, 2400)
        results.append((r, rng.random() < 1 / (1 + 10 ** ((r - true) / 400))))
    got = puzzles.rating(results)
    assert abs(got["rating"] - true) < 40
    assert got["ci95"][0] < true < got["ci95"][1]


def test_capture_first_prefers_mate_then_biggest_capture():
    from jevchess.baselines import capture_first
    rng = random.Random(0)
    board = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    assert capture_first(board, rng) == chess.Move.from_uci("a1a8")
    board = chess.Board("4k3/8/8/3q1p2/4P3/8/8/4K3 w - - 0 1")  # exd5 (queen) beats exf5 (pawn)
    assert capture_first(board, rng) == chess.Move.from_uci("e4d5")


def test_random_puzzle_probability():
    from jevchess.baselines import random_puzzle_solve_probability
    p = {"FEN": "6k1/5ppp/8/8/8/8/5PPP/R5K1 b - - 0 1", "Moves": "g8h8 a1a8"}
    board = chess.Board(p["FEN"]); board.push_uci("g8h8")
    n = board.legal_moves.count()
    assert random_puzzle_solve_probability(p) == pytest.approx(1 / n)


def test_game_between_random_players_ends_by_the_rules():
    import io
    import chess.pgn
    from jevchess.games import play
    from jevchess.players import RandomPlayer
    g = play(RandomPlayer(1), RandomPlayer(2), ["e2e4", "e7e5"], {"match": "test"})
    assert g["result"] in ("1-0", "0-1", "1/2-1/2")
    assert g["termination"] in ("checkmate", "stalemate", "insufficient_material", "threefold_repetition",
                                "fifty_moves", "seventyfive_moves", "fivefold_repetition", "ply_cap")
    game = chess.pgn.read_game(io.StringIO(g["pgn"]))
    assert game.headers["Result"] == g["result"]
    assert len(g["moves"]) == g["plies"] - 2


def test_bootstrap_interval_contains_fit():
    rng = random.Random(3)
    games = [("A", "B", 1.0 if rng.random() < 0.76 else 0.0) for _ in range(200)]
    point = elo.fit(games, {"A": 1320})
    ci = elo.bootstrap(games, {"A": 1320}, n=100)
    assert ci["B"][0] <= point["B"] <= ci["B"][1]
    assert ci["A"] == (1320, 1320)
