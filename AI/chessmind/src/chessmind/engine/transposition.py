from dataclasses import dataclass
from enum import Enum

import chess


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
    """
    Fixed-size direct-mapped transposition table.

    The position key uses python-chess's O(1) raw board-state tuple rather
    than rescanning 64 squares with a Polyglot hash. The table itself is a
    fixed list indexed by the tuple's Python hash, so lookup/store are O(1)
    with no whole-table eviction sort.

    The 75-move counter is deliberately not part of the key. Searcher._tt_safe
    prevents TT reuse whenever the halfmove clock plus remaining search depth
    could reach the automatic 75-move draw. This preserves rule correctness
    while allowing high TT reuse during ordinary positions.
    """

    def __init__(self, max_entries: int = 250_000):
        requested = max(10_000, int(max_entries))
        size = 1
        while size < requested:
            size <<= 1
        self.size = size
        self._mask = size - 1
        self._table: list[_Slot | None] = [None] * size
        self.generation = 0
        self._entries = 0

    @staticmethod
    def key(board):
        return board._transposition_key()

    @staticmethod
    def _index(key, mask):
        # Stable, process-independent mixing keeps deterministic mode truly
        # deterministic while avoiding Python's randomized hash seed.
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
            # Preserve deeper information from the same generation. A newer
            # generation with equal/greater depth can replace it.
            if (
                old.entry.generation == self.generation
                and old.entry.depth > entry.depth
            ):
                return
            self._table[index] = _Slot(key, entry)
            return

        # Collision replacement:
        # - always replace an entry from an older generation;
        # - otherwise prefer the deeper search result.
        if (
            old.entry.generation < self.generation
            or entry.depth >= old.entry.depth
        ):
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
