@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Application is not installed. Run install.bat first.
    pause
    exit /b 1
)

set "APP_HOST=127.0.0.1"
set "OPEN_BROWSER=1"
".venv\Scripts\python.exe" app.py

echo.
pause
