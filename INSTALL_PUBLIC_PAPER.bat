@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  SENTINUITY V2 PUBLIC PAPER - FIRST-TIME INSTALLER
echo ============================================================

where py >nul 2>nul
if %errorlevel%==0 (
  set "BOOTPY=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [FAIL] Python 3.11 or 3.12 was not found on PATH.
    echo Install 64-bit Python from python.org and enable Add Python to PATH.
    pause
    exit /b 1
  )
  set "BOOTPY=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Creating virtual environment...
  %BOOTPY% -m venv .venv
  if errorlevel 1 goto :fail
) else (
  echo [1/5] Virtual environment already exists.
)

set "PY=%CD%\.venv\Scripts\python.exe"

echo [2/5] Installing Python requirements...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

if not exist ".env" (
  echo [3/5] Creating .env from the paper-safe example...
  copy /Y ".env.example" ".env" >nul
) else (
  echo [3/5] Existing .env preserved.
)

echo [4/5] Creating clean local databases and Council state...
"%PY%" .\tools\bootstrap_public_install.py
if errorlevel 1 goto :fail

echo [5/5] Running release verification...
"%PY%" .\tools\verify_public_v2_release.py
if errorlevel 1 goto :fail

echo.
echo Installation completed.
echo Add your Chainstack and model provider keys to .env, then run:
echo   LAUNCH_PUBLIC_PAPER.bat
start "" notepad.exe ".env"
pause
exit /b 0

:fail
echo.
echo [FAIL] Installation did not complete. Review the message above.
pause
exit /b 1
