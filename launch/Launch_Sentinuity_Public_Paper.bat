@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."

echo ============================================================
echo  SENTINUITY V2 PUBLIC PAPER RELEASE - SAFE LAUNCH
echo ============================================================

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

%PY% .\launch\FORCE_PAPER_SAFE_PRESTART_0707.py
if errorlevel 1 (
  echo [FAIL] Could not force paper-safe configuration.
  exit /b 1
)

%PY% .\launch\VERIFY_LAUNCH_READY.py
if errorlevel 1 (
  echo [FAIL] Paper-release verification failed. Nothing was launched.
  exit /b 1
)

set "SENTINUITY_PUBLIC_PAPER_RELEASE=1"
call .\launch\Launch_Sentinuity.bat
exit /b %errorlevel%
