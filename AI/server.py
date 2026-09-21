from __future__ import annotations

import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

AI_DIR = Path(__file__).resolve().parent
SITE_ROOT = AI_DIR.parent
CORE_SRC = AI_DIR / "chessmind" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

try:
    import chess
except ImportError as exc:
    raise SystemExit(
        "python-chess is not installed. Run AI\\run_ai.bat first."
    ) from exc

from chessmind.config import EngineConfig
from chessmind.engine.engine import ChessEngine

HOST = os.environ.get("CHESSMIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("CHESSMIND_PORT", "8080")))
ENGINE_LOCK = threading.Lock()


def board_from_fen(fen: str | None) -> chess.Board:
    if not fen or fen == "start":
        return chess.Board()
    board = chess.Board(fen.strip())
    if not board.is_valid():
        raise ValueError(f"Invalid chess position: {board.status()}")
    return board


def state_payload(
    board: chess.Board,
    last_move: str | None = None,
    history: list[str] | None = None,
) -> dict:
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
        else:
            message = f"Game over — {kind.replace('_', ' ').title()}."

    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "check": board.is_check(),
        "terminal": terminal,
        "terminal_kind": kind,
        "terminal_message": message,
        "legal_moves": [m.uci() for m in board.legal_moves],
        "history": list(history or []),
        "last_move": last_move,
    }


def safe_engine_analysis(board: chess.Board, depth: int, time_ms: int) -> dict:
    """Run the supplied engine, with defensive retries at the web boundary."""
    configs = [
        EngineConfig(max_depth=depth, time_limit_ms=time_ms, transposition_size=50_000),
        EngineConfig(max_depth=min(depth, 5), time_limit_ms=min(time_ms, 700), transposition_size=20_000),
    ]
    last_error: Exception | None = None

    for cfg in configs:
        try:
            # Each attempt receives a fresh board/engine so no search state can leak.
            work = chess.Board(board.fen())
            engine = ChessEngine(cfg)
            response = engine.analyze(work)
            legal = set(work.legal_moves)
            candidate = chess.Move.from_uci(response.best_move_uci) if response.best_move_uci else None

            if candidate is not None and candidate in legal:
                # Recompute SAN against the exact untouched board copy.
                san = work.san(candidate)
                return {
                    "ok": True,
                    "best_move_uci": candidate.uci(),
                    "best_move_san": san,
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

            # A core response without a usable move is only valid for terminal positions.
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

    # Absolute safety net: never break the UI on a non-terminal position.
    legal_moves = list(board.legal_moves)
    if not legal_moves:
        return {"ok": True, "best_move_uci": None, "best_move_san": None, "terminal": True,
                "terminal_kind": "terminal", "fallback": True, "error": str(last_error) if last_error else None}

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
    server_version = "SuyashChessMind/1.0"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def send_json(self, payload: dict, status: int = 200):
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
                self.send_json({"ok": True, "engine": "ChessMind Core 0.4.0", "python": sys.version.split()[0]})
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
                board = board_from_fen(body.get("fen", "start"))
                self.send_json(state_payload(board, body.get("last_move"), body.get("history") or []))
                return

            if path == "/api/chessmind/play":
                board = board_from_fen(body.get("fen", "start"))
                uci = str(body.get("uci", "")).strip()
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(f"Illegal move: {uci}")
                move_san = board.san(move)
                prior_history = body.get("history") or []
                if not isinstance(prior_history, list):
                    prior_history = []
                next_history = [str(item) for item in prior_history] + [move_san]
                board.push(move)
                self.send_json(state_payload(board, move.uci(), next_history))
                return

            if path == "/api/chessmind/analyze":
                board = board_from_fen(body.get("fen", "start"))
                depth = max(1, min(18, int(body.get("depth", 8))))
                time_ms = max(100, min(120000, int(body.get("time_ms", 1000))))
                with ENGINE_LOCK:
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


def main() -> None:
    os.chdir(SITE_ROOT)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"[ERROR] Could not start server on {HOST}:{PORT}: {exc}")
        print("Try: set CHESSMIND_PORT=8081")
        raise SystemExit(1) from exc

    print("==========================================")
    print("  Suyash Portfolio + ChessMind AI")
    print("==========================================")
    print(f"Portfolio : http://{HOST}:{PORT}/")
    print(f"ChessMind : http://{HOST}:{PORT}/AI/chessmind/web/index.html")
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
