@echo off
rem Read-only health and protected-edge monitor. No restart authority.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:replace"
title SENTINUITY WATCH - READ ONLY
set "ROOT_PATH=%~dp0.."
for %%I in ("%ROOT_PATH%") do set "ROOT_PATH=%%~fI"
cd /d "%ROOT_PATH%"
echo ============================================================
echo SENTINUITY WATCH - READ ONLY AUDIT MONITOR
echo No restarts. No DB writes. No configuration mutation.
echo ============================================================
python -m services.sentinuity_watch
pause
