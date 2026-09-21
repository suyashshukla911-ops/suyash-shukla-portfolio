from dataclasses import dataclass
import chess


@dataclass(frozen=True, slots=True)
class MoveInfo:
    uci: str
    san: str

    @classmethod
    def from_move(cls, board: chess.Board, move: chess.Move) -> "MoveInfo":
        if move not in board.legal_moves:
            raise ValueError(f"Illegal move: {move.uci()}")
        return cls(uci=move.uci(), san=board.san(move))
