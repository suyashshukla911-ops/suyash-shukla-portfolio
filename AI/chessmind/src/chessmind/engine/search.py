from dataclasses import dataclass, field
from threading import Event

import chess

from .constants import INF, MATE_SCORE, MATE_THRESHOLD, PIECE_VALUES
from .evaluation import evaluate, evaluate_fast, evaluate_incremental_fast, IncrementalEvaluator
from .time_manager import SearchTimer
from .transposition import (
    Bound,
    TTEntry,
    TranspositionTable,
    score_from_tt,
    score_to_tt,
)

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


def _has_non_pawn_material(board, color):
    """O(1) guard against null-move pruning in pawn/king zugzwang positions."""
    return bool(board.occupied_co[color] & ~(board.pawns | board.kings))


def _has_legal_move(board):
    return any(board.generate_legal_moves())


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
        """
        Game-history-aware root treatment.

        Search intentionally does not carry the whole repetition stack through
        every node. At the root we can cheaply detect moves that would
        immediately create a repeated position. A third repetition is a draw
        regardless of the static search score; a second occurrence gets a
        tiny preference penalty so equal-score AI-vs-AI positions don't
        oscillate forever.

        A separate small penalty handles a very common shallow-search failure:
        the same side immediately moving a piece back to the square it occupied
        two plies earlier. It is only a tie-break preference; a materially
        superior reversal can still be selected.
        """
        penalty = 0

        if len(board.move_stack) >= 2:
            previous_same_side_move = board.move_stack[-2]
            if (
                move.from_square == previous_same_side_move.to_square
                and move.to_square == previous_same_side_move.from_square
                and move.promotion == previous_same_side_move.promotion
                and move.promotion is None
            ):
                penalty -= 30

        if not board.move_stack:
            return penalty

        board.push(move)
        try:
            if board.is_repetition(3):
                return 0
            if board.is_repetition(2):
                return penalty - 8
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
            try:
                if first:
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

                score = -child_score
            finally:
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

        if self._tt_safe(board, depth):
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
        tt_entry = self.tt.get(board) if self._tt_safe(board, depth) else None
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
            try:
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

                    # Quiet late moves are selectively reduced. Strong history
                    # moves stay at full depth more often.
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

                    # Fail-soft re-search.
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

                score = -child_score
            finally:
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

        if self._tt_safe(board, depth):
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
