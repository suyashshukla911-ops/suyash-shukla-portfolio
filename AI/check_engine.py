from __future__ import annotations

import chess

import server


def run():
    b = chess.Board()
    first = []
    for seed in (101, 202, 303, 404, 505):
        r = server.safe_engine_analysis(b, depth=6, time_ms=1200, search_seed=seed, diversify=True)
        first.append(r["best_move_san"])
    print("Opening samples:", first)

    screenshot_fen = "r3k2r/p2p1p1p/1pp1p3/7R/7P/3b1BK1/6P1/4q3 w kq - 0 26"
    pos = chess.Board(screenshot_fen)
    print("Screenshot position valid:", pos.is_valid(), "check:", pos.is_check())
    print("Legal check evasions:", [pos.san(m) for m in pos.legal_moves])

    r = server.safe_engine_analysis(pos, depth=8, time_ms=1800, search_seed=2026, diversify=True)
    print("Screenshot analysis:", r["best_move_san"], r["evaluation"], "depth", r["depth"])

    # A legal sequence for repetition-stack verification.
    hist = ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6"]
    b2 = server.board_from_request("start", hist)
    print("Repetition count after sequence:", server.repetition_count(b2))


if __name__ == "__main__":
    run()
