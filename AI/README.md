# ChessMind Final Adaptive

This build preserves the existing ChessMind website implementation. The frontend
files were not changed. The backend now uses a two-layer decision architecture:

1. **Stockfish 19 primary calculator** when the official executable is installed.
2. **ChessMind Python fallback** when it is not, with the previous search stack plus
   stronger mate-safety and root-risk handling.

## Logic refinements

- Mate and forced tactical lines take priority over material safety.
- Near-best variation is allowed only when it remains inside a tight evaluation band.
- A seeded decision layer varies close opening choices instead of replaying one script.
- Opening choices react to the opponent's early queen moves, wing-pawn expansion,
  repeated-piece movement, and recent piece usage.
- Dubious edge-knight opening moves are filtered out when normal alternatives are
  engine-equivalent.
- Obvious unforced piece hangs are penalized only at the variation tie-break layer;
  genuine sacrifices remain available when the engine itself values them.
- Immediate opponent-mate threats are hard-gated at the root.
- Repetition history is rebuilt from UCI move history across the web/FEN boundary.
- AI-vs-AI uses game seeds and near-best choices, so identical positions do not
  always generate an identical opening script.

## Strong-engine mode

The recommended runtime is Stockfish 19. Stockfish 19 was released on 2026-09-05
and introduced SFNNv16 plus additional training improvements. ChessMind does not
claim to reproduce Magnus Carlsen's human brain or to be literally unbeatable.
Instead, it uses Stockfish's calculation strength and adds a bounded adaptive layer
for opening variation and human-like move selection.

### Windows

Run `run_ai.bat`. It creates the virtual environment, installs python-chess, then
tries to download the official Stockfish 19 Windows universal binary into
`engines/stockfish/`.

### Render/Linux

`render.yaml` downloads the official Stockfish 19 Linux universal binary during the
build and launches `server.py`.

### Manual installation

The installer uses these official Stockfish 19 release assets:

- Windows x86-64 universal:
  https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-universal.zip
- Linux x86-64 universal:
  https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-linux-x86-64-universal.tar.gz

You can also set `CHESSMIND_STOCKFISH_PATH` to an existing Stockfish executable.

## API contract

The existing `/api/chessmind/state`, `/api/chessmind/play`, `/api/chessmind/analyze`
and `/api/chessmind/health` endpoints remain unchanged.
