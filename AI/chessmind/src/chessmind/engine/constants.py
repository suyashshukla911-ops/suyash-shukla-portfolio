import chess

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
