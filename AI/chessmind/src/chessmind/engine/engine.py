from dataclasses import dataclass

import chess

from ..config import EngineConfig
from ..rules.termination import game_terminal_state
from ..rules.validation import validate_board
from .constants import MATE_SCORE, MATE_THRESHOLD
from .move_ordering import MoveOrderer
from .search import Searcher
from .transposition import TranspositionTable


@dataclass(slots=True)
class EngineResponse:
    best_move_uci: str | None
    best_move_san: str | None
    evaluation: float
    depth: int
    nodes: int
    elapsed_ms: int
    principal_variation: list[str]
    is_mate: bool
    terminal: bool
    terminal_kind: str | None
    qnodes: int
    tt_hits: int
    tt_cutoffs: int
    cutoffs: int
    nps: int


class ChessEngine:
    def __init__(self, config: EngineConfig | None = None):
        self.config = (config or EngineConfig()).validated()
        self.tt = TranspositionTable(self.config.transposition_size)
        self.orderer = MoveOrderer()
        self.searcher = Searcher(
            self.tt,
            self.orderer,
            quiescence_depth=self.config.quiescence_depth,
            check_interval=self.config.stop_check_interval,
        )

    def stop(self):
        self.searcher.request_stop()

    def analyze(self, board: chess.Board) -> EngineResponse:
        validate_board(board)

        terminal = game_terminal_state(board)
        if terminal.terminal:
            evaluation = -MATE_SCORE / 100.0 if terminal.kind == "checkmate" else 0.0
            return EngineResponse(
                best_move_uci=None,
                best_move_san=None,
                evaluation=evaluation,
                depth=0,
                nodes=0,
                elapsed_ms=0,
                principal_variation=[],
                is_mate=terminal.kind == "checkmate",
                terminal=True,
                terminal_kind=terminal.kind,
                qnodes=0,
                tt_hits=0,
                tt_cutoffs=0,
                cutoffs=0,
                nps=0,
            )

        work = board.copy(stack=True)
        move, score, pv = self.searcher.search(
            work,
            max_depth=self.config.max_depth,
            time_limit_ms=self.config.time_limit_ms,
        )

        # The search must never leak an invalid Move object to a client.
        # Validate against one concrete legal-move list before converting to
        # SAN. If a defensive fallback is needed, keep the position playable.
        legal_moves = list(work.legal_moves)
        if not legal_moves:
            terminal = game_terminal_state(work)
            evaluation = -MATE_SCORE / 100.0 if terminal.kind == "checkmate" else 0.0
            return EngineResponse(
                best_move_uci=None,
                best_move_san=None,
                evaluation=evaluation,
                depth=0,
                nodes=self.searcher.context.stats.nodes,
                elapsed_ms=self.searcher.context.stats.elapsed_ms,
                principal_variation=[],
                is_mate=terminal.kind == "checkmate",
                terminal=True,
                terminal_kind=terminal.kind,
                qnodes=self.searcher.context.stats.qnodes,
                tt_hits=self.searcher.context.stats.tt_hits,
                tt_cutoffs=self.searcher.context.stats.tt_cutoffs,
                cutoffs=self.searcher.context.stats.cutoffs,
                nps=self.searcher.context.stats.nps,
            )

        if move is None or move not in legal_moves:
            move = legal_moves[0]
            score = self._side_to_move_eval(work, fast=True)
            pv = [move]

        response_stats = self.searcher.context.stats

        return EngineResponse(
            best_move_uci=move.uci(),
            best_move_san=work.san(move),
            evaluation=score / 100.0,
            depth=response_stats.completed_depth,
            nodes=response_stats.nodes,
            elapsed_ms=response_stats.elapsed_ms,
            principal_variation=[item.uci() for item in pv],
            is_mate=abs(score) >= MATE_THRESHOLD,
            terminal=False,
            terminal_kind=None,
            qnodes=response_stats.qnodes,
            tt_hits=response_stats.tt_hits,
            tt_cutoffs=response_stats.tt_cutoffs,
            cutoffs=response_stats.cutoffs,
            nps=response_stats.nps,
        )

    def choose_move(self, board: chess.Board) -> chess.Move:
        response = self.analyze(board)
        return chess.Move.from_uci(response.best_move_uci)
