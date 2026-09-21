import chess


def uci_to_san(board: chess.Board, uci: str) -> str:
    move = chess.Move.from_uci(uci)
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move: {uci}")
    return board.san(move)


def san_to_uci(board: chess.Board, san: str) -> str:
    return board.parse_san(san).uci()
