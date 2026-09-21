from __future__ import annotations

import json
import os
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event
import time
from urllib.parse import urlparse

try:
    import chess
    from chess import popcount
except ImportError as exc:
    raise SystemExit("python-chess is not installed. Run AI\\run_ai.bat first.") from exc


# ========================= CHESSMIND CORE =========================

INF = 10_000_000
MATE_SCORE = 1_000_000
MATE_THRESHOLD = MATE_SCORE - 10_000
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20_000,
}
CENTER_SQUARES = {chess.D4, chess.E4, chess.D5, chess.E5}

@dataclass(frozen=True, slots=True)
class EngineConfig:
    max_depth: int = 18
    time_limit_ms: int = 2500
    transposition_size: int = 131_072
    quiescence_depth: int = 10
    stop_check_interval: int = 128
    deterministic: bool = True

    def validated(self):
        return EngineConfig(
            max_depth=max(1, min(int(self.max_depth), 32)),
            time_limit_ms=max(50, min(int(self.time_limit_ms), 120_000)),
            transposition_size=max(10_000, min(int(self.transposition_size), 2_000_000)),
            quiescence_depth=max(0, min(int(self.quiescence_depth), 32)),
            stop_check_interval=max(1, min(int(self.stop_check_interval), 4096)),
            deterministic=bool(self.deterministic),
        )

class SearchTimer:
    def __init__(self, time_limit_ms, stop_event, check_interval=64):
        self.started_ns = time.perf_counter_ns()
        self.deadline_ns = self.started_ns + max(1, time_limit_ms) * 1_000_000
        self.stop_event = stop_event
        self.check_interval = max(1, check_interval)

    def should_stop(self, work_units, force=False):
        if self.stop_event.is_set():
            return True
        if force or work_units % self.check_interval == 0:
            if time.perf_counter_ns() >= self.deadline_ns:
                self.stop_event.set()
                return True
        return False

    @property
    def elapsed_ms(self):
        return int((time.perf_counter_ns() - self.started_ns) / 1_000_000)

class Bound(Enum):
    EXACT = 0
    LOWER = 1
    UPPER = 2

@dataclass(slots=True)
class TTEntry:
    depth: int
    score: int
    bound: Bound
    move: chess.Move | None
    generation: int

@dataclass(slots=True)
class _Slot:
    key: tuple
    entry: TTEntry

class TranspositionTable:
    def __init__(self, max_entries=131_072):
        requested = max(10_000, int(max_entries))
        size = 1
        while size < requested:
            size <<= 1
        self.size = size
        self._mask = size - 1
        self._table = [None] * size
        self.generation = 0
        self._entries = 0

    @staticmethod
    def key(board):
        return board._transposition_key()

    @staticmethod
    def _index(key, mask):
        h = 0x9E3779B97F4A7C15
        for i, value in enumerate(key):
            x = 0xD6E8FEB86659FD93 if value is None else int(value)
            x &= 0xFFFFFFFFFFFFFFFF
            x ^= (i + 1) * 0xA0761D6478BD642F
            x &= 0xFFFFFFFFFFFFFFFF
            h ^= x
            h = ((h << 7) | (h >> 57)) & 0xFFFFFFFFFFFFFFFF
            h = (h * 0xE7037ED1A0B428D) & 0xFFFFFFFFFFFFFFFF
        return h & mask

    def new_search(self):
        self.generation += 1

    def get(self, board):
        key = self.key(board)
        slot = self._table[self._index(key, self._mask)]
        if slot is not None and slot.key == key:
            return slot.entry
        return None

    def store(self, board, entry):
        key = self.key(board)
        index = self._index(key, self._mask)
        old = self._table[index]
        if old is None:
            self._table[index] = _Slot(key, entry)
            self._entries += 1
            return
        if old.key == key:
            if old.entry.generation == self.generation and old.entry.depth > entry.depth:
                return
            self._table[index] = _Slot(key, entry)
            return
        if old.entry.generation < self.generation or entry.depth >= old.entry.depth:
            self._table[index] = _Slot(key, entry)

    def clear(self):
        self._table = [None] * self.size
        self._entries = 0

    def __len__(self):
        return self._entries

def score_to_tt(score, ply, mate_threshold):
    if score > mate_threshold:
        return score + ply
    if score < -mate_threshold:
        return score - ply
    return score

def score_from_tt(score, ply, mate_threshold):
    if score > mate_threshold:
        return score - ply
    if score < -mate_threshold:
        return score + ply
    return score

class MoveOrderer:
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
        return sorted(list(moves), key=lambda move: self.score(board, move, ply, tt_move, counter_move), reverse=True)

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
        self.history[key] = min(self.history[key] + depth * depth, 1_000_000)
        if previous_move is not None:
            self.countermoves[previous_move] = move

    def record_best_quiet(self, board, move, depth, previous_move=None):
        if not self.is_quiet(board, move):
            return
        key = (board.turn, move)
        self.history[key] = min(self.history[key] + max(1, depth * depth // 2), 1_000_000)
        if previous_move is not None:
            self.countermoves[previous_move] = move

    def counter_move_for(self, previous_move):
        return None if previous_move is None else self.countermoves.get(previous_move)

    def decay(self):
        for key, value in list(self.history.items()):
            decayed = value - value // 16
            if decayed:
                self.history[key] = decayed
            else:
                self.history.pop(key, None)

def validate_board(board):
    if not isinstance(board, chess.Board):
        raise TypeError("Expected chess.Board.")
    if not board.is_valid():
        raise ValueError(f"Invalid board state: {board.status()}")

@dataclass(frozen=True, slots=True)
class TerminalState:
    terminal: bool
    kind: str | None
    score: int

def game_terminal_state(board):
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return TerminalState(False, None, 0)
    if outcome.termination == chess.Termination.CHECKMATE:
        return TerminalState(True, "checkmate", -MATE_SCORE)
    if outcome.termination == chess.Termination.STALEMATE:
        return TerminalState(True, "stalemate", 0)
    return TerminalState(True, outcome.termination.name.lower(), 0)


# ------------------------- evaluation -------------------------

# Precomputed square metadata. Function-call overhead matters because evaluation
# runs at every quiescence leaf.
FILE_OF = tuple(chess.square_file(sq) for sq in chess.SQUARES)
RANK_OF = tuple(chess.square_rank(sq) for sq in chess.SQUARES)
MIRROR_OF = tuple(chess.square_mirror(sq) for sq in chess.SQUARES)

PST = {
    chess.PAWN: [
         0,   0,   0,   0,   0,   0,   0,   0,
        50,  50,  50,  50,  50,  50,  50,  50,
        10,  10,  20,  30,  30,  20,  10,  10,
         5,   5,  10,  25,  25,  10,   5,   5,
         0,   0,   0,  20,  20,   0,   0,   0,
         5,  -5, -10,   0,   0, -10,  -5,   5,
         5,  10,  10, -20, -20,  10,  10,   5,
         0,   0,   0,   0,   0,   0,   0,   0,
    ],
    chess.KNIGHT: [
        -50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20,   0,   0,   0,   0, -20, -40,
        -30,   0,  10,  15,  15,  10,   0, -30,
        -30,   5,  15,  20,  20,  15,   5, -30,
        -30,   0,  15,  20,  20,  15,   0, -30,
        -30,   5,  10,  15,  15,  10,   5, -30,
        -40, -20,   0,   5,   5,   0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50,
    ],
    chess.BISHOP: [
        -20, -10, -10, -10, -10, -10, -10, -20,
        -10,   0,   0,   0,   0,   0,   0, -10,
        -10,   0,   5,  10,  10,   5,   0, -10,
        -10,   5,   5,  10,  10,   5,   5, -10,
        -10,   0,  10,  10,  10,  10,   0, -10,
        -10,  10,  10,  10,  10,  10,  10, -10,
        -10,   5,   0,   0,   0,   0,   5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20,
    ],
    chess.ROOK: [
         0,  0,  0,   5,   5,   0,   0,   0,
         5, 10, 10,  10,  10,  10,  10,   5,
        -5,  0,  0,   0,   0,   0,   0,  -5,
        -5,  0,  0,   0,   0,   0,   0,  -5,
        -5,  0,  0,   0,   0,   0,   0,  -5,
        -5,  0,  0,   0,   0,   0,   0,  -5,
        -5,  0,  0,   0,   0,   0,   0,  -5,
         0,  0,  0,   5,   5,   0,   0,   0,
    ],
    chess.QUEEN: [
        -20, -10, -10,   0,   0, -10, -10, -20,
        -10,   0,   0,   0,   0,   0,   0, -10,
        -10,   0,   5,   5,   5,   5,   0, -10,
          0,   0,   5,   5,   5,   5,   0,  -5,
         -5,   0,   5,   5,   5,   5,   0,  -5,
        -10,   5,   5,   5,   5,   5,   0, -10,
        -10,   0,   5,   0,   0,   0,   0, -10,
        -20, -10, -10,   0,   0, -10, -10, -20,
    ],
    chess.KING: [
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -10, -20, -20, -20, -20, -20, -20, -10,
         20,  20,   0,   0,   0,   0,  20,  20,
         20,  30,  10,   0,   0,  10,  30,  20,
    ],
}

KING_ENDGAME = [
    -50, -40, -30, -20, -20, -30, -40, -50,
    -40, -20,   0,   0,   0,   0, -20, -40,
    -30,   0,  10,  15,  15,  10,   0, -30,
    -20,   0,  15,  20,  20,  15,   0, -20,
    -20,   0,  15,  20,  20,  15,   0, -20,
    -30,   0,  10,  15,  15,  10,   0, -30,
    -40, -20,   0,   0,   0,   0, -20, -40,
    -50, -40, -30, -20, -20, -30, -40, -50,
]

PHASE_WEIGHTS = {
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
}
MAX_PHASE = 24
MOBILE_TYPES = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)

CENTER_MASK = (
    chess.BB_SQUARES[chess.D4]
    | chess.BB_SQUARES[chess.E4]
    | chess.BB_SQUARES[chess.D5]
    | chess.BB_SQUARES[chess.E5]
)

DEV_MASK = {
    chess.WHITE: sum(chess.BB_SQUARES[sq] for sq in (chess.B1, chess.C1, chess.F1, chess.G1)),
    chess.BLACK: sum(chess.BB_SQUARES[sq] for sq in (chess.B8, chess.C8, chess.F8, chess.G8)),
}

# King shield: friendly pawn squares one or two ranks in front of the king.
KING_SHIELD_MASK = {chess.WHITE: [0] * 64, chess.BLACK: [0] * 64}
for sq in chess.SQUARES:
    file_ = FILE_OF[sq]
    rank = RANK_OF[sq]
    for color, direction in ((chess.WHITE, 1), (chess.BLACK, -1)):
        mask = 0
        for df in (-1, 0, 1):
            f = file_ + df
            if not 0 <= f < 8:
                continue
            for step in (1, 2):
                r = rank + direction * step
                if 0 <= r < 8:
                    mask |= chess.BB_SQUARES[chess.square(f, r)]
        KING_SHIELD_MASK[color][sq] = mask

KING_CENTER_TERM = tuple(
    int((7 - (abs(FILE_OF[sq] - 3.5) + abs(RANK_OF[sq] - 3.5))) * 5)
    for sq in chess.SQUARES
)


class _Scan:
    __slots__ = (
        "material",
        "pst",
        "phase",
        "pawns",
        "pawn_counts",
        "rooks",
        "bishop_count",
        "mobile_pieces",
        "king_sq",
    )

    def __init__(self):
        self.material = 0
        self.pst = 0
        self.phase = 0
        self.pawns = {chess.WHITE: [], chess.BLACK: []}
        self.pawn_counts = {chess.WHITE: [0] * 8, chess.BLACK: [0] * 8}
        self.rooks = {chess.WHITE: [], chess.BLACK: []}
        self.bishop_count = {chess.WHITE: 0, chess.BLACK: 0}
        self.mobile_pieces = {chess.WHITE: [], chess.BLACK: []}
        self.king_sq = {chess.WHITE: None, chess.BLACK: None}


def _scan_board(board) -> _Scan:
    scan = _Scan()

    for square, piece in board.piece_map().items():
        pt = piece.piece_type
        color = piece.color
        sign = 1 if color == chess.WHITE else -1

        scan.material += sign * PIECE_VALUES[pt]

        weight = PHASE_WEIGHTS.get(pt)
        if weight:
            scan.phase += weight

        if pt == chess.KING:
            scan.king_sq[color] = square
            continue

        idx = square if color == chess.WHITE else MIRROR_OF[square]
        scan.pst += sign * PST[pt][idx]

        if pt == chess.PAWN:
            scan.pawns[color].append(square)
            scan.pawn_counts[color][FILE_OF[square]] += 1
        elif pt == chess.ROOK:
            scan.rooks[color].append(square)
        elif pt == chess.BISHOP:
            scan.bishop_count[color] += 1

        if pt in MOBILE_TYPES:
            scan.mobile_pieces[color].append((pt, square))

    scan.phase = min(scan.phase, MAX_PHASE)
    return scan


def _king_pst(scan, endgame):
    score = 0
    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        king = scan.king_sq[color]
        if king is None:
            continue
        idx = king if color == chess.WHITE else MIRROR_OF[king]
        table = KING_ENDGAME if endgame else PST[chess.KING]
        score += sign * table[idx]
    return score


def _mobility(board, scan):
    white = sum(
        popcount(board.attacks_mask(sq))
        for _, sq in scan.mobile_pieces[chess.WHITE]
    )
    black = sum(
        popcount(board.attacks_mask(sq))
        for _, sq in scan.mobile_pieces[chess.BLACK]
    )
    return (white - black) * 2


def _king_safety(board, scan):
    score = 0

    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        king = scan.king_sq[color]
        if king is None:
            continue

        shield = popcount(
            board.pawns
            & board.occupied_co[color]
            & KING_SHIELD_MASK[color][king]
        )
        score += sign * shield * 10

        if (
            board.has_kingside_castling_rights(color)
            or board.has_queenside_castling_rights(color)
        ):
            score += sign * 5

        # Direct enemy attacks on the king are strongly negative.
        score -= sign * popcount(board.attackers_mask(not color, king)) * 12

    return score


def _pawn_structure(scan):
    score = 0

    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        pawns = scan.pawns[color]
        if not pawns:
            continue

        counts = scan.pawn_counts[color]
        doubled = sum(count - 1 for count in counts if count > 1)
        score -= sign * 12 * doubled

        isolated = 0
        for file_, count in enumerate(counts):
            if count == 0:
                continue
            left = counts[file_ - 1] if file_ > 0 else 0
            right = counts[file_ + 1] if file_ < 7 else 0
            if left == 0 and right == 0:
                isolated += count
        score -= sign * 10 * isolated

        enemy_counts = scan.pawn_counts[not color]
        enemy_ranks = []
        # For each file, keep only the furthest enemy pawn that matters to
        # the passed-pawn test.
        if color == chess.WHITE:
            enemy_ranks = [-1] * 8
            for square in scan.pawns[not color]:
                f = FILE_OF[square]
                enemy_ranks[f] = max(enemy_ranks[f], RANK_OF[square])

            for square in pawns:
                f = FILE_OF[square]
                r = RANK_OF[square]
                blocked = any(
                    enemy_ranks[nf] > r
                    for nf in (f - 1, f, f + 1)
                    if 0 <= nf < 8
                )
                if not blocked:
                    score += sign * (20 + 8 * r)
        else:
            enemy_ranks = [8] * 8
            for square in scan.pawns[not color]:
                f = FILE_OF[square]
                enemy_ranks[f] = min(enemy_ranks[f], RANK_OF[square])

            for square in pawns:
                f = FILE_OF[square]
                r = RANK_OF[square]
                blocked = any(
                    enemy_ranks[nf] < r
                    for nf in (f - 1, f, f + 1)
                    if 0 <= nf < 8
                )
                if not blocked:
                    score += sign * (20 + 8 * (7 - r))

    return score


def _bishop_pair(scan):
    return 25 * (
        int(scan.bishop_count[chess.WHITE] >= 2)
        - int(scan.bishop_count[chess.BLACK] >= 2)
    )


def _rook_activity(scan):
    score = 0

    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        own_files = scan.pawn_counts[color]
        enemy_files = scan.pawn_counts[not color]

        for square in scan.rooks[color]:
            file_ = FILE_OF[square]

            own_pawn = own_files[file_] > 0
            enemy_pawn = enemy_files[file_] > 0

            if not own_pawn and not enemy_pawn:
                score += sign * 24
            elif not own_pawn:
                score += sign * 12

            if RANK_OF[square] in (1, 6):
                score += sign * 8

    return score


def _development(board):
    score = 0
    occupied = board.occupied

    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        empty = 4 - popcount(occupied & DEV_MASK[color])
        score += sign * empty * 8

        # In the opening, repeatedly shuffling a rook while castling rights
        # are still available is usually a tempo loss. This small positional
        # term breaks shallow-search rook oscillations without overriding a
        # genuine tactical gain.
        if board.has_castling_rights(color):
            if color == chess.WHITE:
                home_rooks = {chess.A1, chess.H1}
                king = chess.E1
                castled = chess.G1 if board.king(color) == chess.G1 else chess.C1
            else:
                home_rooks = {chess.A8, chess.H8}
                king = chess.E8
                castled = chess.G8 if board.king(color) == chess.G8 else chess.C8

            for square in board.pieces(chess.ROOK, color):
                if square not in home_rooks:
                    score -= sign * 18

            if board.king(color) == castled:
                score += sign * 20

        # A queen move before the minor pieces develop is mildly discouraged.
        # The PST and search still allow an early queen move when tactics make
        # it genuinely worthwhile.
        queen_sq = next(iter(board.pieces(chess.QUEEN, color)), None)
        undeveloped_minors = (
            board.piece_at(chess.B1 if color == chess.WHITE else chess.B8) is not None
            or board.piece_at(chess.G1 if color == chess.WHITE else chess.G8) is not None
        )
        if (
            queen_sq is not None
            and color == board.turn
            and undeveloped_minors
            and queen_sq != (chess.D1 if color == chess.WHITE else chess.D8)
        ):
            score -= sign * 8

    return score


def _center_control(board):
    return 8 * (
        popcount(board.occupied_co[chess.WHITE] & CENTER_MASK)
        - popcount(board.occupied_co[chess.BLACK] & CENTER_MASK)
    )


def _endgame_terms(scan):
    if scan.phase > 8:
        return 0

    score = 0
    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        king = scan.king_sq[color]
        if king is not None:
            score += sign * KING_CENTER_TERM[king]
    return score


class IncrementalEvaluator:
    """
    Maintains material + non-king PST incrementally through push/pop.

    Dynamic positional terms remain board-derived, so this class is deliberately
    used as a safe fast-search accelerator rather than as a second definition
    of the complete evaluator. Differential tests compare its base score
    against a fresh board scan over random legal games.
    """

    __slots__ = ("base_score", "phase", "_stack")

    def __init__(self, board):
        scan = _scan_board(board)
        self.base_score = scan.material + scan.pst
        self.phase = scan.phase
        self._stack = []

    @staticmethod
    def _piece_value_pst(piece, square):
        sign = 1 if piece.color == chess.WHITE else -1
        value = PIECE_VALUES[piece.piece_type]

        # _scan_board intentionally excludes king PST from scan.pst because
        # king-square scoring is phase-dependent and recomputed dynamically.
        if piece.piece_type != chess.KING:
            idx = square if piece.color == chess.WHITE else MIRROR_OF[square]
            value += PST[piece.piece_type][idx]

        return sign * value

    @staticmethod
    def _phase_weight(piece_type):
        return PHASE_WEIGHTS.get(piece_type, 0)

    def push(self, board, move):
        moving = board.piece_at(move.from_square)
        if moving is None:
            raise ValueError(f"No moving piece on {chess.square_name(move.from_square)}")

        delta = 0
        phase_delta = 0

        # Remove the moving piece from its origin.
        delta -= self._piece_value_pst(moving, move.from_square)

        # Capture removal, including en-passant's off-target pawn.
        captured_square = move.to_square
        captured = board.piece_at(move.to_square)
        if captured is None and board.is_en_passant(move):
            captured_square += -8 if moving.color == chess.WHITE else 8
            captured = board.piece_at(captured_square)

        if captured is not None:
            delta -= self._piece_value_pst(captured, captured_square)
            phase_delta -= self._phase_weight(captured.piece_type)

        # Destination piece: normal move or promoted piece.
        destination_piece = moving
        if move.promotion:
            destination_piece = chess.Piece(move.promotion, moving.color)
            phase_delta += self._phase_weight(move.promotion)

        delta += self._piece_value_pst(destination_piece, move.to_square)

        # Promotion replaces a pawn, so remove the pawn phase (zero) and add
        # the promoted piece phase already handled above.
        if moving.piece_type != chess.PAWN and not move.promotion:
            phase_delta += 0

        # Castling also moves a rook.
        if moving.piece_type == chess.KING and abs(
            move.to_square - move.from_square
        ) == 2:
            if move.to_square > move.from_square:
                rook_from = move.from_square + 3
                rook_to = move.from_square + 1
            else:
                rook_from = move.from_square - 4
                rook_to = move.from_square - 1

            rook = board.piece_at(rook_from)
            if rook is not None:
                delta -= self._piece_value_pst(rook, rook_from)
                delta += self._piece_value_pst(rook, rook_to)

        self.base_score += delta
        self.phase = max(0, min(MAX_PHASE, self.phase + phase_delta))
        self._stack.append((delta, phase_delta))

    def push_null(self):
        self._stack.append((0, 0))

    def pop(self):
        delta, phase_delta = self._stack.pop()
        self.base_score -= delta
        self.phase -= phase_delta


def _fast_pawn_data(board):
    score = 0
    counts_by_color = {
        chess.WHITE: [0] * 8,
        chess.BLACK: [0] * 8,
    }
    pawn_info = {
        chess.WHITE: [],
        chess.BLACK: [],
    }

    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        pawns = board.pawns & board.occupied_co[color]
        while pawns:
            bit = pawns & -pawns
            square = bit.bit_length() - 1
            pawns ^= bit
            file_ = FILE_OF[square]
            rank = RANK_OF[square]
            counts_by_color[color][file_] += 1
            pawn_info[color].append((file_, rank))

        counts = counts_by_color[color]
        doubled = sum(count - 1 for count in counts if count > 1)
        score -= sign * 12 * doubled

        isolated = 0
        for file_, count in enumerate(counts):
            if count == 0:
                continue
            left = counts[file_ - 1] if file_ > 0 else 0
            right = counts[file_ + 1] if file_ < 7 else 0
            if left == 0 and right == 0:
                isolated += count
        score -= sign * 10 * isolated

    # Passed pawns.
    white_enemy_max = [-1] * 8
    for file_, rank in pawn_info[chess.BLACK]:
        white_enemy_max[file_] = max(white_enemy_max[file_], rank)
    for file_, rank in pawn_info[chess.WHITE]:
        if not any(
            white_enemy_max[nf] > rank
            for nf in (file_ - 1, file_, file_ + 1)
            if 0 <= nf < 8
        ):
            score += 20 + 8 * rank

    black_enemy_min = [8] * 8
    for file_, rank in pawn_info[chess.WHITE]:
        black_enemy_min[file_] = min(black_enemy_min[file_], rank)
    for file_, rank in pawn_info[chess.BLACK]:
        if not any(
            black_enemy_min[nf] < rank
            for nf in (file_ - 1, file_, file_ + 1)
            if 0 <= nf < 8
        ):
            score -= 20 + 8 * (7 - rank)

    return score, counts_by_color


def _cheap_rook_activity(board, counts_by_color):
    score = 0
    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        rooks = board.rooks & board.occupied_co[color]
        own = counts_by_color[color]
        enemy = counts_by_color[not color]

        while rooks:
            bit = rooks & -rooks
            square = bit.bit_length() - 1
            rooks ^= bit
            file_ = FILE_OF[square]

            if own[file_] == 0 and enemy[file_] == 0:
                score += sign * 24
            elif own[file_] == 0:
                score += sign * 12

            if RANK_OF[square] in (1, 6):
                score += sign * 8
    return score


def _cheap_king_safety(board):
    score = 0
    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        king = board.king(color)
        if king is None:
            continue

        shield = popcount(
            board.pawns
            & board.occupied_co[color]
            & KING_SHIELD_MASK[color][king]
        )
        score += sign * shield * 8

        if (
            board.has_kingside_castling_rights(color)
            or board.has_queenside_castling_rights(color)
        ):
            score += sign * 5

    return score



def evaluate_incremental_fast(board, evaluator):
    """
    Fast search evaluation using the incrementally maintained material/PST.

    It deliberately avoids piece_map(), attacks_mask() and attackers_mask().
    Those expensive maps remain in the full evaluator used outside the deepest
    search loop.
    """
    score = evaluator.base_score

    score += 25 * (
        int(popcount(board.bishops & board.occupied_co[chess.WHITE]) >= 2)
        - int(popcount(board.bishops & board.occupied_co[chess.BLACK]) >= 2)
    )

    endgame = evaluator.phase <= 8
    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        king = board.king(color)
        if king is not None:
            idx = king if color == chess.WHITE else MIRROR_OF[king]
            table = KING_ENDGAME if endgame else PST[chess.KING]
            score += sign * table[idx]

    score += _cheap_king_safety(board)

    pawn_score, counts = _fast_pawn_data(board)
    score += pawn_score
    score += _cheap_rook_activity(board, counts)
    score += _development(board)
    score += _center_control(board)
    score += _endgame_terms_from_phase(board, evaluator.phase)

    score += 8 if board.turn == chess.WHITE else -8
    return int(score)


def _endgame_terms_from_phase(board, phase):
    if phase > 8:
        return 0

    score = 0
    for color, sign in ((chess.WHITE, 1), (chess.BLACK, -1)):
        king = board.king(color)
        if king is not None:
            score += sign * KING_CENTER_TERM[king]
    return score

def _base_score(board, scan):
    endgame = scan.phase <= 8
    return (
        scan.material
        + scan.pst
        + _king_pst(scan, endgame)
        + _pawn_structure(scan)
        + _bishop_pair(scan)
        + _rook_activity(scan)
        + _development(board)
        + _center_control(board)
        + _endgame_terms(scan)
    )


def evaluate_fast(board):
    """
    Fast static evaluation for quiescence.

    Mobility and direct king-attack maps are the two most expensive parts of
    the full evaluator. Quiescence visits many more nodes than the principal
    search, so it uses this cheaper tactical evaluator while retaining
    material, PST, pawn structure, rook/bishop terms, development, center and
    endgame king activity.
    """
    scan = _scan_board(board)
    score = _base_score(board, scan)
    score += _cheap_king_safety(board)
    score += 8 if board.turn == chess.WHITE else -8
    return int(score)


def evaluate(board):
    """
    Full static evaluation, from White's point of view.

    Callers must not pass terminal positions.
    """
    scan = _scan_board(board)
    score = _base_score(board, scan)
    score += _mobility(board, scan)
    score += _king_safety(board, scan)
    score += 8 if board.turn == chess.WHITE else -8
    return int(score)


# ------------------------- search -----------------------------
# Search tuning. These are deliberately conservative so that the engine gains
# speed without turning tactical correctness into a gamble.
NULL_MOVE_MIN_DEPTH = 3
LMR_MIN_DEPTH = 3
LMR_MIN_MOVE_INDEX = 3
ASPIRATION_MIN_DEPTH = 3
ASPIRATION_WINDOW = 30

CHECK_EXTENSION_MAX_DEPTH = 8
MAX_QUIESCENCE_CHECK_DEPTH = 16
DELTA_MARGIN = 80


@dataclass(slots=True)
class SearchStats:
    nodes: int = 0
    qnodes: int = 0
    tt_hits: int = 0
    tt_cutoffs: int = 0
    cutoffs: int = 0
    root_moves_checked: int = 0
    completed_depth: int = 0
    elapsed_ms: int = 0
    null_move_prunes: int = 0
    lmr_reductions: int = 0
    aspiration_researches: int = 0
    check_extensions: int = 0
    q_delta_prunes: int = 0

    @property
    def total_nodes(self):
        return self.nodes + self.qnodes

    @property
    def nps(self):
        if self.elapsed_ms <= 0:
            return 0
        return int(self.total_nodes * 1000 / self.elapsed_ms)


@dataclass(slots=True)
class SearchContext:
    timer: SearchTimer
    evaluator: IncrementalEvaluator
    stats: SearchStats = field(default_factory=SearchStats)
    stop_event: Event = field(default_factory=Event)
    repetition_counts: dict = field(default_factory=dict)


def _has_non_pawn_material(board, color):
    """O(1) guard against null-move pruning in pawn/king zugzwang positions."""
    return bool(board.occupied_co[color] & ~(board.pawns | board.kings))


def _has_legal_move(board):
    return any(board.generate_legal_moves())

def _build_repetition_counts(board):
    """Count real game positions from the root move stack."""
    counts = defaultdict(int)
    work = board.copy(stack=True)
    while True:
        counts[work._transposition_key()] += 1
        if not work.move_stack:
            break
        work.pop()
    return counts


class Searcher:
    def __init__(self, tt, orderer, quiescence_depth=8, check_interval=128):
        self.tt = tt
        self.orderer = orderer
        self.quiescence_depth = quiescence_depth
        self.check_interval = check_interval
        self.context = None

    def request_stop(self):
        if self.context is not None:
            self.context.stop_event.set()

    def _tt_safe(self, board, depth):
        """
        A TT entry may ignore halfmove_clock only while a 75-move terminal
        cannot be reached inside the stored/current search horizon.
        """
        return board.halfmove_clock + max(0, depth) < 150

    def search(self, board, max_depth, time_limit_ms):
        stop_event = Event()
        timer = SearchTimer(time_limit_ms, stop_event, self.check_interval)
        self.context = SearchContext(
            timer=timer,
            evaluator=IncrementalEvaluator(board),
            stop_event=stop_event,
            repetition_counts=_build_repetition_counts(board),
        )
        self.tt.new_search()
        self.orderer.decay()

        root_moves = self.orderer.ordered(board, board.legal_moves, 0, None)
        if not root_moves:
            return None, 0, []

        best_move = root_moves[0]
        best_score = self._side_to_move_eval(board)
        best_pv = [best_move]

        window = ASPIRATION_WINDOW

        for depth in range(1, max_depth + 1):
            if timer.should_stop(self.context.stats.total_nodes, force=True):
                break

            if depth < ASPIRATION_MIN_DEPTH:
                score, move, _ = self._root(board, depth, -INF, INF)
            else:
                alpha = max(-INF, best_score - window)
                beta = min(INF, best_score + window)
                attempts = 0

                while True:
                    score, move, _ = self._root(board, depth, alpha, beta)
                    if stop_event.is_set() or move is None:
                        break

                    if score <= alpha and alpha > -INF:
                        attempts += 1
                        self.context.stats.aspiration_researches += 1
                        alpha = max(-INF, alpha - window * (2 ** attempts))
                        continue

                    if score >= beta and beta < INF:
                        attempts += 1
                        self.context.stats.aspiration_researches += 1
                        beta = min(INF, beta + window * (2 ** attempts))
                        continue

                    break

            if stop_event.is_set() or move is None:
                break

            best_move = move
            best_score = score
            self.context.stats.completed_depth = depth

            # PV extraction happens once per completed iteration, not on every
            # internal TT cutoff.
            extracted = self._extract_pv(board, depth)
            best_pv = extracted if extracted else [best_move]

            if abs(best_score) >= MATE_THRESHOLD:
                break

        self.context.stats.elapsed_ms = timer.elapsed_ms
        return best_move, best_score, best_pv

    def _root_repetition_adjustment(self, board, move):
        penalty = 0
        if len(board.move_stack) >= 2:
            previous_same_side_move = board.move_stack[-2]
            if (
                move.from_square == previous_same_side_move.to_square
                and move.to_square == previous_same_side_move.from_square
                and move.promotion == previous_same_side_move.promotion
                and move.promotion is None
            ):
                penalty -= 45

        if not board.move_stack:
            return penalty

        board.push(move)
        try:
            prior = self.context.repetition_counts.get(board._transposition_key(), 0)
            if prior >= 2:
                return 0
            if prior == 1:
                return penalty - 18
            return penalty
        finally:
            board.pop()

    def _root(self, board, depth, alpha, beta):
        original_alpha = alpha

        tt_move = None
        if self._tt_safe(board, depth):
            entry = self.tt.get(board)
            if entry is not None:
                tt_move = entry.move

        moves = self.orderer.ordered(board, board.legal_moves, 0, tt_move)
        if not moves:
            if board.is_check():
                return -MATE_SCORE, None, []
            return 0, None, []

        best_score = -INF
        best_move = moves[0]
        best_pv = []
        first = True

        for move in moves:
            self.context.stats.root_moves_checked += 1
            if self.context.timer.should_stop(self.context.stats.total_nodes, force=True):
                return 0, None, []

            self.context.evaluator.push(board, move)
            board.push(move)
            child_key = board._transposition_key()
            prior_count = self.context.repetition_counts.get(child_key, 0)
            self.context.repetition_counts[child_key] = prior_count + 1
            try:
                if prior_count + 1 >= 3:
                    child_score, child_pv = 0, []
                elif first:
                    child_score, child_pv = self._negamax(
                        board, depth - 1, -beta, -alpha, 1, move
                    )
                else:
                    child_score, child_pv = self._negamax(
                        board, depth - 1, -alpha - 1, -alpha, 1, move
                    )
                    if (
                        not self.context.stop_event.is_set()
                        and -child_score > alpha
                    ):
                        child_score, child_pv = self._negamax(
                            board, depth - 1, -beta, -alpha, 1, move
                        )

                score = 0 if prior_count + 1 >= 3 else -child_score
            finally:
                self.context.repetition_counts[child_key] -= 1
                if self.context.repetition_counts[child_key] <= 0:
                    self.context.repetition_counts.pop(child_key, None)
                board.pop()
                self.context.evaluator.pop()

            if self.context.stop_event.is_set():
                return 0, None, []

            score += self._root_repetition_adjustment(board, move)

            if score > best_score:
                best_score = score
                best_move = move
                best_pv = [move] + child_pv

            if score > alpha:
                alpha = score

            if alpha >= beta:
                self.context.stats.cutoffs += 1
                self.orderer.record_cutoff(
                    board, move, 0, depth, None
                )
                break

            first = False

        if best_score <= original_alpha:
            bound = Bound.UPPER
        elif best_score >= beta:
            bound = Bound.LOWER
        else:
            bound = Bound.EXACT

        if self._tt_safe(board, depth) and self.context.repetition_counts.get(board._transposition_key(), 0) <= 1:
            self.tt.store(
                board,
                TTEntry(
                    depth=depth,
                    score=score_to_tt(best_score, 0, MATE_THRESHOLD),
                    bound=bound,
                    move=best_move,
                    generation=self.tt.generation,
                ),
            )

        return best_score, best_move, best_pv

    def _negamax(self, board, depth, alpha, beta, ply, previous_move=None):
        self.context.stats.nodes += 1

        if self.context.timer.should_stop(self.context.stats.total_nodes):
            return 0, []

        # Automatic draw conditions are cheaper than generating moves and can
        # be checked before the legal-move pass.
        if board.halfmove_clock >= 150 or board.is_insufficient_material():
            return 0, []

        if depth <= 0:
            return self._quiescence(board, alpha, beta, 0), []

        in_check = board.is_check()
        original_alpha = alpha

        # A TT entry is safe to reuse only when a 75-move draw cannot be
        # reached within the current horizon.
        current_rep = self.context.repetition_counts.get(board._transposition_key(), 0)
        tt_entry = self.tt.get(board) if self._tt_safe(board, depth) and current_rep <= 1 else None
        tt_move = tt_entry.move if tt_entry else None

        if tt_entry is not None and tt_entry.depth >= depth:
            self.context.stats.tt_hits += 1
            tt_score = score_from_tt(tt_entry.score, ply, MATE_THRESHOLD)

            if tt_entry.bound == Bound.EXACT:
                self.context.stats.tt_cutoffs += 1
                return tt_score, []

            if tt_entry.bound == Bound.LOWER:
                alpha = max(alpha, tt_score)
            elif tt_entry.bound == Bound.UPPER:
                beta = min(beta, tt_score)

            if alpha >= beta:
                self.context.stats.tt_cutoffs += 1
                return tt_score, []

        # Null move pruning.
        if (
            depth >= NULL_MOVE_MIN_DEPTH
            and not in_check
            and beta < MATE_THRESHOLD
            and _has_non_pawn_material(board, board.turn)
        ):
            reduction = 3 if depth >= 6 else 2

            board.push(chess.Move.null())
            self.context.evaluator.push_null()
            try:
                null_score, _ = self._negamax(
                    board,
                    depth - 1 - reduction,
                    -beta,
                    -beta + 1,
                    ply + 1,
                    None,
                )
            finally:
                board.pop()
                self.context.evaluator.pop()

            null_score = -null_score

            if self.context.stop_event.is_set():
                return 0, []

            if null_score >= beta:
                self.context.stats.cutoffs += 1
                self.context.stats.null_move_prunes += 1
                return beta, []

        # Generate the legal moves once. The previous implementation first
        # called search_terminal_state(any(legal_moves)) and then generated
        # the same legal move list again for ordering.
        moves = list(board.legal_moves)
        if not moves:
            if in_check:
                return -MATE_SCORE + ply, []
            return 0, []

        ordered = self.orderer.ordered(
            board,
            moves,
            ply,
            tt_move,
            self.orderer.counter_move_for(previous_move),
        )

        best_score = -INF
        best_move = ordered[0]
        best_pv = []
        first = True
        move_index = 0

        for move in ordered:
            move_index += 1

            is_capture = board.is_capture(move)
            is_quiet = not (is_capture or move.promotion)

            history_value = self.orderer.history_score(board, move)

            self.context.evaluator.push(board, move)
            board.push(move)
            child_key = board._transposition_key()
            prior_count = self.context.repetition_counts.get(child_key, 0)
            self.context.repetition_counts[child_key] = prior_count + 1
            try:
                if prior_count + 1 >= 3:
                    score, child_pv = 0, []
                else:
                    gives_check = board.is_check()
                    extension = (
                        1
                        if gives_check and depth <= CHECK_EXTENSION_MAX_DEPTH
                        else 0
                    )
                    if extension:
                        self.context.stats.check_extensions += 1

                    full_depth = depth - 1 + extension

                    if first:
                        child_score, child_pv = self._negamax(
                            board,
                            full_depth,
                            -beta,
                            -alpha,
                            ply + 1,
                            move,
                        )
                    else:
                        reduction = 0
                        if (
                            depth >= LMR_MIN_DEPTH
                            and move_index > LMR_MIN_MOVE_INDEX
                            and is_quiet
                            and not gives_check
                        ):
                            if history_value < 4_000:
                                if depth >= 7 and move_index >= 10:
                                    reduction = 2
                                else:
                                    reduction = 1

                        if reduction:
                            self.context.stats.lmr_reductions += 1

                        search_depth = max(0, full_depth - reduction)
                        child_score, child_pv = self._negamax(
                            board,
                            search_depth,
                            -alpha - 1,
                            -alpha,
                            ply + 1,
                            move,
                        )

                        if (
                            not self.context.stop_event.is_set()
                            and -child_score > alpha
                        ):
                            if reduction:
                                child_score, child_pv = self._negamax(
                                    board,
                                    full_depth,
                                    -alpha - 1,
                                    -alpha,
                                    ply + 1,
                                    move,
                                )

                            if (
                                not self.context.stop_event.is_set()
                                and -child_score > alpha
                            ):
                                child_score, child_pv = self._negamax(
                                    board,
                                    full_depth,
                                    -beta,
                                    -alpha,
                                    ply + 1,
                                    move,
                                )

                if prior_count + 1 < 3:
                    score = -child_score
            finally:
                self.context.repetition_counts[child_key] -= 1
                if self.context.repetition_counts[child_key] <= 0:
                    self.context.repetition_counts.pop(child_key, None)
                board.pop()
                self.context.evaluator.pop()
            if self.context.stop_event.is_set():
                return 0, []

            if score > best_score:
                best_score = score
                best_move = move
                best_pv = [move] + child_pv

            if score > alpha:
                alpha = score

            if alpha >= beta:
                self.context.stats.cutoffs += 1
                self.orderer.record_cutoff(
                    board, move, ply, depth, previous_move
                )
                break

            if is_quiet and score > original_alpha:
                self.orderer.record_best_quiet(
                    board, move, depth, previous_move
                )

            first = False

        if best_score <= original_alpha:
            bound = Bound.UPPER
        elif best_score >= beta:
            bound = Bound.LOWER
        else:
            bound = Bound.EXACT

        if self._tt_safe(board, depth) and self.context.repetition_counts.get(board._transposition_key(), 0) <= 1:
            self.tt.store(
                board,
                TTEntry(
                    depth=depth,
                    score=score_to_tt(best_score, ply, MATE_THRESHOLD),
                    bound=bound,
                    move=best_move,
                    generation=self.tt.generation,
                ),
            )

        return best_score, best_pv

    @staticmethod
    def _capture_gain(board, move):
        victim = board.piece_at(move.to_square)
        if victim is None and board.is_en_passant(move):
            gain = PIECE_VALUES[chess.PAWN]
        elif victim is None:
            gain = 0
        else:
            gain = PIECE_VALUES[victim.piece_type]

        if move.promotion:
            gain += PIECE_VALUES.get(move.promotion, 0) - PIECE_VALUES[chess.PAWN]

        return gain

    def _quiescence(self, board, alpha, beta, qdepth):
        self.context.stats.qnodes += 1

        if self.context.timer.should_stop(self.context.stats.total_nodes):
            return 0

        if board.halfmove_clock >= 150 or board.is_insufficient_material():
            return 0

        in_check = board.is_check()

        # When in check, stand-pat is illegal. Search all legal evasions and
        # continue until the king is safe. A bounded emergency fallback keeps
        # a pathological checking sequence from becoming unbounded.
        if in_check:
            moves = list(board.legal_moves)
            if not moves:
                return -MATE_SCORE

            if qdepth >= MAX_QUIESCENCE_CHECK_DEPTH:
                # Still in check at the emergency ceiling: choose a legal
                # evasion and evaluate the resulting non-terminal position.
                best = -INF
                for move in moves:
                    board.push(move)
                    try:
                        if not board.is_check():
                            best = max(best, -self._side_to_move_eval(board))
                    finally:
                        board.pop()
                return best if best != -INF else -MATE_SCORE // 2

            best = -INF
            ordered = self.orderer.ordered(board, moves, qdepth, None)
            for move in ordered:
                self.context.evaluator.push(board, move)
                board.push(move)
                try:
                    score = -self._quiescence(
                        board, -beta, -alpha, qdepth + 1
                    )
                finally:
                    board.pop()
                    self.context.evaluator.pop()

                if self.context.stop_event.is_set():
                    return 0

                if score > best:
                    best = score

                if score >= beta:
                    return score
                if score > alpha:
                    alpha = score

            return best

        stand_pat = self._side_to_move_eval(board, fast=True)

        if stand_pat >= beta:
            return stand_pat
        if stand_pat > alpha:
            alpha = stand_pat

        if qdepth >= self.quiescence_depth:
            return stand_pat

        # Captures and promotions only. A quiet position with no tactical
        # moves is normally a q-leaf; stalemate is checked only in that rare
        # case so ordinary q-nodes avoid a second full legal-move generation.
        moves = self._quiescence_moves(board)
        if not moves:
            if not _has_legal_move(board):
                return 0
            return stand_pat

        ordered = self.orderer.ordered(board, moves, qdepth, None)

        for move in ordered:
            # Delta pruning: when the maximum plausible material swing still
            # cannot reach alpha, don't spend a q-node on the capture.
            if board.is_capture(move) and not move.promotion:
                gain = self._capture_gain(board, move)
                if stand_pat + gain + DELTA_MARGIN < alpha:
                    self.context.stats.q_delta_prunes += 1
                    continue

            self.context.evaluator.push(board, move)
            board.push(move)
            try:
                score = -self._quiescence(
                    board, -beta, -alpha, qdepth + 1
                )
            finally:
                board.pop()
                self.context.evaluator.pop()

            if self.context.stop_event.is_set():
                return 0

            if score >= beta:
                return score
            if score > alpha:
                alpha = score

        return alpha

    @staticmethod
    def _quiescence_moves(board):
        # Only legal captures and promotions. Checking quiet moves are
        # intentionally omitted to keep q-search narrow and fast.
        moves = list(board.generate_legal_captures())

        promo_rank = (
            chess.BB_RANK_7 if board.turn == chess.WHITE else chess.BB_RANK_2
        )
        pawn_mask = board.pawns & board.occupied_co[board.turn] & promo_rank
        if pawn_mask:
            for move in board.generate_legal_moves(from_mask=pawn_mask):
                if move.promotion and not board.is_capture(move):
                    moves.append(move)

        return moves

    def _side_to_move_eval(self, board, fast=False):
        if fast and self.context is not None:
            white_score = evaluate_incremental_fast(
                board, self.context.evaluator
            )
        else:
            evaluator = evaluate_fast if fast else evaluate
            white_score = evaluator(board)
        return white_score if board.turn == chess.WHITE else -white_score

    def _extract_pv(self, board, depth):
        """Reconstruct the final PV from TT moves only once per completed depth."""
        pv = []
        seen = set()

        for _ in range(max(0, depth)):
            key = self.tt.key(board)
            if key in seen:
                break
            seen.add(key)

            entry = self.tt.get(board)
            if entry is None or entry.move is None:
                break

            move = entry.move
            if move not in board.legal_moves:
                break

            pv.append(move)
            board.push(move)

        for _ in pv:
            board.pop()

        return pv


# ------------------------- engine wrapper --------------------


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

    def analyze(self, board: chess.Board, max_depth: int | None = None, time_limit_ms: int | None = None) -> EngineResponse:
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
            max_depth=max_depth if max_depth is not None else self.config.max_depth,
            time_limit_ms=time_limit_ms if time_limit_ms is not None else self.config.time_limit_ms,
        )

        if move is None or move not in work.legal_moves:
            raise RuntimeError("ChessMind returned an invalid move.")

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



# ========================= WEB/API LAYER =========================

AI_DIR = Path(__file__).resolve().parent
SITE_ROOT = AI_DIR.parent
HOST = os.environ.get("CHESSMIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("CHESSMIND_PORT", "8080")))
ENGINE_LOCK = threading.RLock()
ENGINE = ChessEngine(EngineConfig(max_depth=18, time_limit_ms=2500, transposition_size=131_072, quiescence_depth=10, stop_check_interval=128))


def _history_list(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def board_from_request(fen: str | None, history_uci) -> chess.Board:
    """Rebuild the real game stack when the client supplies move history.

    This is the key fix for the website repetition bug: a plain FEN contains
    the current position, but not the previous positions needed for
    threefold-repetition detection. The web client therefore sends the UCI
    move stack as well as the current FEN.
    """
    history = _history_list(history_uci)
    if history:
        board = chess.Board()
        for uci in history:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError(f"Invalid history move: {uci}")
            board.push(move)
        if fen and fen != "start":
            expected = chess.Board(fen.strip())
            if board.fen() != expected.fen():
                raise ValueError("FEN does not match the supplied move history.")
        return board

    if not fen or fen == "start":
        return chess.Board()
    board = chess.Board(fen.strip())
    if not board.is_valid():
        raise ValueError(f"Invalid chess position: {board.status()}")
    return board


def history_san(board: chess.Board) -> list[str]:
    if not board.move_stack:
        return []
    root = chess.Board()
    out = []
    for move in board.move_stack:
        if move not in root.legal_moves:
            break
        out.append(root.san(move))
        root.push(move)
    return out


def repetition_count(board: chess.Board) -> int:
    target = board._transposition_key()
    work = board.copy(stack=True)
    count = 0
    while True:
        if work._transposition_key() == target:
            count += 1
        if not work.move_stack:
            break
        work.pop()
    return count


def state_payload(board: chess.Board, last_move: str | None = None, analysis: dict | None = None) -> dict:
    outcome = board.outcome(claim_draw=True)
    terminal = outcome is not None
    kind = outcome.termination.name.lower() if outcome else None
    message = None
    if outcome:
        if outcome.termination == chess.Termination.CHECKMATE:
            winner = "White" if outcome.winner == chess.WHITE else "Black"
            message = f"Checkmate — {winner} wins."
        elif outcome.termination == chess.Termination.STALEMATE:
            message = "Game over — stalemate."
        elif outcome.termination == chess.Termination.THREEFOLD_REPETITION:
            message = "Game over — threefold repetition."
        elif outcome.termination == chess.Termination.FIFTY_MOVES:
            message = "Game over — fifty-move draw."
        elif outcome.termination == chess.Termination.SEVENTYFIVE_MOVES:
            message = "Game over — seventy-five-move draw."
        elif outcome.termination == chess.Termination.INSUFFICIENT_MATERIAL:
            message = "Game over — insufficient material."
        else:
            message = f"Game over — {kind.replace('_', ' ').title()}."

    payload = {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "check": board.is_check(),
        "terminal": terminal,
        "terminal_kind": kind,
        "terminal_message": message,
        "legal_moves": [m.uci() for m in board.legal_moves],
        "history": history_san(board),
        "history_uci": [m.uci() for m in board.move_stack],
        "last_move": last_move,
        "repetition_count": repetition_count(board),
        "analysis": analysis,
    }
    return payload


def safe_engine_analysis(board: chess.Board, depth: int, time_ms: int) -> dict:
    configs = [
        (max(1, min(depth, 18)), max(100, min(time_ms, 120000))),
        (max(1, min(depth, 10)), max(150, min(time_ms, 1200))),
    ]
    last_error = None
    for use_depth, use_time in configs:
        try:
            work = board.copy(stack=True)
            with ENGINE_LOCK:
                response = ENGINE.analyze(work, max_depth=use_depth, time_limit_ms=use_time)
            if response.best_move_uci:
                candidate = chess.Move.from_uci(response.best_move_uci)
                if candidate in work.legal_moves:
                    return {
                        "ok": True,
                        "best_move_uci": candidate.uci(),
                        "best_move_san": work.san(candidate),
                        "evaluation": response.evaluation,
                        "depth": response.depth,
                        "nodes": response.nodes,
                        "elapsed_ms": response.elapsed_ms,
                        "principal_variation": response.principal_variation,
                        "is_mate": response.is_mate,
                        "terminal": response.terminal,
                        "terminal_kind": response.terminal_kind,
                        "qnodes": response.qnodes,
                        "tt_hits": response.tt_hits,
                        "tt_cutoffs": response.tt_cutoffs,
                        "cutoffs": response.cutoffs,
                        "nps": response.nps,
                        "fallback": False,
                    }
            if response.terminal:
                return {
                    "ok": True,
                    "best_move_uci": None,
                    "best_move_san": None,
                    "evaluation": response.evaluation,
                    "depth": response.depth,
                    "nodes": response.nodes,
                    "elapsed_ms": response.elapsed_ms,
                    "principal_variation": response.principal_variation,
                    "is_mate": response.is_mate,
                    "terminal": True,
                    "terminal_kind": response.terminal_kind,
                    "qnodes": response.qnodes,
                    "tt_hits": response.tt_hits,
                    "tt_cutoffs": response.tt_cutoffs,
                    "cutoffs": response.cutoffs,
                    "nps": response.nps,
                    "fallback": False,
                }
            raise RuntimeError("ChessMind produced no legal move.")
        except Exception as exc:
            last_error = exc

    legal_moves = list(board.legal_moves)
    if not legal_moves:
        return {"ok": True, "best_move_uci": None, "best_move_san": None, "terminal": True, "terminal_kind": "terminal", "fallback": True, "error": str(last_error) if last_error else None}
    move = legal_moves[0]
    return {
        "ok": True,
        "best_move_uci": move.uci(),
        "best_move_san": board.san(move),
        "evaluation": 0.0,
        "depth": 0,
        "nodes": 0,
        "elapsed_ms": 0,
        "principal_variation": [move.uci()],
        "is_mate": False,
        "terminal": False,
        "terminal_kind": None,
        "qnodes": 0,
        "tt_hits": 0,
        "tt_cutoffs": 0,
        "cutoffs": 0,
        "nps": 0,
        "fallback": True,
        "error": str(last_error) if last_error else None,
    }


class Handler(SimpleHTTPRequestHandler):
    server_version = "SuyashChessMind/2.0"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def send_json(self, payload, status=200):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_json({"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/chessmind/health":
                self.send_json({
                    "ok": True,
                    "engine": "ChessMind Core 2.0 Refined",
                    "python": sys.version.split()[0],
                    "features": ["anti-repetition", "mate-first", "shared-transposition-table", "AI-vs-AI", "fixed-square-board"],
                })
                return
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")

            if path == "/api/chessmind/state":
                board = board_from_request(body.get("fen", "start"), body.get("history_uci", []))
                self.send_json(state_payload(board, body.get("last_move")))
                return

            if path == "/api/chessmind/play":
                board = board_from_request(body.get("fen", "start"), body.get("history_uci", []))
                uci = str(body.get("uci", "")).strip()
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(f"Illegal move: {uci}")
                san = board.san(move)
                board.push(move)
                self.send_json(state_payload(board, move.uci()))
                return

            if path == "/api/chessmind/analyze":
                board = board_from_request(body.get("fen", "start"), body.get("history_uci", []))
                depth = max(1, min(18, int(body.get("depth", 10))))
                time_ms = max(100, min(120000, int(body.get("time_ms", 2500))))
                result = safe_engine_analysis(board, depth, time_ms)
                self.send_json(result)
                return

            self.send_json({"error": "Unknown API endpoint."}, 404)
        except json.JSONDecodeError:
            self.send_json({"error": "Request body must be valid JSON."}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def log_message(self, fmt, *args):
        print(f"[ChessMind] {self.address_string()} - {fmt % args}")


def main():
    os.chdir(SITE_ROOT)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"[ERROR] Could not start server on {HOST}:{PORT}: {exc}")
        print("Try: set CHESSMIND_PORT=8081")
        raise SystemExit(1) from exc

    print("==========================================")
    print("  Suyash Portfolio + ChessMind AI 2.0")
    print("==========================================")
    print(f"Portfolio : http://{HOST}:{PORT}/")
    print(f"ChessMind : http://{HOST}:{PORT}/AI/chessmind/web/index.html")
    print("Engine    : refined anti-repetition + deep search")
    print("Press Ctrl+C to stop.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
