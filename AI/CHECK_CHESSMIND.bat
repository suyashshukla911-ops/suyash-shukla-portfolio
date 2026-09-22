@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run run_ai.bat first so the virtual environment exists.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "%CD%\check_engine.py"
pause
