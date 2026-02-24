@echo off
REM LingQ Import Manager — Windows launcher
REM Double-click this file in File Explorer to start the app.
REM The browser will open automatically.

cd /d "%~dp0"

REM ── Set up virtual environment if missing ──────────────────────────────── REM
if not exist ".venv\Scripts\activate.bat" (
    echo First-time setup: creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Python was not found.
        echo Please install Python from https://www.python.org/downloads/
        echo Make sure to check "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
    playwright install chromium
    echo.
    echo Setup complete!
    echo.
)

call .venv\Scripts\activate.bat
python lingq_app.py

REM Keep the window open if something went wrong
if errorlevel 1 pause
