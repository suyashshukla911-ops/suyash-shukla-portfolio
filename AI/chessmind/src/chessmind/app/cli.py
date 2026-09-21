import argparse
import json
from dataclasses import asdict
import chess

from ..config import EngineConfig
from ..engine.engine import ChessEngine


def build_parser():
    parser = argparse.ArgumentParser(
        description="ChessMind Core 0.4.0 — local chess intelligence"
    )
    parser.add_argument("--mode", choices=["gui", "human", "ai", "analyze"], default="gui")
    parser.add_argument("--side", choices=["white", "black"], default="black")
    parser.add_argument("--time-ms", type=int, default=3000)
    parser.add_argument("--depth", type=int, default=18)
    parser.add_argument("--fen", default=chess.STARTING_FEN)
    return parser


def make_engine(args):
    return ChessEngine(
        EngineConfig(
            max_depth=args.depth,
            time_limit_ms=args.time_ms,
        )
    )


def print_position(board):
    print()
    print(board)
    print()
    print("FEN:", board.fen())
    print()


def print_response(response):
    print(
        f"AI: {response.best_move_san} ({response.best_move_uci}) | "
        f"eval={response.evaluation:+.2f} | "
        f"depth={response.depth} | "
        f"nodes={response.nodes:,} | "
        f"qnodes={response.qnodes:,} | "
        f"tt={response.tt_hits:,} | "
        f"cutoffs={response.cutoffs:,} | "
        f"nps={response.nps:,} | "
        f"time={response.elapsed_ms} ms"
    )
    if response.principal_variation:
        print("PV:", " ".join(response.principal_variation))


def parse_user_move(board, raw):
    try:
        return board.parse_san(raw)
    except ValueError:
        pass

    try:
        move = chess.Move.from_uci(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid move: {raw}") from exc

    if move not in board.legal_moves:
        raise ValueError(f"Illegal move: {raw}")

    return move


def run_human(args):
    board = chess.Board(args.fen)
    engine = make_engine(args)
    human_color = chess.WHITE if args.side == "white" else chess.BLACK

    while True:
        print_position(board)

        outcome = board.outcome(claim_draw=True)
        if outcome:
            print("GAME OVER:", outcome)
            return

        if board.turn == human_color:
            raw = input("Your move (SAN/UCI, 'quit' to exit): ").strip()
            if raw.lower() in {"quit", "exit"}:
                print("Exited.")
                return

            try:
                board.push(parse_user_move(board, raw))
            except ValueError as exc:
                print("Invalid move:", exc)
                continue
        else:
            response = engine.analyze(board)
            print_response(response)
            board.push_uci(response.best_move_uci)


def run_ai_vs_ai(args):
    board = chess.Board(args.fen)
    engine = make_engine(args)
    move_number = 1

    while not board.is_game_over(claim_draw=True):
        print_position(board)
        response = engine.analyze(board)
        print(f"Move {move_number}:")
        print_response(response)

        if response.best_move_uci is None:
            break

        board.push_uci(response.best_move_uci)
        move_number += 1

    print_position(board)
    print("GAME OVER:", board.outcome(claim_draw=True))


def run_analyze(args):
    board = chess.Board(args.fen)
    engine = make_engine(args)
    print_position(board)

    response = engine.analyze(board)
    print_response(response)
    print()
    print(json.dumps(asdict(response), indent=2))


def main():
    args = build_parser().parse_args()

    if args.mode == "gui":
        from ..gui.app import launch
        launch()
    elif args.mode == "human":
        run_human(args)
    elif args.mode == "ai":
        run_ai_vs_ai(args)
    else:
        run_analyze(args)


if __name__ == "__main__":
    main()
