@echo off
setlocal EnableExtensions
title SENTINUITY HALT ENGINE V9 + RETENTION V8.1

rem WIRING_FIX_20260723: resolve root relative to this script (portable for
rem the V3 GitHub release); the absolute path survives only as a fallback.
for %%I in ("%~dp0..") do set "ROOT_PATH=%%~fI"
if not exist "%ROOT_PATH%\services" set "ROOT_PATH=C:\Users\Polar\.openclaw\workspace\trading-bot"
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
echo   SENTINUITY SIGN-OFF SHUTDOWN V9 + RETENTION V8.1
echo ============================================================
echo   Root: %ROOT_PATH%
echo   Time: %DATE% %TIME%
echo.
echo   This version restores the old working blunt kill path
echo   AND adds command-line/port verification for current infra.
echo ============================================================
echo.

rem ---------------------------------------------------------------------------
rem Optional administrator elevation. The parent waits for the elevated child
rem and returns the child exit code; audit callers cannot race ahead.
rem ---------------------------------------------------------------------------
if /I not "%~1"=="--elevated" (
  net session >nul 2>&1
  if errorlevel 1 (
    echo [0] Requesting Administrator shutdown window and waiting for completion...
    "%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$p=Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    set "ELEVATED_RC=%ERRORLEVEL%"
    if not "%ELEVATED_RC%"=="0" (
      echo [FAIL] Elevated shutdown returned code %ELEVATED_RC%.
    )
    exit /b %ELEVATED_RC%
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
  "Gateway"
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
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$root='%ROOT_PATH%'; $me=$PID; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)).ParentProcessId; $grand=0; try{$grand=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $parent)).ParentProcessId}catch{}; $exclude=@($me,$parent,$grand); $rx=([regex]::Escape($root) + '|sentinuity_matrix\.db|sentinuity_intelligence\.db|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|Stop_All|sovereign_hub|streamlit|cloudflared|services\.freshness_enforcer|services[\\/\.](pump_monitor|ingest_pipeline|market_intelligence|ws_price_oracle|neural_supervisor|execution_engine|system_guardian|sovereign_governor|sovereign_parameter_engine|replay_engine|polaris|code_vault|rolling_eviction|active_pipeline_cleaner|price_enricher|periodic_refresh|winner_snapshot_archiver|shadow_runner_tracker|wallet_scout|telegram_scout|x_scout|symbiotic_router|reconciliation_engine|live_settlement_recovery|council_build_orchestrator|intelligence_orchestrator|forge_code_writer|github_scout|openclaw_security_sentinel|polaris_auxiliary|copytrade_shadow_scanner|smart_wallet_trade_ingester|substrate_opportunity_scanner|substrate_portfolio_supervisor|substrate_copytrade_bridge_loop|substrate_paper_trader|macro_channel|macro_price_feed|paper_wallet_refresher|council_chamber_bridge|market_tide|signal_gate)'); $procs=Get-CimInstance Win32_Process | Where-Object { $exclude -notcontains $_.ProcessId -and ([string]$_.CommandLine -match $rx) } | Sort-Object ProcessId -Descending; foreach($p in $procs){ Write-Host ('KILL PID {0} {1}' -f $p.ProcessId,$p.Name); taskkill /PID $p.ProcessId /T /F | Out-Null }; Write-Host ('Killed command-line matches: {0}' -f @($procs).Count)" > "%LOG_PATH%\shutdown_cmdline_sweep.log" 2>&1
type "%LOG_PATH%\shutdown_cmdline_sweep.log"
echo.

rem ---------------------------------------------------------------------------
rem Restore the old working blunt kill. This is what the previous shutdown had
rem that the over-surgical version did not rely on enough.
rem ---------------------------------------------------------------------------
echo [5] Blunt Python/runtime image kill ??? old working behaviour restored...
taskkill /F /IM python.exe      /T >nul 2>&1
taskkill /F /IM pythonw.exe     /T >nul 2>&1
taskkill /F /IM py.exe          /T >nul 2>&1
taskkill /F /IM streamlit.exe   /T >nul 2>&1
taskkill /F /IM node.exe        /T >nul 2>&1
taskkill /F /IM npm.exe         /T >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
taskkill /F /IM openclaw.exe    /T >nul 2>&1
echo   [OK] Blunt runtime image kill complete.
echo.

rem ---------------------------------------------------------------------------
rem Legacy titles from the older known-working shutdown + newer services.
rem ---------------------------------------------------------------------------
echo [6] Legacy service-window sweep...
for %%T in (
  "CouncilBuild" "CouncilChamber" "ShadowTracker" "SecuritySentinel" "OpenClaw Security Sentinel"
  "ActivePipelineCleaner10m" "CopytradeScanner" "SmartWalletTradeIngester" "PaperWalletRefresher"
  "SubstrateScanner" "SubstrateSupervisor" "SubstrateCopyBridge" "SubstrateTrader" "MarketTide" "SignalGate" "SysGuardian" "SENTINUITY WATCH"
  "PumpMon" "Ingest" "IngestPipeline" "MarketIntel" "MktIntel" "WsOracle" "Supervisor" "Executor" "ExecEngine"
  "SovGovernor" "SPE" "Replay" "Freshness" "FreshnessEnforcer" "RollEvict" "RollingEviction"
  "PriceEnricher" "PeriodicRefresh" "WinnerArchiver" "MacroPriceFeed" "MacroChannel"
  "WalletScout" "WalletScoutSvc" "TG Scout" "TelegramScout" "XScout" "SymbioticRouter" "Reconciler" "SettleRecovery"
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
rem Stop OpenClaw gateway / scheduled supervisor before DB retention so it cannot
rem respawn workers while handles are being released.
rem ---------------------------------------------------------------------------
echo [8] Stopping OpenClaw gateway/scheduled task before DB hygiene...
schtasks /End /TN "OpenClaw Gateway" >nul 2>&1
where openclaw >nul 2>&1
if not errorlevel 1 (
  openclaw gateway stop >nul 2>&1
  echo   [OK] openclaw gateway stop issued before DB hygiene.
) else (
  echo   [SKIP] openclaw not on PATH.
)
taskkill /F /IM openclaw.exe /T >nul 2>&1
echo   [OK] OpenClaw gateway/image fallback complete.
echo.

echo [9] Waiting 15 seconds for DB handles and heartbeats to expire...
timeout /t 15 /nobreak >nul
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
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$root='%ROOT_PATH%'; $me=$PID; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)).ParentProcessId; $rx=([regex]::Escape($root) + '|sentinuity_matrix\.db|sentinuity_intelligence\.db|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|sovereign_hub|streamlit|cloudflared|services\.freshness_enforcer|services[\\/\.](execution_engine|market_intelligence|neural_supervisor|pump_monitor|ws_price_oracle|system_guardian|sovereign_governor|polaris|replay_engine|sovereign_parameter_engine|code_vault|live_settlement_recovery)'); $left=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.ProcessId -ne $parent -and ([string]$_.CommandLine -match $rx) }; if(@($left).Count -eq 0){ Write-Host '[OK] No Sentinuity matched processes remain.'; exit 0 }; Write-Host ('[WARN] Remaining matched processes: {0}' -f @($left).Count); $left | Select-Object ProcessId,Name,CommandLine | Format-List; foreach($p in $left){ taskkill /PID $p.ProcessId /T /F | Out-Null }; Start-Sleep -Seconds 2; $left2=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.ProcessId -ne $parent -and ([string]$_.CommandLine -match $rx) }; if(@($left2).Count -eq 0){ Write-Host '[OK] Remaining matches killed on final pass.'; exit 0 }; Write-Host '[FAIL] Some processes resisted shutdown:'; $left2 | Select-Object ProcessId,Name,CommandLine | Format-List; exit 2" > "%LOG_PATH%\shutdown_verify.log" 2>&1
type "%LOG_PATH%\shutdown_verify.log"
if errorlevel 2 (
  echo.
  echo [FAIL] Some processes resisted shutdown. Check logs\shutdown_verify.log
  echo        Retention will not run while a process may still hold the databases.
  if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
  exit /b 3
) else (
  echo.
  echo [OK] Shutdown verified.
)

echo.
rem ---------------------------------------------------------------------------
rem DB RETENTION / ARCHIVAL / VACUUM V6
rem Runs only after the final process verification. Every removed row is first
rem archived, active positions and durable state are protected, and a complete
rem pre-prune SQLite backup is created.
rem ---------------------------------------------------------------------------
echo [11c] Synchronising closed-trade history into intelligence cache...
python "%ROOT_PATH%\launch\sync_historical_trade_cache.py" --db "%ROOT_PATH%\sentinuity_matrix.db" --intel "%ROOT_PATH%\sentinuity_intelligence.db" >> "%LOG_PATH%\historical_trade_cache_sync.log" 2>&1
if errorlevel 1 (
  echo   [WARN] Historical cache sync failed. See logs\historical_trade_cache_sync.log
) else (
  echo   [OK] Historical trade cache synchronised.
)
echo.

echo [12] Canonical offline retention V8.1 - matrix database...
if not exist "%LOG_PATH%\db_retention" mkdir "%LOG_PATH%\db_retention" >nul 2>&1

if not exist "%ROOT_PATH%\launch\db_retention_trim.py" (
  echo   [FAIL] launch\db_retention_trim.py is missing.
  set "PRUNE_FAILED=1"
) else (
  timeout /t 8 /nobreak >nul
  python "%ROOT_PATH%\launch\db_retention_trim.py" ^
    --db "%ROOT_PATH%\sentinuity_matrix.db" ^
    --archive "%ROOT_PATH%\sentinuity_archive.db" ^
    --apply --vacuum ^
    --target-mb 15 --max-safe-mb 20 ^
    --heartbeat-grace-seconds 12 --keep-backups 3 ^
    --json "%LOG_PATH%\db_retention\matrix_retention_v8_latest.json" ^
    > "%LOG_PATH%\db_retention_matrix_v8.log" 2>&1

  set "PRUNE_RC=%ERRORLEVEL%"
  type "%LOG_PATH%\db_retention_matrix_v8.log"

  if "%PRUNE_RC%"=="0" (
    echo   [OK] Matrix retention V8.1 complete.
  ) else (
    echo   [FAIL] Matrix retention V8.1 returned code %PRUNE_RC%.
    echo          Sentinuity remains shut down.
    set "PRUNE_FAILED=1"
  )
)
echo.

echo [12a] Canonical offline retention V8.1 - intelligence database...
if exist "%ROOT_PATH%\sentinuity_intelligence.db" (
  python "%ROOT_PATH%\launch\db_retention_trim.py" ^
    --db "%ROOT_PATH%\sentinuity_intelligence.db" ^
    --archive "%ROOT_PATH%\sentinuity_intelligence_archive.db" ^
    --apply --vacuum ^
    --target-mb 20 --max-safe-mb 35 ^
    --heartbeat-grace-seconds 12 --keep-backups 3 ^
    --json "%LOG_PATH%\db_retention\intelligence_retention_v8_latest.json" ^
    > "%LOG_PATH%\db_retention_intelligence_v8.log" 2>&1

  set "INTEL_PRUNE_RC=%ERRORLEVEL%"
  type "%LOG_PATH%\db_retention_intelligence_v8.log"

  if "%INTEL_PRUNE_RC%"=="0" (
    echo   [OK] Intelligence retention V8.1 complete.
  ) else (
    echo   [FAIL] Intelligence retention V8.1 returned code %INTEL_PRUNE_RC%.
    set "PRUNE_FAILED=1"
  )
) else (
  echo   [SKIP] sentinuity_intelligence.db not present.
)
echo.

echo [12b] Post-retention integrity and size verification...
python -c "import pathlib,sqlite3; root=pathlib.Path(r'%ROOT_PATH%'); names=('sentinuity_matrix.db','sentinuity_intelligence.db'); [(lambda p: print(p.name,'quick_check='+str((lambda c:(c.execute('PRAGMA quick_check').fetchone()[0],c.close())[0])(sqlite3.connect(p,timeout=30))),'size_mb='+str(round(p.stat().st_size/1048576,2))))(root/n) for n in names if (root/n).exists()]" > "%LOG_PATH%\shutdown_post_retention_db_check.log" 2>&1
type "%LOG_PATH%\shutdown_post_retention_db_check.log"
if errorlevel 1 (
  echo   [FAIL] Post-retention DB integrity check failed.
  set "PRUNE_FAILED=1"
) else (
  echo   [OK] Post-retention DB integrity check complete.
)
echo.

echo [12c] Enforcing matrix hot-DB target ceiling...
python -c "import pathlib,sys; p=pathlib.Path(r'%ROOT_PATH%\sentinuity_matrix.db'); mb=p.stat().st_size/1048576; print('matrix_size_mb='+str(round(mb,2))); sys.exit(0 if mb<=20 else 4)" > "%LOG_PATH%\shutdown_matrix_target_gate.log" 2>&1
type "%LOG_PATH%\shutdown_matrix_target_gate.log"
if errorlevel 4 (
  echo   [FAIL] Matrix DB remains above the 20 MB shutdown ceiling.
  echo          Sentinuity remains shut down; inspect the V8 JSON top_objects_after list.
  set "PRUNE_FAILED=1"
) else (
  echo   [OK] Matrix DB is inside the signed-off 20 MB ceiling.
)
echo.

rem ---------------------------------------------------------------------------
rem GRACEFUL OPENCLAW GATEWAY STOP - must be the final action, after the whole
rem codebase is confirmed down and the DB is trimmed. The gateway can supervise
rem and respawn worker processes, so it is stopped last to avoid relaunch races.
rem ---------------------------------------------------------------------------
echo [13] Final OpenClaw image fallback...
where openclaw >nul 2>&1
if not errorlevel 1 (
  openclaw gateway stop >nul 2>&1
  echo   [OK] openclaw gateway stop issued.
) else (
  echo   [SKIP] openclaw not on PATH - using final image fallback only.
)
taskkill /F /IM openclaw.exe /T >nul 2>&1
echo   [OK] Final openclaw.exe image fallback complete.
echo.
echo ============================================================
echo   SENTINUITY SHUTDOWN V9 + RETENTION V8.1 COMPLETE
echo ============================================================
echo   DB retention: launch\db_retention_trim.py (V8.1 schema-sync + archive + vacuum)
echo   Archive DB:   %ROOT_PATH%\sentinuity_archive.db
echo   Matrix report: %LOG_PATH%\db_retention\matrix_retention_v8_latest.json
echo   Intel report:  %LOG_PATH%\db_retention\intelligence_retention_v8_latest.json
echo   Logs:         %LOG_PATH%
echo.
echo   Verification command:
echo   Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -match "sentinuity^|sovereign_hub^|execution_engine^|market_intelligence^|neural_supervisor^|pump_monitor^|ws_price_oracle^|freshness_enforcer^|Launch_Sentinuity^|Watchdog_Sentinuity" } ^| Select ProcessId,Name,CommandLine
echo ============================================================
echo.

if defined PRUNE_FAILED (
  echo.
  echo [WARN] All Sentinuity processes were shut down, but DB compaction/check reported a failure.
  echo        Review the logs above before the next launch.
  if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
  exit /b 2
)

if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
exit /b 0


