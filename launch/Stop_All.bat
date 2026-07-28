@echo off
title SENTINUITY — STOPPING ALL
setlocal enabledelayedexpansion

:: ── AUTO-ELEVATE TO ADMIN ────────────────────────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    %SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================================
echo  STOPPING ALL SENTINUITY SERVICES
echo ============================================================
echo.

:: ── CLOUDFLARE SERVICE (needs admin — now works) ─────────────────────────────
echo [1] Stopping Cloudflare tunnel service...
sc stop cloudflared >nul 2>&1
if errorlevel 1 (
    taskkill /F /IM cloudflared.exe /T >nul 2>&1
    echo   Fallback taskkill used.
) else (
    echo   Service stopped cleanly.
)
echo.

:: ── KILL ALL IN PARALLEL ─────────────────────────────────────────────────────
echo [2] Killing all processes (parallel)...
start "" /B taskkill /F /IM python.exe    /T >nul 2>&1
start "" /B taskkill /F /IM pythonw.exe   /T >nul 2>&1
REM === SENTINUITY BOOT CLEAN HOTFIX START ===
echo [BOOT-CLEAN] Running startup freshness purge...
set "SENT_HOTFIX_DIR="
if exist "%~dp0launch\startup_freshness_purge.py" set "SENT_HOTFIX_DIR=%~dp0launch"
if exist "%~dp0startup_freshness_purge.py" set "SENT_HOTFIX_DIR=%~dp0"
if exist "%~dp0..\launch\startup_freshness_purge.py" set "SENT_HOTFIX_DIR=%~dp0..\launch"

if not defined SENT_HOTFIX_DIR (
    echo [FATAL] startup_freshness_purge.py not found from launcher path
    pause
    exit /b 1
)

python "%SENT_HOTFIX_DIR%\startup_freshness_purge.py"
if errorlevel 1 (
    echo [FATAL] startup_freshness_purge failed. Launch aborted.
    pause
    exit /b 1
)

echo [BOOT-CLEAN] Running restart stale position reset...
python "%SENT_HOTFIX_DIR%\startup_restart_position_reset.py"
if errorlevel 1 (
    echo [FATAL] startup_restart_position_reset failed. Launch aborted.
    pause
    exit /b 1
)

echo [BOOT-CLEAN] Startup purge/reset complete.
REM === SENTINUITY BOOT CLEAN HOTFIX END ===
start "" /B taskkill /F /IM streamlit.exe /T >nul 2>&1
start "" /B taskkill /F /IM node.exe      /T >nul 2>&1

for %%T in (
  "SENTINUITY GUARDIAN" "SENTINUITY WATCHDOG" "CouncilBuild" "ShadowTracker"
  "SENTINUITY SOVEREIGN CONSOLE" "Gateway"
  "Scout" "IngestPipeline" "MarketIntel" "Supervisor"
  "ExecEngine" "SovGovernor" "SPE" "Replay" "PolarisAux" "Recon"
  "Researcher" "Reflect" "Reviewer" "Calibrator"
  "Messenger" "CH Analyst" "WalletScout" "TG Scout"
  "Vault" "Ivy Forge" "Tunnel" "SovHub"
  "Ingest" "Resolver" "Weaver" "Oracle" "Qualifier"
  "Executor" "ZombieRes" "Polaris" "HITL Bot"
  "Debate" "Health" "DB Prune" "Healer" "Dashboard" "Substrate"
  "SENTINUITY-ingest_pipeline" "SENTINUITY-market_intelligence"
  "SENTINUITY-execution_engine" "SENTINUITY-sovereign_governor"
  "SENTINUITY-pump_monitor" "SENTINUITY-wallet_scout"
  "SENTINUITY-telegram_scout" "SENTINUITY-neural_supervisor"
  "SENTINUITY-sovereign_parameter_engine" "SENTINUITY-replay_engine"
  "SENTINUITY-polaris" "SENTINUITY-polaris_auxiliary"
  "SENTINUITY-reconnaissance_engine" "SENTINUITY-code_vault"
  "SENTINUITY-sovereign_hub" "SENTINUITY-system_guardian"
  "WD-scout" "WD-ingest" "WD-resolver" "WD-signal_engine"
  "WD-oracle" "WD-token_qualifier" "WD-supervisor"
  "WD-paper_executor" "WD-zombie_resolver" "WD-polaris"
  "WD-health_monitor" "WD-db_prune_guard" "WD-auto_healer"
  "WD-debate_engine" "WD-polaris_researcher" "WD-wallet_scout"
) do start "" /B taskkill /F /FI "WINDOWTITLE eq %%~T" /T >nul 2>&1

timeout /t 3 /nobreak >nul
echo   Done.
echo.

echo ============================================================
echo  ALL SERVICES STOPPED
echo  Safe to run diagnostics or restart.
echo ============================================================
echo.
pause

:: Stop OpenClaw gateway last, after confirmed codebase shutdown
taskkill /F /IM openclaw.exe /T >nul 2>&1
