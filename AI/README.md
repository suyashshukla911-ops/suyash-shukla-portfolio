# ChessMind AI — Portfolio Integration

This folder contains the AI runtime and the portfolio integration.

## Structure

- `run_ai.bat` — starts the local portfolio + ChessMind server from the `AI` folder.
- `server.py` — serves the portfolio and ChessMind API.
- `requirements.txt` — runtime Python dependency.
- `chessmind/web/index.html` — ChessMind browser UI.
- `chessmind/portfolio-integration.js` — injects the AI node/modal into the portfolio.
- `chessmind/src/chessmind/` — ChessMind core runtime.

## Run

From the `AI` folder, double-click `run_ai.bat`. Then open the portfolio at `http://127.0.0.1:8080/`.

The portfolio AI node opens `AI/chessmind/web/index.html` in the modal.

The virtual environment is created as `AI/.venv` and should not be committed.
