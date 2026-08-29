@echo off
cd /d "%~dp0"

echo ========================================
echo   Starting Application
echo ========================================
echo.

REM ========================================
REM Create virtual environment if necessary
REM ========================================

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    py -m venv .venv

    if errorlevel 1 (
        echo.
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Virtual environment found.
)

REM ========================================
REM Check / repair pip
REM ========================================

echo.
echo [2/4] Checking pip...

".venv\Scripts\python.exe" -m pip --version >nul 2>&1

if errorlevel 1 (
    echo pip not found. Installing pip...
    ".venv\Scripts\python.exe" -m ensurepip --upgrade

    if errorlevel 1 (
        echo.
        echo ERROR: Could not install pip.
        echo Delete .venv and recreate it.
        pause
        exit /b 1
    )
)

echo pip is ready.

REM ========================================
REM Install requirements
REM ========================================

echo.
echo [3/4] Installing requirements...

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Failed to install requirements.
    pause
    exit /b 1
)

REM ========================================
REM Start application
REM ========================================

echo.
echo [4/4] Starting application...
echo.

".venv\Scripts\python.exe" app.py

echo.
pause