import chess
from chess import popcount

from .constants import PIECE_VALUES

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
