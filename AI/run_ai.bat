@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ==========================================
echo   ChessMind AI 2.0 - Windows Launcher
echo ==========================================
echo.

set "PY_EXE="
where py >nul 2>nul
if not errorlevel 1 (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%P"
)

if not defined PY_EXE (
  where python >nul 2>nul
  if not errorlevel 1 (
    for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PY_EXE=%%P"
  )
)

if not defined PY_EXE (
  echo [ERROR] Python 3 was not found.
  echo Install Python 3 and run this file again.
  pause
  exit /b 1
)

echo Using Python:
echo %PY_EXE%
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Creating AI virtual environment...
  "%PY_EXE%" -m venv ".venv"
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"

"%VENV_PY%" -c "import chess" >nul 2>nul
if errorlevel 1 (
  echo Installing python-chess...
  "%VENV_PY%" -m pip install --disable-pip-version-check -r "%CD%\requirements.txt"
  if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
  )
)

echo.
echo Starting ChessMind AI 2.0...
echo Open http://127.0.0.1:8080/AI/chessmind/web/index.html
start "ChessMind AI 2.0" http://127.0.0.1:8080/AI/chessmind/web/index.html

echo.
"%VENV_PY%" server.py
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" echo [ERROR] Server exited with code %EXITCODE%.
pause
exit /b %EXITCODE%
