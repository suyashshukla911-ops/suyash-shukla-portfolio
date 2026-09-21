from pathlib import Path
import chess.pgn


def load_pgn(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return chess.pgn.read_game(handle)


def save_game(game, path):
    with Path(path).open("w", encoding="utf-8") as handle:
        exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        handle.write(game.accept(exporter))
