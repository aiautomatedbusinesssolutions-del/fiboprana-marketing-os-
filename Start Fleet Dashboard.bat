@echo off
REM Fiboprana Marketing OS dashboard (fleet) - localhost only.
REM Serves http://127.0.0.1:8766 and opens it in the browser.
REM (8766 on purpose - Stackivate's fleet dashboard owns 8765.)
REM Press Ctrl+C or close this window to stop the server.

cd /d "%~dp0"

if not exist ".\.venv\Scripts\python.exe" (
    echo.
    echo Virtual environment not found at .\.venv\Scripts\python.exe
    echo See README.md "Development setup" for details.
    echo.
    pause
    exit /b 1
)

.\.venv\Scripts\python.exe -m fleet.dashboard --port 8766
