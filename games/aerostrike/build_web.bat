@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if exist "build\web\audio" rmdir /s /q "build\web\audio"

python -m pygbag --build .
if errorlevel 1 (
    echo.
    echo PYGBAG BUILD FAILED.
    exit /b 1
)

python postbuild.py
if errorlevel 1 (
    echo.
    echo POST-BUILD AUDIO PATCH FAILED.
    exit /b 1
)

echo.
echo AeroStrike web build is ready in build\web
endlocal
