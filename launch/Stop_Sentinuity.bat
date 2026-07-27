@echo off
:: Stop_Sentinuity.bat — FAST shutdown, no UAC, no prompts.
:: Kills all named Sentinuity service windows, does a WAL checkpoint,
:: then exits. Use this during active sessions when you need to stop fast.
:: For a graceful checkpoint-and-log shutdown use Shutdown_Sentinuity.bat.
setlocal EnableDelayedExpansion

echo.
echo  ══════════════════════════════════════════════════════
echo    SENTINUITY — FAST STOP  (no UAC, no prompts)
echo  ══════════════════════════════════════════════════════

:: ── kill named windows ────────────────────────────────────────────────────
for %%W in (
    "Executor"
    "NeuralSupervisor"
    "MarketIntel"
    "PumpMonitor"
    "IngestPipeline"
    "PriceOracle"
    "FreshnessEnforcer"
    "RollingEviction"
    "SystemGuardian"
    "SovHub"
    "Gateway"
    "OpenClaw Security Sentinel"
    "WalletScout"
    "APIMonitor"
    "Cloudflare"
) do (
    taskkill /FI "WINDOWTITLE eq %%~W*" /F >nul 2>&1
    echo   stopped: %%~W
)

:: ── WAL checkpoint (non-blocking, best-effort) ────────────────────────────
set "ROOT_PATH=%~dp0"
if exist "%ROOT_PATH%sentinuity_matrix.db" (
    python -c "import sqlite3; c=sqlite3.connect('%ROOT_PATH%sentinuity_matrix.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close(); print('  WAL checkpoint OK')" 2>nul || echo   WAL checkpoint skipped
)

echo.
echo  All services stopped. Safe to re-launch or run cleanup scripts.
echo.
