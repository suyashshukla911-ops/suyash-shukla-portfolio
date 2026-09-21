from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn


@dataclass(slots=True)
class GameState:
    board: chess.Board

    @classmethod
    def new(cls) -> "GameState":
        return cls(chess.Board())

    @classmethod
    def from_fen(cls, fen: str) -> "GameState":
        board = chess.Board(fen)
        if not board.is_valid():
            raise ValueError(f"Invalid chess position: {board.status()}")
        return cls(board)

    def push_uci(self, uci: str) -> chess.Move:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise ValueError(f"Invalid UCI move: {uci}") from exc
        if move not in self.board.legal_moves:
            raise ValueError(f"Illegal move: {uci}")
        self.board.push(move)
        return move

    def push_san(self, san: str) -> chess.Move:
        try:
            move = self.board.parse_san(san)
        except ValueError as exc:
            raise ValueError(f"Invalid/illegal SAN move: {san}") from exc
        self.board.push(move)
        return move

    def is_over(self) -> bool:
        return self.board.is_game_over(claim_draw=True)

    def outcome(self):
        return self.board.outcome(claim_draw=True)

    def to_pgn(self) -> str:
        game = chess.pgn.Game()
        node = game
        for move in self.board.move_stack:
            node = node.add_variation(move)
        return str(game)

    def save_pgn(self, path: str | Path) -> None:
        Path(path).write_text(self.to_pgn(), encoding="utf-8")
