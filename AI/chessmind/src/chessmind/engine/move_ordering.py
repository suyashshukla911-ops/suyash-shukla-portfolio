from collections import defaultdict
import chess

from .constants import PIECE_VALUES


class MoveOrderer:
    """Search move ordering heuristics shared by every ChessMind search."""

    def __init__(self):
        self.killers = defaultdict(lambda: [None, None])
        self.history = defaultdict(int)
        self.countermoves = {}

    @staticmethod
    def capture_score(board, move):
        victim = board.piece_at(move.to_square)

        if victim is None and board.is_en_passant(move):
            victim_value = PIECE_VALUES[chess.PAWN]
        elif victim is None:
            victim_value = 0
        else:
            victim_value = PIECE_VALUES[victim.piece_type]

        attacker = board.piece_at(move.from_square)
        attacker_value = PIECE_VALUES.get(attacker.piece_type, 0) if attacker else 0
        return victim_value * 16 - attacker_value

    @staticmethod
    def is_quiet(board, move):
        return not (board.is_capture(move) or move.promotion)

    def score(self, board, move, ply, tt_move, counter_move=None):
        if tt_move is not None and move == tt_move:
            return 10_000_000

        score = 0

        if board.is_capture(move):
            # MVV-LVA style ordering. Promotions receive a separate bonus.
            score += 1_000_000 + self.capture_score(board, move)

        if move.promotion:
            score += 900_000 + PIECE_VALUES.get(move.promotion, 0)

        killer_0, killer_1 = self.killers[ply]
        if killer_0 == move:
            score += 800_000
        elif killer_1 == move:
            score += 700_000

        if counter_move is not None and move == counter_move:
            score += 650_000

        score += self.history[(board.turn, move)]
        return score

    def ordered(self, board, moves, ply, tt_move, counter_move=None):
        return sorted(
            list(moves),
            key=lambda move: self.score(board, move, ply, tt_move, counter_move),
            reverse=True,
        )

    def history_score(self, board, move):
        return self.history[(board.turn, move)]

    def record_cutoff(self, board, move, ply, depth, previous_move=None):
        if board.is_capture(move):
            return

        killer_0, _ = self.killers[ply]
        if killer_0 != move:
            self.killers[ply][1] = killer_0
            self.killers[ply][0] = move

        key = (board.turn, move)
        bonus = depth * depth
        self.history[key] = min(
            self.history[key] + bonus,
            1_000_000,
        )

        if previous_move is not None:
            self.countermoves[previous_move] = move

    def record_best_quiet(self, board, move, depth, previous_move=None):
        """Reward strong quiet moves even when they did not trigger beta."""
        if not self.is_quiet(board, move):
            return

        key = (board.turn, move)
        bonus = max(1, depth * depth // 2)
        self.history[key] = min(
            self.history[key] + bonus,
            1_000_000,
        )

        if previous_move is not None:
            self.countermoves[previous_move] = move

    def counter_move_for(self, previous_move):
        if previous_move is None:
            return None
        return self.countermoves.get(previous_move)

    def decay(self):
        """Age history values between searches so ancient moves do not dominate."""
        for key, value in list(self.history.items()):
            decayed = value - value // 16
            if decayed:
                self.history[key] = decayed
            else:
                self.history.pop(key, None)
