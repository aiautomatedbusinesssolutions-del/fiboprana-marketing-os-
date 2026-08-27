@echo off
REM Fiboprana marketing dashboard - localhost only, debug on.
REM Starts the Flask server in this window.
REM Press Ctrl+C or close this window to stop the server.

cd /d "%~dp0"

if not exist ".\.venv\Scripts\python.exe" (
    echo.
    echo Virtual environment not found at .\.venv\Scripts\python.exe
    echo.
    echo Run these from the project root first:
    echo     python -m venv .venv
    echo     .\.venv\Scripts\Activate.ps1
    echo     pip install -r requirements.txt
    echo.
    echo See README.md "Development setup" for details.
    echo.
    pause
    exit /b 1
)

echo Fiboprana marketing server starting.
echo Wait for "Running on http://127.0.0.1:5000" below,
echo then open that URL in the browser of your choice.
echo Close this window or Ctrl+C to stop the server.
echo.

.\.venv\Scripts\python.exe app.py
