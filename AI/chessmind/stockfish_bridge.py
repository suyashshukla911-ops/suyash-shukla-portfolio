from __future__ import annotations

import atexit
import os
import platform
import shutil
import threading
from pathlib import Path
from typing import Any

import chess
import chess.engine


MATE_SCORE = 1_000_000


class StockfishBridge:
    """
    Optional native-engine layer.

    ChessMind keeps the existing Python engine as a fallback, but when a
    Stockfish 19 executable is installed it becomes the primary tactical
    calculator. A persistent UCI process avoids the startup cost on every
    request. The bridge never changes the website/API contract.
    """

    def __init__(self, engine_path: str | None = None):
        self.engine_path = self._discover(engine_path)
        self._engine: chess.engine.SimpleEngine | None = None
        self._lock = threading.RLock()
        self._started = False
        self._opening_usage: dict[str, int] = {}
        atexit.register(self.close)

    @staticmethod
    def _discover(explicit: str | None) -> str | None:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())

        env_path = os.environ.get("CHESSMIND_STOCKFISH_PATH")
        if env_path:
            candidates.append(Path(env_path).expanduser())

        root = Path(__file__).resolve().parents[1]
        binary_names = (
            ["stockfish.exe", "stockfish"]
            if platform.system().lower().startswith("win")
            else ["stockfish", "stockfish.exe"]
        )
        for name in binary_names:
            candidates.extend(
                [
                    root / "engines" / name,
                    root / "engines" / "stockfish" / name,
                    root / "stockfish" / name,
                ]
            )

        path_name = "stockfish.exe" if platform.system().lower().startswith("win") else "stockfish"
        found = shutil.which(path_name) or shutil.which("stockfish")
        if found:
            candidates.append(Path(found))

        for path in candidates:
            try:
                if path.is_file() and os.access(path, os.X_OK):
                    return str(path)
                if path.is_file():
                    return str(path)
            except OSError:
                continue
        return None

    @property
    def available(self) -> bool:
        return self.engine_path is not None

    def _ensure_engine(self) -> chess.engine.SimpleEngine:
        if self.engine_path is None:
            raise FileNotFoundError("Stockfish executable not found.")
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.engine_path, timeout=15)
            options: dict[str, Any] = {}
            if "Threads" in self._engine.options:
                options["Threads"] = max(1, min(4, os.cpu_count() or 1))
            if "Hash" in self._engine.options:
                options["Hash"] = max(16, min(256, int(os.environ.get("CHESSMIND_STOCKFISH_HASH_MB", "64"))))
            if options:
                self._engine.configure(options)
            self._started = True
        return self._engine

    @staticmethod
    def _score_cp(info: dict[str, Any], board: chess.Board) -> tuple[int, bool]:
        pov = info.get("score")
        if pov is None:
            return 0, False
        white = pov.white()
        if white.is_mate():
            mate = white.mate()
            if mate is None:
                return 0, True
            # Preserve distance-to-mate semantics used by the existing API.
            return (MATE_SCORE - abs(mate) * 2) if mate > 0 else (-MATE_SCORE + abs(mate) * 2), True
        cp = white.score(mate_score=MATE_SCORE)
        return int(cp or 0), False

    def analyze(
        self,
        board: chess.Board,
        time_ms: int,
        depth: int,
        diversify: bool = False,
        seed: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            engine = self._ensure_engine()
            limit = chess.engine.Limit(
                time=max(0.05, time_ms / 1000.0),
                depth=max(1, min(30, int(depth))),
            )

            # For ordinary analysis use the full budget on one principal move.
            # For gameplay diversification, use a small MultiPV only when it
            # can still preserve engine accuracy; exact tactical/mate choices
            # are never replaced merely to add variety.
            multipv = 3 if diversify else 1
            infos = engine.analyse(board, limit, multipv=multipv)
            if isinstance(infos, dict):
                infos = [infos]
            infos = [i for i in infos if i.get("pv")]
            if not infos:
                raise RuntimeError("Stockfish returned no principal variation.")

            primary = infos[0]
            primary_score, primary_mate = self._score_cp(primary, board)
            primary_move = primary["pv"][0]

            chosen = primary
            chosen_score = primary_score
            candidates: list[tuple[chess.engine.InfoDict, int, bool]] = []
            for info in infos:
                score, mate = self._score_cp(info, board)
                candidates.append((info, score, mate))

            # A human-like variation layer is deliberately subordinate to the
            # engine. Only near-equal, non-mate moves can be selected, and every
            # candidate comes from the same Stockfish search.
            if diversify and not primary_mate and len(candidates) > 1:
                tolerance = 18 if len(board.move_stack) < 16 else 10
                near = [item for item in candidates if primary_score - item[1] <= tolerance]
                if len(near) > 1:
                    import random
                    rng = random.Random(int(seed or 0))
                    recent_by_side: dict[bool, list[chess.Move]] = {chess.WHITE: [], chess.BLACK: []}
                    work = chess.Board()
                    for move in board.move_stack:
                        recent_by_side[work.turn].append(move)
                        work.push(move)

                    opponent_queen_early = False
                    opponent_wing_pushes = 0
                    mover = not board.turn
                    for ply, move in enumerate(board.move_stack[:14]):
                        tmp = chess.Board()
                        for prior in board.move_stack[:ply]:
                            tmp.push(prior)
                        piece = tmp.piece_at(move.from_square)
                        if tmp.turn == mover and piece is not None:
                            if piece.piece_type == chess.QUEEN and ply < 8:
                                opponent_queen_early = True
                            if piece.piece_type == chess.PAWN and (chess.square_file(move.to_square) in (0, 1, 6, 7)):
                                opponent_wing_pushes += 1

                    def style_adjust(move: chess.Move) -> float:
                        piece = board.piece_at(move.from_square)
                        if piece is None:
                            return 0.0
                        value = 0.0
                        if len(board.move_stack) < 16:
                            uci = move.uci()
                            used = self._opening_usage.get(uci, 0)
                            value -= min(3.0, used * 0.8)
                            if piece.piece_type in (chess.KNIGHT, chess.BISHOP) and move.from_square in (chess.B1, chess.G1, chess.B8, chess.G8):
                                value += 1.2
                            if piece.piece_type == chess.PAWN and chess.square_file(move.to_square) in (2, 3, 4, 5):
                                value += 1.4
                            if opponent_queen_early and piece.piece_type in (chess.KNIGHT, chess.BISHOP):
                                value += 1.0
                            if opponent_wing_pushes >= 2 and piece.piece_type == chess.PAWN and chess.square_file(move.to_square) in (2, 3, 4, 5):
                                value += 1.0
                            same_side_moves = recent_by_side.get(board.turn, [])[-3:]
                            if same_side_moves and move.from_square == same_side_moves[-1].to_square:
                                value -= 1.5
                        return value

                    near.sort(key=lambda item: item[1], reverse=True)
                    weights = []
                    for info, score, mate in near:
                        move = info["pv"][0]
                        gap = primary_score - score
                        weights.append(max(0.01, 1.0 - gap / max(1.0, tolerance + 1.0)) * max(0.05, 1.0 + style_adjust(move) / 10.0))
                    total = sum(weights)
                    roll = rng.random() * total
                    acc = 0.0
                    for item, weight in zip(near, weights):
                        acc += weight
                        if roll <= acc:
                            chosen = item[0]
                            chosen_score = item[1]
                            break

                    if len(board.move_stack) == 0:
                        self._opening_usage[chosen["pv"][0].uci()] = self._opening_usage.get(chosen["pv"][0].uci(), 0) + 1

            # Final tactical gate: never select a near-best variation that
            # allows the opponent to checkmate on its very next move. This is
            # deliberately separate from material so a real tactical sacrifice
            # remains available when Stockfish considers it sound.
            chosen_move = chosen["pv"][0]
            test = board.copy(stack=True)
            test.push(chosen_move)
            # Evaluate the actual legal replies without mutating the original
            # test board permanently.
            opponent_has_mate = False
            for reply in list(test.legal_moves):
                test.push(reply)
                try:
                    if test.is_checkmate():
                        opponent_has_mate = True
                        break
                finally:
                    if test.move_stack:
                        test.pop()
            if opponent_has_mate:
                chosen_move = primary_move
                chosen = primary
                chosen_score = primary_score

            pv = [m.uci() for m in chosen.get("pv", [])[:12]]
            return {
                "engine": "Stockfish 19",
                "backend": "stockfish",
                "best_move_uci": chosen_move.uci(),
                "best_move_san": board.san(chosen_move),
                "evaluation": chosen_score / 100.0,
                "depth": int(primary.get("depth", depth)),
                "nodes": int(primary.get("nodes", 0) or 0),
                "elapsed_ms": int(primary.get("time", 0.0) * 1000),
                "principal_variation": pv,
                "is_mate": bool(primary_mate),
                "terminal": False,
                "terminal_kind": None,
                "qnodes": 0,
                "tt_hits": 0,
                "tt_cutoffs": 0,
                "cutoffs": 0,
                "nps": int(primary.get("nps", 0) or 0),
                "fallback": False,
                "candidates": [
                    {"uci": info["pv"][0].uci(), "evaluation": score / 100.0, "mate": mate}
                    for info, score, mate in candidates
                ],
            }

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except Exception:
                    pass
                self._engine = None
                self._started = False
