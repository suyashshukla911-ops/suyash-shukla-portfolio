# ChessMind AI 2.0 — refined website build

This package intentionally keeps the portfolio integration untouched.

## Runtime layout
- `server.py` — single Python server + complete chess engine core + API.
- `chessmind/web/index.html` — single interactive chess client, including AI-vs-AI mode.
- `chessmind/portfolio-integration.js` — existing portfolio glue, unchanged.
- `chessmind/portfolio-ai.css` — existing portfolio glue styling, unchanged.
- `run_ai.bat` — Windows launcher.
- `CHECK_CHESSMIND.bat` — direct local launcher.

## Main refinements
- Rebuilds the real move stack from UCI history, so threefold repetition detection survives the web/FEN boundary.
- Treats third repetitions as draw nodes during search and penalizes immediate backtracking / repeated positions at the root.
- Shared engine/transposition table across requests instead of recreating the complete engine for every AI move.
- Mate-aware iterative deepening, quiescence, null move, LMR, PVS, TT, check extensions, history/killer/countermove ordering from the supplied optimized build.
- Fixed-size 8×8 board grid with explicit 8 equal rows and columns.
- AI-vs-AI showcase mode.
- Local web UI automatically targets the local API; production keeps the existing Render API URL.

No file under the root portfolio was modified.
