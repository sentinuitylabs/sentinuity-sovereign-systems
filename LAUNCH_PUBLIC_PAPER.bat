@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [FAIL] Sentinuity has not been installed in this folder.
  echo Run INSTALL_PUBLIC_PAPER.bat first.
  pause
  exit /b 1
)

set "PATH=%CD%\.venv\Scripts;%PATH%"
call .\launch\Launch_Sentinuity_Public_Paper.bat
if errorlevel 1 pause
exit /b %errorlevel%
