@echo off
REM Fiboprana Marketing OS dashboard (fleet) - localhost only.
REM Serves http://127.0.0.1:8765 and opens it in the browser.
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

.\.venv\Scripts\python.exe -m fleet.dashboard
