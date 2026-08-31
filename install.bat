@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    py -m venv .venv
    if errorlevel 1 goto :error
)

echo Installing pinned dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Installation completed. Use run.bat for local access or run-lan.bat for LAN access.
pause
exit /b 0

:error
echo.
echo ERROR: Installation failed.
pause
exit /b 1
