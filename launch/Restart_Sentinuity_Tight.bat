@echo off
chcp 65001 >nul
setlocal EnableExtensions
title SENTINUITY TARGETED RESTART V5.1

set "ROOT=%~dp0.."

call "%ROOT%\launch\Shutdown_Sentinuity.bat"
if errorlevel 1 (
  echo [FAIL] Shutdown/compaction failed. Restart cancelled.
  pause
  exit /b 1
)

start "" "%ROOT%\launch\Launch_Sentinuity.bat"
exit /b 0

