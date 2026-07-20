@echo off
title SENTINUITY GUARDIAN
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:replace"
set "ROOT_PATH=%~dp0.."
for %%I in ("%ROOT_PATH%") do set "ROOT_PATH=%%~fI"
set "LOG_PATH=%ROOT_PATH%\logs"
cd /d "%ROOT_PATH%"

echo ============================================================
echo SENTINUITY SYSTEM GUARDIAN ONLINE
echo.
echo  Replaces: watchdog_monitor + system_health_monitor +
echo            auto_healer + db_prune_guard + emergency_vacuum
echo.
echo  Single restart authority - DB lease enforced
echo  Capital mutation guard - close_claimed_until active
echo  Wallet recon - safe-only (no open positions or latched signals)
echo.
echo  Waiting 15 seconds for core services to initialise...
echo ============================================================
echo.
timeout /t 15 /nobreak >nul

echo Guardian active. Running continuous cycle.
echo Restart decisions logged to system_health_events table.
echo Output shown here (not redirected) so startup errors are visible.
echo.

rem ── API MONITOR (starts silently alongside guardian) ─────────────────────
rem Runs on localhost:8766 — console + panel connect to it for API visibility
rem If api_monitor_server.py doesn't exist, this silently skips
if exist "%ROOT_PATH%\api_monitor_server.py" (
    start "API Monitor" /b cmd /c "cd /d %ROOT_PATH% && python api_monitor_server.py >> %LOG_PATH%\api_monitor.log 2>&1"
    echo [OK] API Monitor started on localhost:8766
    echo      Open api_monitor_panel.html in browser for full API visibility
    echo.
) else (
    echo [INFO] api_monitor_server.py not found - API monitoring disabled
    echo.
)

rem ── FORGE STATUS (snapshot before guardian loop starts) ──────────────────
echo ============================================================
echo SOVEREIGN FORGE STATUS
echo ============================================================
if exist "scripts\forge_status_snapshot.py" (python scripts\forge_status_snapshot.py 2^>^&1) else (echo [INFO] Optional forge status snapshot not included.)
echo ============================================================
echo Guardian active. Running continuous cycle.
echo ============================================================
echo.

python -m services.system_guardian

echo.
echo ============================================================
echo GUARDIAN PROCESS EXITED - see error above if unexpected
echo Press any key to close this window
echo ============================================================
pause
