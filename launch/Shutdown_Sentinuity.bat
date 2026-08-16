@echo off
setlocal EnableExtensions
title SENTINUITY HALT ENGINE V2 - GATEWAY LAST

set "ROOT_PATH=C:\Users\Polar\.openclaw\workspace\trading-bot"
set "LOG_PATH=%ROOT_PATH%\logs"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%ROOT_PATH%" (
  echo [FAIL] Root path not found: %ROOT_PATH%
  pause
  exit /b 1
)

cd /d "%ROOT_PATH%"
if not exist "%LOG_PATH%" mkdir "%LOG_PATH%" >nul 2>&1

echo ============================================================
echo   SENTINUITY SIGN-OFF SHUTDOWN V2 - GATEWAY LAST
echo ============================================================
echo   Root: %ROOT_PATH%
echo   Time: %DATE% %TIME%
echo.
echo   This version restores the old working blunt kill path
echo   AND adds command-line/port verification for current infra.
echo ============================================================
echo.

rem ---------------------------------------------------------------------------
rem Optional admin elevation. Old Stop_All worked partly because it elevated,
rem which matters for cloudflared service and some wrapper processes.
rem ---------------------------------------------------------------------------
if /I not "%~1"=="--elevated" (
  net session >nul 2>&1
  if errorlevel 1 (
    echo [0] Requesting Administrator shutdown window...
    "%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
      echo   [WARN] Elevation failed or was cancelled. Continuing non-admin.
    ) else (
      echo   [OK] Elevated shutdown launched. This window will close.
      timeout /t 2 /nobreak >nul
      exit /b 0
    )
  )
)

rem ---------------------------------------------------------------------------
rem Write shutdown marker before killing anything. Failure is non-fatal.
rem ---------------------------------------------------------------------------
echo [1] Writing shutdown marker...
if not exist "%ROOT_PATH%\runtime" mkdir "%ROOT_PATH%\runtime" >nul 2>&1
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path '%ROOT_PATH%' 'runtime\shutdown_requested.marker'; Set-Content -LiteralPath $p -Value ('shutdown_requested_at=' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff zzz')) -Encoding UTF8" > "%LOG_PATH%\shutdown_marker_file.log" 2>&1
python -c "import sqlite3; con=sqlite3.connect(r'%ROOT_PATH%\sentinuity_matrix.db',timeout=10); con.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT)'); con.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('OPERATOR_SHUTDOWN_REQUESTED','1','Shutdown_Sentinuity V2')); con.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('LAUNCH_STATE','shutdown_requested','Shutdown_Sentinuity V2')); con.commit(); con.close()" > "%LOG_PATH%\shutdown_marker_db.log" 2>&1
if errorlevel 1 (
  echo   [WARN] DB marker write failed; file marker was still attempted.
) else (
  echo   [OK] Shutdown marker written.
)
echo.

rem ---------------------------------------------------------------------------
rem Stop service-style Cloudflare first. If not installed, fallback taskkill later.
rem ---------------------------------------------------------------------------
echo [2] Stopping tunnel service surfaces...
sc stop cloudflared >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
taskkill /F /IM ngrok.exe /T >nul 2>&1
echo   [OK] Tunnel image sweep complete. OpenClaw gateway is stopped last.
echo.

rem ---------------------------------------------------------------------------
rem Kill relaunch authorities/windows first. DO NOT target this window title.
rem ---------------------------------------------------------------------------
echo [3] Killing watchdog/restart/console windows by title...
for %%T in (
  "SENTINUITY GUARDIAN"
  "SENTINUITY WATCHDOG"
  "SENTINUITY SIGN-OFF LAUNCH"
  "SENTINUITY SOVEREIGN CONSOLE"
  "SENTINUITY SOVEREIGN TERMINAL"
  "SovHub"
  "Dashboard"
  "API Monitor"
  "Tunnel"
) do taskkill /F /FI "WINDOWTITLE eq %%~T" /T >nul 2>&1
echo   [OK] Relaunch/window authority sweep complete.
echo.

rem ---------------------------------------------------------------------------
rem Current-infra command-line sweep. This kills cmd /c and cmd /k wrappers that
rem survive after Python dies. It excludes this BAT's cmd parent and PS child.
rem ---------------------------------------------------------------------------
echo [4] Killing Sentinuity process trees by command line...
rem [SIGNOFF] Startup purge/reset intentionally removed from shutdown. Shutdown must never mutate/start pipeline rows.
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$root='%ROOT_PATH%'; $me=$PID; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)).ParentProcessId; $grand=0; try{$grand=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $parent)).ParentProcessId}catch{}; $exclude=@($me,$parent,$grand); $rx=([regex]::Escape($root) + '|sentinuity_matrix\.db|sentinuity_intelligence\.db|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|Stop_All|sovereign_hub|streamlit|cloudflared|services\.freshness_enforcer|services[\\/\.](pump_monitor|ingest_pipeline|market_intelligence|ws_price_oracle|neural_supervisor|execution_engine|system_guardian|sovereign_governor|sovereign_parameter_engine|replay_engine|polaris|code_vault|rolling_eviction|active_pipeline_cleaner|price_enricher|periodic_refresh|winner_snapshot_archiver|shadow_runner_tracker|wallet_scout|telegram_scout|x_scout|symbiotic_router|reconciliation_engine|council_build_orchestrator|intelligence_orchestrator|forge_code_writer|github_scout|openclaw_security_sentinel)'); $procs=Get-CimInstance Win32_Process | Where-Object { $exclude -notcontains $_.ProcessId -and ([string]$_.CommandLine -match $rx) } | Sort-Object ProcessId -Descending; foreach($p in $procs){ Write-Host ('KILL PID {0} {1}' -f $p.ProcessId,$p.Name); taskkill /PID $p.ProcessId /T /F | Out-Null }; Write-Host ('Killed command-line matches: {0}' -f @($procs).Count)" > "%LOG_PATH%\shutdown_cmdline_sweep.log" 2>&1
type "%LOG_PATH%\shutdown_cmdline_sweep.log"
echo.

rem ---------------------------------------------------------------------------
rem Restore the old working blunt kill. This is what the previous shutdown had
rem that the over-surgical version did not rely on enough.
rem ---------------------------------------------------------------------------
echo [5] Blunt Python/runtime image kill — old working behaviour restored...
taskkill /F /IM python.exe      /T >nul 2>&1
taskkill /F /IM pythonw.exe     /T >nul 2>&1
taskkill /F /IM py.exe          /T >nul 2>&1
taskkill /F /IM streamlit.exe   /T >nul 2>&1
taskkill /F /IM node.exe        /T >nul 2>&1
taskkill /F /IM npm.exe         /T >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
echo   [OK] Blunt runtime image kill complete.
echo.

rem ---------------------------------------------------------------------------
rem Legacy titles from the older known-working shutdown + newer services.
rem ---------------------------------------------------------------------------
echo [6] Legacy service-window sweep...
for %%T in (
  "CouncilBuild" "ShadowTracker" "SecuritySentinel"
  "PumpMon" "Ingest" "IngestPipeline" "MarketIntel" "MktIntel" "WsOracle" "Supervisor" "Executor" "ExecEngine"
  "SovGovernor" "SPE" "Replay" "Freshness" "FreshnessEnforcer" "RollEvict" "RollingEviction"
  "PriceEnricher" "PeriodicRefresh" "WinnerArchiver" "MacroPriceFeed" "MacroChannel"
  "WalletScout" "WalletScoutSvc" "TG Scout" "TelegramScout" "XScout" "SymbioticRouter" "Reconciler"
  "ForgeOrchestrator" "ForgeWriter" "GithubScout" "ForgeResearch" "Polaris" "PolarisAux" "Recon" "Vault"
  "Scout" "Resolver" "Weaver" "Oracle" "Qualifier" "ZombieRes" "HITL Bot" "Debate" "Health" "DB Prune" "Healer" "Substrate"
  "SENTINUITY-ingest" "SENTINUITY-resolver" "SENTINUITY-signal_engine" "SENTINUITY-qualifier" "SENTINUITY-price_enricher"
  "SENTINUITY-execution_engine" "SENTINUITY-sovereign_governor" "SENTINUITY-pump_monitor" "SENTINUITY-wallet_scout"
  "SENTINUITY-telegram_scout" "SENTINUITY-neural_supervisor" "SENTINUITY-sovereign_parameter_engine" "SENTINUITY-replay_engine"
  "SENTINUITY-polaris_researcher" "SENTINUITY-polaris_reflection" "SENTINUITY-polaris_reviewer" "SENTINUITY-polaris_calibrator"
  "SENTINUITY-polaris_messenger" "SENTINUITY-polaris_channel_analyst" "SENTINUITY-market_intelligence" "SENTINUITY-ingest_pipeline" "SENTINUITY-system_guardian"
  "WD-scout" "WD-ingest" "WD-resolver" "WD-signal_engine" "WD-oracle" "WD-token_qualifier" "WD-supervisor" "WD-paper_executor"
  "WD-zombie_resolver" "WD-polaris" "WD-polaris_reviewer" "WD-polaris_calibrator" "WD-polaris_reflection" "WD-health_monitor"
  "WD-db_prune_guard" "WD-auto_healer" "WD-debate_engine" "WD-polaris_researcher" "WD-replay_engine" "WD-wallet_scout"
) do taskkill /F /FI "WINDOWTITLE eq %%~T" /T >nul 2>&1
echo   [OK] Legacy window sweep complete.
echo.

rem ---------------------------------------------------------------------------
rem Port sweep for dashboard/API/gateway. 8501 is always Sentinuity dashboard.
rem ---------------------------------------------------------------------------
echo [7] Port sweep for dashboard/API surfaces...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$ports=@(8501,8502,8766,8000,8080,3000,5000,7860); foreach($port in $ports){ Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $pid2=$_.OwningProcess; if($pid2 -and $pid2 -ne $PID){ Write-Host ('PORT KILL :{0} PID {1}' -f $port,$pid2); taskkill /PID $pid2 /T /F | Out-Null } } }" > "%LOG_PATH%\shutdown_port_sweep.log" 2>&1
type "%LOG_PATH%\shutdown_port_sweep.log"
echo.

rem ---------------------------------------------------------------------------
rem OpenClaw deliberately remains up until the very end. Sentinuity workers are
rem already down; keeping the gateway until after retention preserves the
rem operator-visible graceful "gateway stop" confirmation as the final service
rem action and avoids racing the slower supervised gateway stop against DB trim.
rem ---------------------------------------------------------------------------
echo [8] Sentinuity workers down. OpenClaw gateway intentionally remains until final step.
echo.

echo [9] Waiting for Sentinuity DB handles to release...
timeout /t 5 /nobreak >nul
echo.

rem ---------------------------------------------------------------------------
rem WAL checkpoint after process kill, not before. Old shutdown said checkpointed
rem before shutdown, but that can fail silently while services hold the DB.
rem ---------------------------------------------------------------------------
echo [10] WAL checkpoint after shutdown...
python -c "import sqlite3; con=sqlite3.connect('sentinuity_matrix.db',timeout=15); print(con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()); con.close()" > "%LOG_PATH%\shutdown_wal_checkpoint.log" 2>&1
if errorlevel 1 (echo   [WARN] WAL checkpoint failed or DB still locked. See logs\shutdown_wal_checkpoint.log) else (echo   [OK] WAL checkpoint complete.)
echo.

rem ---------------------------------------------------------------------------
rem Verification. If anything remains, print it and attempt one final PID kill.
rem ---------------------------------------------------------------------------
echo [11] Final verification and kill-if-needed...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$root='%ROOT_PATH%'; $me=$PID; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)).ParentProcessId; $rx=([regex]::Escape($root) + '|sentinuity_matrix\.db|sentinuity_intelligence\.db|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|sovereign_hub|streamlit|cloudflared|services\.freshness_enforcer|services[\\/\.](execution_engine|market_intelligence|neural_supervisor|pump_monitor|ws_price_oracle|system_guardian|sovereign_governor|polaris|replay_engine|sovereign_parameter_engine|code_vault)'); $left=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.ProcessId -ne $parent -and ([string]$_.CommandLine -match $rx) }; if(@($left).Count -eq 0){ Write-Host '[OK] No Sentinuity matched processes remain.'; exit 0 }; Write-Host ('[WARN] Remaining matched processes: {0}' -f @($left).Count); $left | Select-Object ProcessId,Name,CommandLine | Format-List; foreach($p in $left){ taskkill /PID $p.ProcessId /T /F | Out-Null }; Start-Sleep -Seconds 2; $left2=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.ProcessId -ne $parent -and ([string]$_.CommandLine -match $rx) }; if(@($left2).Count -eq 0){ Write-Host '[OK] Remaining matches killed on final pass.'; exit 0 }; Write-Host '[FAIL] Some processes resisted shutdown:'; $left2 | Select-Object ProcessId,Name,CommandLine | Format-List; exit 2" > "%LOG_PATH%\shutdown_verify.log" 2>&1
type "%LOG_PATH%\shutdown_verify.log"
if errorlevel 2 (
  echo.
  echo [FAIL] Some processes resisted shutdown. Check logs\shutdown_verify.log
  echo        Run this BAT once more as Administrator if needed.
) else (
  echo.
  echo [OK] Shutdown verified.
)

echo.
rem ---------------------------------------------------------------------------
rem DB RETENTION TRIM - runs only after every Sentinuity process is confirmed down.
rem The retention tool lives in launch\ so this BAT is only orchestration, not
rem database logic. It moves cold archive/vault bloat to sentinuity_archive.db,
rem writes logs\db_retention\retention_latest.json, and refuses live services.
rem Non-fatal: a trim failure never blocks shutdown completion.
rem ---------------------------------------------------------------------------
echo [11c] Synchronising closed-trade history into intelligence cache...
python "%ROOT_PATH%\launch\sync_historical_trade_cache.py" --db "%ROOT_PATH%\sentinuity_matrix.db" --intel "%ROOT_PATH%\sentinuity_intelligence.db" >> "%LOG_PATH%\historical_trade_cache_sync.log" 2>&1
if errorlevel 1 (echo   [WARN] Historical cache sync failed. See logs\historical_trade_cache_sync.log) else (echo   [OK] Historical trade cache synchronised.)
echo.

echo [12] DB retention trim - archive cold bloat out of hot DB...
if not exist "%LOG_PATH%\db_retention" mkdir "%LOG_PATH%\db_retention" >nul 2>&1
if exist "%ROOT_PATH%\launch\db_retention_trim.py" (
  rem Let recently-killed service heartbeats age beyond the retention live-window.
  timeout /t 16 /nobreak >nul
  python "%ROOT_PATH%\launch\db_retention_trim.py" --db "%ROOT_PATH%\sentinuity_matrix.db" --archive "%ROOT_PATH%\sentinuity_archive.db" --apply --vacuum-auto --target-mb 10 --json "%LOG_PATH%\db_retention\retention_shutdown_latest.json" >> "%LOG_PATH%\db_retention_shutdown.log" 2>&1
  if errorlevel 1 (
    echo   [WARN] DB retention reported an issue. See logs\db_retention_shutdown.log
  ) else (
    echo   [OK] DB retention complete. Archive locator: logs\db_retention\retention_latest.json
  )
) else (
  echo   [SKIP] launch\db_retention_trim.py not found - skipping DB retention.
)
echo.

echo [12b] Post-retention DB quick_check + WAL checkpoint...
python -c "import sqlite3, pathlib; db='sentinuity_matrix.db'; con=sqlite3.connect(db,timeout=15); print('quick_check', con.execute('PRAGMA quick_check').fetchone()[0]); print('checkpoint', con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()); con.close(); print('db_mb', round(pathlib.Path(db).stat().st_size/1024/1024,2))" > "%LOG_PATH%\shutdown_post_retention_db_check.log" 2>&1
type "%LOG_PATH%\shutdown_post_retention_db_check.log"
if errorlevel 1 (echo   [WARN] Post-retention DB check failed. See logs\shutdown_post_retention_db_check.log) else (echo   [OK] Post-retention DB check complete.)
echo.

rem ---------------------------------------------------------------------------
rem GRACEFUL OPENCLAW GATEWAY STOP - final service action only.
rem 1) Let the OpenClaw CLI finish and show its own stopped confirmation.
rem 2) Immediately end the registered scheduled task as the supervisor fallback.
rem 3) Only then use the process-image fallback.
rem ---------------------------------------------------------------------------
echo [13] Final OpenClaw gateway shutdown...
where openclaw >nul 2>&1
if not errorlevel 1 (
  echo   Running: openclaw gateway stop
  echo   ------------------------------------------------------------
  openclaw gateway stop
  set "OPENCLAW_STOP_RC=%ERRORLEVEL%"
  echo   ------------------------------------------------------------
  if "%OPENCLAW_STOP_RC%"=="0" (
    echo   [OK] OpenClaw gateway stop command completed.
  ) else (
    echo   [WARN] OpenClaw gateway stop returned %OPENCLAW_STOP_RC%.
    echo          Scheduled-task fallback will run immediately.
  )
) else (
  echo   [WARN] openclaw is not on PATH. Scheduled-task fallback will run.
)

echo   Running immediately after gateway-stop completion:
echo   schtasks /End /TN "OpenClaw Gateway"
schtasks /End /TN "OpenClaw Gateway"
if errorlevel 1 (
  echo   [INFO] Scheduled task was already stopped, absent, or could not be ended.
) else (
  echo   [OK] OpenClaw Gateway scheduled task terminated.
)

timeout /t 2 /nobreak >nul
taskkill /F /IM openclaw.exe /T >nul 2>&1
echo   [OK] Final openclaw.exe residual-process sweep complete.
echo.

echo ============================================================
echo   SENTINUITY SHUTDOWN V2 COMPLETE
echo ============================================================
echo   DB retention: launch\db_retention_trim.py
echo   Archive DB:   %ROOT_PATH%\sentinuity_archive.db
echo   Manifest:     %LOG_PATH%\db_retention\retention_latest.json
echo   Logs:         %LOG_PATH%
echo.
echo   Verification command:
echo   Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -match "sentinuity^|sovereign_hub^|execution_engine^|market_intelligence^|neural_supervisor^|pump_monitor^|ws_price_oracle^|freshness_enforcer^|Launch_Sentinuity^|Watchdog_Sentinuity" } ^| Select ProcessId,Name,CommandLine
echo ============================================================
echo.

if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
exit /b 0
