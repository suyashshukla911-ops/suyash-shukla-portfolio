from dataclasses import dataclass
import chess


@dataclass(frozen=True, slots=True)
class TerminalState:
    terminal: bool
    kind: str | None
    score: int


MATE_SCORE = 1_000_000


def game_terminal_state(board: chess.Board) -> TerminalState:
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return TerminalState(False, None, 0)

    if outcome.termination == chess.Termination.CHECKMATE:
        return TerminalState(True, "checkmate", -MATE_SCORE)

    if outcome.termination == chess.Termination.STALEMATE:
        return TerminalState(True, "stalemate", 0)

    return TerminalState(True, outcome.termination.name.lower(), 0)


def search_terminal_state(board: chess.Board, ply: int = 0) -> TerminalState:
    """
    Fast terminal checks for search nodes.

    History-dependent repetition is not scanned at every node. Complete
    draw adjudication remains in the game layer.
    """
    legal = any(board.legal_moves)

    if not legal:
        if board.is_check():
            return TerminalState(True, "checkmate", -MATE_SCORE + ply)
        return TerminalState(True, "stalemate", 0)

    if board.is_insufficient_material():
        return TerminalState(True, "insufficient_material", 0)

    if board.halfmove_clock >= 150:
        return TerminalState(True, "seventyfive_moves", 0)

    return TerminalState(False, None, 0)
