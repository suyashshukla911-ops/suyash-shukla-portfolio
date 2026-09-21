from dataclasses import dataclass
import chess


@dataclass(slots=True)
class Position:
    board: chess.Board

    @classmethod
    def start(cls) -> "Position":
        return cls(chess.Board())

    @classmethod
    def from_fen(cls, fen: str) -> "Position":
        if not isinstance(fen, str) or not fen.strip():
            raise ValueError("FEN must be a non-empty string.")
        try:
            board = chess.Board(fen.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid FEN: {exc}") from exc
        if not board.is_valid():
            raise ValueError(f"Invalid chess position: {board.status()}")
        return cls(board)

    @property
    def fen(self) -> str:
        return self.board.fen()

    def copy(self) -> "Position":
        return Position(self.board.copy(stack=True))
