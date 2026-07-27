@echo off
setlocal EnableExtensions
title SENTINUITY RESTART

set "ROOT=%~dp0.."
set "LOG=%ROOT%\logs"
set "PY=python"

if not exist "%ROOT%" (
  echo [FATAL] Root not found: %ROOT%
  pause
  exit /b 1
)

cd /d "%ROOT%"
if not exist "%LOG%" mkdir "%LOG%" >nul 2>&1

rem Elevate once. Shutdown, service-stop, port checks and retention may require admin.
net session >nul 2>&1
if errorlevel 1 (
  echo [0] Requesting Administrator restart window...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs"
  exit /b
)

echo ============================================================
echo   SENTINUITY SIGN-OFF RESTART V3
echo ============================================================
echo   Root: %ROOT%
echo   Time: %DATE% %TIME%
echo.
echo   Canonical order:
echo   shutdown ^> verify dead ^> checkpoint/sync ^> prune ^> DB audit
echo   ^> chart/history warmup ^> canonical Launch_Sentinuity.bat
echo ============================================================
echo.

rem ---------------------------------------------------------------------------
rem 1. Use the signed-off shutdown. It owns process termination and retention.
rem ---------------------------------------------------------------------------
echo [1/7] Running signed-off shutdown and retention...
rem EXIT_HARVEST_AUDIT_20260712: canonical launcher location is launch\.
rem Root-level duplicates must not exist; this restart calls ONLY the launch\ copy.
if not exist "%ROOT%\launch\Shutdown_Sentinuity.bat" (
  echo [FATAL] Missing %ROOT%\launch\Shutdown_Sentinuity.bat
  pause
  exit /b 1
)
call "%ROOT%\launch\Shutdown_Sentinuity.bat" --restart --no-pause
if errorlevel 1 (
  echo [FATAL] Shutdown returned an error. Launch aborted.
  pause
  exit /b 1
)
echo.

rem ---------------------------------------------------------------------------
rem 2. Verify no Sentinuity writers remain. Never delete WAL/SHM manually.
rem ---------------------------------------------------------------------------
echo [2/7] Verifying all DB writers are down...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[regex]::Escape('%ROOT%');" ^
  "$rx=$root+'|sentinuity_matrix\.db|sentinuity_intelligence\.db|services[\\/\.](execution_engine|market_intelligence|neural_supervisor|pump_monitor|ws_price_oracle|price_enricher|freshness_enforcer|active_pipeline_cleaner)|sovereign_hub|streamlit';" ^
  "$left=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ([string]$_.CommandLine -match $rx) };" ^
  "if($left){$left|Select-Object ProcessId,Name,CommandLine|Format-List; exit 2}else{Write-Host '[OK] No matched Sentinuity writers remain.'}" > "%LOG%\restart_writer_verify.log" 2>&1
type "%LOG%\restart_writer_verify.log"
if errorlevel 2 (
  echo [FATAL] Writer processes remain. Launch aborted to protect the DB.
  pause
  exit /b 2
)
echo.

rem ---------------------------------------------------------------------------
rem 3. Post-shutdown integrity and authoritative history/cache synchronisation.
rem Shutdown already checkpoints and prunes; this verifies the resulting DB.
rem ---------------------------------------------------------------------------
echo [3/7] Verifying database integrity after prune...
%PY% -c "import sqlite3,pathlib,sys; p=pathlib.Path(r'%ROOT%\sentinuity_matrix.db'); con=sqlite3.connect(str(p),timeout=20); q=con.execute('PRAGMA quick_check').fetchone()[0]; ck=con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall(); con.close(); print('quick_check=',q); print('checkpoint=',ck); print('db_mb=',round(p.stat().st_size/1048576,2)); sys.exit(0 if q=='ok' else 3)" > "%LOG%\restart_db_integrity.log" 2>&1
type "%LOG%\restart_db_integrity.log"
if errorlevel 1 (
  echo [FATAL] Database integrity failed. Launch aborted.
  pause
  exit /b 3
)
echo.

echo [4/7] Synchronising history and warming chart data...
if exist "%ROOT%\launch\sync_historical_trade_cache.py" (
  %PY% "%ROOT%\launch\sync_historical_trade_cache.py" --db "%ROOT%\sentinuity_matrix.db" --intel "%ROOT%\sentinuity_intelligence.db" >> "%LOG%\restart_history_sync.log" 2>&1
  if errorlevel 1 (
    echo   [WARN] Historical cache sync failed. See logs\restart_history_sync.log
  ) else (
    echo   [OK] Historical trade cache synchronised.
  )
) else (
  echo   [SKIP] launch\sync_historical_trade_cache.py not found.
)

%PY% -c "from services.glass_cadence_chart import fetch_cadence_buckets; b=fetch_cadence_buckets(r'%ROOT%\sentinuity_matrix.db'); print('bucket_count=',len(b)); print('source=',b[0].get('source_used') if b else 'NONE'); print('window_hours=',b[0].get('window_hours') if b else 'NONE'); print('trade_count=',b[0].get('trade_count_total') if b else 0); print('net=',round(b[0].get('net_pnl_total',0),2) if b else 0)" > "%LOG%\restart_chart_warmup.log" 2>&1
type "%LOG%\restart_chart_warmup.log"
if errorlevel 1 (
  echo   [WARN] Chart warmup failed. Launch may continue, but inspect logs\restart_chart_warmup.log.
) else (
  echo   [OK] Chart history warmed before dashboard launch.
)
echo.

rem ---------------------------------------------------------------------------
rem 5. Clear only launch-state markers. Do not reset or delete open positions.
rem ---------------------------------------------------------------------------
echo [5/7] Clearing shutdown marker and setting restart state...
if exist "%ROOT%\runtime\shutdown_requested.marker" del /f /q "%ROOT%\runtime\shutdown_requested.marker" >nul 2>&1
%PY% -c "import sqlite3; con=sqlite3.connect(r'%ROOT%\sentinuity_matrix.db',timeout=10); con.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT)'); con.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('OPERATOR_SHUTDOWN_REQUESTED','0','Restart_Sentinuity V3')); con.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('LAUNCH_STATE','restart_prelaunch','Restart_Sentinuity V3')); con.commit(); con.close()" >> "%LOG%\restart_state.log" 2>&1
if errorlevel 1 (
  echo [FATAL] Could not clear restart state. Launch aborted.
  pause
  exit /b 4
)
echo   [OK] Restart state prepared.
echo.

rem ---------------------------------------------------------------------------
rem 6. Run existing prelaunch audits only if present. Canonical launcher still
rem owns its own preflight and service graph.
rem ---------------------------------------------------------------------------
echo [6/7] Running optional restart sign-off audits...
if exist "%ROOT%\AUDIT_FINAL_0707_EDGE_1107_UI_WATCH.py" (
  %PY% "%ROOT%\AUDIT_FINAL_0707_EDGE_1107_UI_WATCH.py" >> "%LOG%\restart_optional_audits.log" 2>&1
  if errorlevel 1 (
    echo [FATAL] Existing final audit failed. Launch aborted.
    type "%LOG%\restart_optional_audits.log"
    pause
    exit /b 5
  )
)
echo   [OK] Optional audits complete.
echo.

rem ---------------------------------------------------------------------------
rem 7. Launch only through the canonical launcher. Do not duplicate its service
rem list here; that causes drift, duplicate daemons and chart/cache divergence.
rem ---------------------------------------------------------------------------
echo [7/7] Starting canonical Sentinuity launcher...
set "LAUNCHER="
if exist "%ROOT%\launch\Launch_Sentinuity.bat" set "LAUNCHER=%ROOT%\launch\Launch_Sentinuity.bat"
if not defined LAUNCHER if exist "%ROOT%\Launch_Sentinuity.bat" set "LAUNCHER=%ROOT%\Launch_Sentinuity.bat"

if not defined LAUNCHER (
  echo [FATAL] Canonical Launch_Sentinuity.bat was not found.
  pause
  exit /b 6
)

echo   Launcher: %LAUNCHER%
start "SENTINUITY SIGN-OFF LAUNCH" cmd /c ""%LAUNCHER%""

echo.
echo ============================================================
echo   RESTART HANDOFF COMPLETE
echo ============================================================
echo   Shutdown and prune completed before launch.
echo   DB quick_check passed.
echo   Historical cache and cadence chart warmup were attempted.
echo   Canonical launcher now owns service boot and reconciliation.
echo.
echo   Audit logs:
echo     %LOG%\restart_db_integrity.log
echo     %LOG%\restart_history_sync.log
echo     %LOG%\restart_chart_warmup.log
echo     %LOG%\restart_writer_verify.log
echo ============================================================
pause
exit /b 0

