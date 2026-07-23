@echo off
setlocal EnableExtensions EnableDelayedExpansion
title SENTINUITY SHUTDOWN + OFFLINE RETENTION SIGN-OFF

for %%I in ("%~dp0..") do set "ROOT_PATH=%%~fI"
if not exist "%ROOT_PATH%\services" set "ROOT_PATH=C:\Users\Polar\.openclaw\workspace\trading-bot"
set "LOG_PATH=%ROOT_PATH%\logs"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%ROOT_PATH%" (
  echo [FAIL] Trading-bot root not found: %ROOT_PATH%
  pause
  exit /b 1
)

cd /d "%ROOT_PATH%"
if not exist "%LOG_PATH%" mkdir "%LOG_PATH%" >nul 2>&1
if not exist "%LOG_PATH%\db_retention" mkdir "%LOG_PATH%\db_retention" >nul 2>&1

set "PY_EXE="
for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PY_EXE set "PY_EXE=%%P"
if not defined PY_EXE for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PY_EXE set "PY_EXE=%%P"
if not defined PY_EXE (
  echo [FAIL] Python could not be resolved.
  pause
  exit /b 2
)

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if /I "%~2"=="--no-pause" set "NO_PAUSE=1"

echo ============================================================
echo  SENTINUITY SHUTDOWN + OFFLINE RETENTION
echo ============================================================
echo  Root:   %ROOT_PATH%
echo  Python: %PY_EXE%
echo  Time:   %DATE% %TIME%
echo.

rem The marker is best-effort. Shutdown and pruning continue if the DB is absent.
echo [1/7] Writing shutdown marker...
if not exist "%ROOT_PATH%\runtime" mkdir "%ROOT_PATH%\runtime" >nul 2>&1
> "%ROOT_PATH%\runtime\shutdown_requested.marker" echo shutdown_requested_at=%DATE% %TIME%
if exist "%ROOT_PATH%\sentinuity_matrix.db" (
  "%PY_EXE%" -c "import sqlite3; p=r'%ROOT_PATH%\sentinuity_matrix.db'; c=sqlite3.connect(p,timeout=5); c.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT)'); c.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('OPERATOR_SHUTDOWN_REQUESTED','1','Shutdown_Sentinuity')); c.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('LAUNCH_STATE','shutdown_requested','Shutdown_Sentinuity')); c.commit(); c.close()" > "%LOG_PATH%\shutdown_marker_db.log" 2>&1
)
echo  [OK] Marker stage complete.
echo.

echo [2/7] Stopping Sentinuity process trees...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
 "$root=[regex]::Escape('%ROOT_PATH%'); $self=$PID; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)).ParentProcessId; $rx=$root+'|sentinuity_matrix\.db|sentinuity_intelligence\.db|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|sovereign_hub|streamlit|cloudflared|openclaw|services[\\/.](execution_engine|ingest_pipeline|market_intelligence|ws_price_oracle|neural_supervisor|system_guardian|sovereign_governor|freshness_enforcer|active_pipeline_cleaner|price_enricher|periodic_refresh|winner_snapshot_archiver|shadow_runner_tracker|wallet_scout|telegram_scout|x_scout|symbiotic_router|reconciliation_engine|live_settlement_recovery|council_build_orchestrator|council_autobuilder|intelligence_orchestrator|forge_code_writer|github_scout|openclaw_security_sentinel|copytrade_shadow_scanner|smart_wallet_trade_ingester|substrate_opportunity_scanner|substrate_portfolio_supervisor|substrate_copytrade_bridge_loop|substrate_paper_trader|macro_channel|macro_price_feed|paper_wallet_refresher|council_chamber_bridge|market_tide|signal_gate)'; $p=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $self -and $_.ProcessId -ne $parent -and ([string]$_.CommandLine -match $rx) }; foreach($x in $p){ try{Stop-Process -Id $x.ProcessId -Force -ErrorAction Stop}catch{} }; Write-Host ('Stopped matched processes: '+@($p).Count)" > "%LOG_PATH%\shutdown_process_sweep.log" 2>&1
type "%LOG_PATH%\shutdown_process_sweep.log"

rem Image-level fallback restores the known reliable blunt shutdown behaviour.
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM pythonw.exe /T >nul 2>&1
taskkill /F /IM py.exe /T >nul 2>&1
taskkill /F /IM streamlit.exe /T >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
taskkill /F /IM openclaw.exe /T >nul 2>&1
echo  [OK] Runtime image sweep complete.
echo.

echo [3/7] Waiting for SQLite handles to clear...
timeout /t 12 /nobreak >nul

rem Retry the handle check for up to 30 seconds. This also works when already off.
echo [4/7] Verifying databases are offline...
set "LOCK_OK=0"
for /L %%N in (1,1,10) do (
  "%PY_EXE%" -c "import sqlite3,sys,pathlib; p=pathlib.Path(r'%ROOT_PATH%\sentinuity_matrix.db'); sys.exit(0) if not p.exists() else None; c=sqlite3.connect(p,timeout=2); c.execute('PRAGMA busy_timeout=2000'); c.execute('BEGIN IMMEDIATE'); c.rollback(); c.close()" >nul 2>&1
  if not errorlevel 1 (
    set "LOCK_OK=1"
    goto :LOCK_READY
  )
  timeout /t 3 /nobreak >nul
)

:LOCK_READY
if not "!LOCK_OK!"=="1" (
  echo [FAIL] sentinuity_matrix.db remained locked after shutdown.
  echo        Retention was not attempted to avoid corruption.
  if "!NO_PAUSE!"=="0" pause
  exit /b 3
)
echo  [OK] Database is offline and writable.
echo.

echo [5/7] Checkpointing WAL...
if exist "%ROOT_PATH%\sentinuity_matrix.db" (
  "%PY_EXE%" -c "import sqlite3; p=r'%ROOT_PATH%\sentinuity_matrix.db'; c=sqlite3.connect(p,timeout=30); c.execute('PRAGMA busy_timeout=30000'); print(c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()); c.close()" > "%LOG_PATH%\shutdown_wal_checkpoint.log" 2>&1
  if errorlevel 1 (
    echo [WARN] WAL checkpoint failed. Retention runner will retry safely.
  ) else (
    echo  [OK] WAL checkpoint complete.
  )
) else (
  echo [FAIL] sentinuity_matrix.db is missing.
  if "!NO_PAUSE!"=="0" pause
  exit /b 4
)
echo.

echo [6/7] Running archive-first retention, VACUUM and 20 MB gate...
"%PY_EXE%" "%ROOT_PATH%\launch\shutdown_retention_runner.py" --root "%ROOT_PATH%" > "%LOG_PATH%\shutdown_retention_runner.log" 2>&1
set "RETENTION_RC=!ERRORLEVEL!"
type "%LOG_PATH%\shutdown_retention_runner.log"
if not "!RETENTION_RC!"=="0" (
  echo.
  echo [FAIL] Retention failed with code !RETENTION_RC!.
  echo        Sentinuity remains OFF. Review:
  echo        %LOG_PATH%\shutdown_retention_runner.log
  if "!NO_PAUSE!"=="0" pause
  exit /b !RETENTION_RC!
)
echo.

echo [7/7] Final size and integrity confirmation...
"%PY_EXE%" -c "import pathlib,sqlite3,sys; r=pathlib.Path(r'%ROOT_PATH%'); p=r/'sentinuity_matrix.db'; parts=[p,r/'sentinuity_matrix.db-wal',r/'sentinuity_matrix.db-shm']; sizes={x.name:(x.stat().st_size/1048576 if x.exists() else 0.0) for x in parts}; total=sum(sizes.values()); c=sqlite3.connect(p,timeout=30); qc=c.execute('PRAGMA quick_check').fetchone()[0]; c.close(); print('sizes_mb='+str({k:round(v,2) for k,v in sizes.items()})); print('total_footprint_mb='+str(round(total,2))); print('quick_check='+str(qc)); sys.exit(0 if qc=='ok' and total<=20 else 8)" > "%LOG_PATH%\shutdown_final_db_check.log" 2>&1
set "FINAL_RC=!ERRORLEVEL!"
type "%LOG_PATH%\shutdown_final_db_check.log"
if not "!FINAL_RC!"=="0" (
  echo [FAIL] Final database size/integrity gate failed.
  if "!NO_PAUSE!"=="0" pause
  exit /b !FINAL_RC!
)

echo.
echo ============================================================
echo  SHUTDOWN AND RETENTION PASSED
echo ============================================================
echo  Sentinuity is OFF.
echo  Matrix footprint is at or below 20 MB.
echo  Verified pre-retention backup is in: %ROOT_PATH%\db_backups
echo  Full log: %LOG_PATH%\shutdown_retention_runner.log
echo ============================================================
if "!NO_PAUSE!"=="0" pause
exit /b 0
