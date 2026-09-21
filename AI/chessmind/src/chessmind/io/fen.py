from ..core.position import Position


def load_fen(fen):
    return Position.from_fen(fen).board
