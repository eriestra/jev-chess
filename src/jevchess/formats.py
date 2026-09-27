"""How a position and its legal moves are shown to a player.

A format has two parts: the board (the Jev `state`) and the options (the Choice
`criteria`). Every format carries the same facts: piece placement, side to move,
castling rights and the en passant square. Nothing else about the game is given.
"""

import chess

PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def side_name(color: bool) -> str:
    return "White" if color == chess.WHITE else "Black"


def castling_text(board: chess.Board) -> str:
    rights = []
    for color in (chess.WHITE, chess.BLACK):
        sides = []
        if board.has_kingside_castling_rights(color):
            sides.append("kingside")
        if board.has_queenside_castling_rights(color):
            sides.append("queenside")
        rights.append(f"{side_name(color)}: {' and '.join(sides) if sides else 'none'}")
    return "; ".join(rights)


def en_passant_text(board: chess.Board) -> str:
    ep = board.ep_square
    if ep is None or not board.has_legal_en_passant():
        return "none"
    return chess.square_name(ep)


def grid_rows(board: chess.Board) -> list[str]:
    rows = []
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            piece = board.piece_at(chess.square(file, rank))
            cells.append(piece.symbol() if piece else ".")
        rows.append(f"{rank + 1} {' '.join(cells)}")
    rows.append("  a b c d e f g h")
    return rows


def piece_lists(board: chess.Board) -> dict:
    out = {}
    for color in (chess.WHITE, chess.BLACK):
        pieces = {}
        for ptype in (chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
            squares = sorted(chess.square_name(s) for s in board.pieces(ptype, color))
            if squares:
                pieces[PIECE_NAMES[ptype]] = squares
        out[side_name(color).lower()] = pieces
    return out


def board_state(board: chess.Board, fmt: str) -> dict:
    side = side_name(board.turn)
    if fmt == "fen":
        return {"fen": board.fen(), "side_to_move": side}
    if fmt == "grid":
        return {
            "board": grid_rows(board),
            "legend": "Uppercase letters are White pieces, lowercase are Black, '.' is an empty square. K king, Q queen, R rook, B bishop, N knight, P pawn.",
            "side_to_move": side,
            "castling_rights": castling_text(board),
            "en_passant_square": en_passant_text(board),
        }
    if fmt == "pieces":
        return {
            "pieces": piece_lists(board),
            "side_to_move": side,
            "castling_rights": castling_text(board),
            "en_passant_square": en_passant_text(board),
        }
    raise ValueError(f"unknown board format {fmt}")


def describe_move(board: chess.Board, move: chess.Move) -> str:
    """Plain-language description of a move: what moves, where, and what it captures."""
    piece = board.piece_at(move.from_square)
    color = side_name(piece.color)
    if board.is_castling(move):
        text = f"{color} castles {'kingside' if board.is_kingside_castling(move) else 'queenside'}"
    else:
        text = f"{color} {PIECE_NAMES[piece.piece_type]} from {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}"
        if board.is_en_passant(move):
            text += ", captures a pawn en passant"
        else:
            captured = board.piece_at(move.to_square)
            if captured:
                text += f", captures {side_name(captured.color)} {PIECE_NAMES[captured.piece_type]}"
        if move.promotion:
            text += f", promotes to {PIECE_NAMES[move.promotion]}"
    board.push(move)
    try:
        if board.is_checkmate():
            text += ", checkmate"
        elif board.is_check():
            text += ", gives check"
    finally:
        board.pop()
    return text


def move_options(board: chess.Board, style: str) -> dict[str, str | None]:
    """Choice criteria: every legal move keyed by its SAN, optionally described."""
    options = {}
    for move in board.legal_moves:
        san = board.san(move)
        options[san] = describe_move(board, move) if style == "described" else None
    return options


def instructions(board: chess.Board) -> str:
    side = side_name(board.turn)
    return f"Choose the best move for {side} in this chess position."
