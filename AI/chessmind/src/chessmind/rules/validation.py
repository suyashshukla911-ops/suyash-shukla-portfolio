import chess


def validate_board(board: chess.Board) -> None:
    if not isinstance(board, chess.Board):
        raise TypeError("Expected chess.Board.")
    if not board.is_valid():
        raise ValueError(f"Invalid board state: {board.status()}")


def validate_move(board: chess.Board, move: chess.Move) -> None:
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move: {move.uci()}")
