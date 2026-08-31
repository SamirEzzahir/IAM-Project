@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Application is not installed. Run install.bat first.
    pause
    exit /b 1
)

echo LAN mode allows colleagues to operate Selenium on this PC.
echo Keep this window open and use a strong shared password.
echo.
set "APP_USERNAME=fb-emm"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$p=Read-Host 'Shared password' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($p); try {[Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)} finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)}"`) do set "APP_PASSWORD=%%P"
if not defined APP_PASSWORD (
    echo ERROR: A password is required in LAN mode.
    pause
    exit /b 1
)

set "APP_HOST=0.0.0.0"
set "OPEN_BROWSER=1"
".venv\Scripts\python.exe" app.py

echo.
pause
