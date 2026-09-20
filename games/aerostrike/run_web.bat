@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

call build_web.bat
if errorlevel 1 exit /b 1

echo.
echo Starting local static server on http://127.0.0.1:8080
python -m http.server 8080 --directory build\web --bind 127.0.0.1
endlocal
