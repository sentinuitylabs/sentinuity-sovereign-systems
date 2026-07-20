@echo off
setlocal EnableExtensions
title SENTINUITY PUBLIC PAPER - SAFE STOP

set "ROOT_PATH=%~dp0.."
for %%I in ("%ROOT_PATH%") do set "ROOT_PATH=%%~fI"
set "LOG_PATH=%ROOT_PATH%\logs"
set "RUNTIME_PATH=%ROOT_PATH%\runtime"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

cd /d "%ROOT_PATH%" || (
  echo [FAIL] Could not resolve Sentinuity root: %ROOT_PATH%
  exit /b 1
)
if not exist "%LOG_PATH%" mkdir "%LOG_PATH%" >nul 2>&1
if not exist "%RUNTIME_PATH%" mkdir "%RUNTIME_PATH%" >nul 2>&1

if exist "%ROOT_PATH%\.venv\Scripts\python.exe" (
  set "PY=%ROOT_PATH%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo ============================================================
echo  SENTINUITY V2 PUBLIC PAPER - SAFE REPOSITORY-SCOPED STOP
echo ============================================================
echo  Root: %ROOT_PATH%
echo.

rem Signal cooperative shutdown first. Failure is non-fatal.
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p=Join-Path '%RUNTIME_PATH%' 'shutdown_requested.marker'; Set-Content -LiteralPath $p -Value ('shutdown_requested_at=' + (Get-Date -Format 'o')) -Encoding UTF8" ^
  > "%LOG_PATH%\shutdown_marker.log" 2>&1

if exist "%ROOT_PATH%\sentinuity_matrix.db" (
  "%PY%" -c "import sqlite3,time; p=r'%ROOT_PATH%\sentinuity_matrix.db'; c=sqlite3.connect(p,timeout=5); c.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT)'); c.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('OPERATOR_SHUTDOWN_REQUESTED','1','Public paper safe stop')); c.execute('INSERT OR REPLACE INTO system_config VALUES (?,?,?)',('LAUNCH_STATE','shutdown_requested','Public paper safe stop')); c.commit(); c.close()" ^
    > "%LOG_PATH%\shutdown_db_marker.log" 2>&1
)

echo [1/4] Requesting cooperative stop...
timeout /t 3 /nobreak >nul

echo [2/4] Stopping only processes launched from this repository...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[regex]::Escape('%ROOT_PATH%');" ^
  "$self=$PID; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)).ParentProcessId;" ^
  "$matches=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $self -and $_.ProcessId -ne $parent -and ([string]$_.CommandLine -match $root) };" ^
  "$matches | ForEach-Object { Write-Host ('STOP PID {0} {1}' -f $_.ProcessId,$_.Name); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Write-Host ('Repository-scoped processes stopped: {0}' -f @($matches).Count)" ^
  > "%LOG_PATH%\shutdown_processes.log" 2>&1
type "%LOG_PATH%\shutdown_processes.log"

echo [3/4] Releasing Sentinuity-owned localhost ports...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[regex]::Escape('%ROOT_PATH%'); foreach($port in 8501,8766){ Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $p=Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess) -ErrorAction SilentlyContinue; if($p -and ([string]$p.CommandLine -match $root)){ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host ('Released port {0} PID {1}' -f $port,$_.OwningProcess) } } }" ^
  > "%LOG_PATH%\shutdown_ports.log" 2>&1
type "%LOG_PATH%\shutdown_ports.log"

if exist "%ROOT_PATH%\sentinuity_matrix.db" (
  "%PY%" -c "import sqlite3; p=r'%ROOT_PATH%\sentinuity_matrix.db'; c=sqlite3.connect(p,timeout=10); print(c.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchall()); c.close()" ^
    > "%LOG_PATH%\shutdown_checkpoint.log" 2>&1
)

echo [4/4] Auditing and safely compacting offline databases...
"%PY%" "%ROOT_PATH%\tools\audit_db_growth.py" > "%LOG_PATH%\shutdown_db_growth_before.json" 2>&1
"%PY%" "%ROOT_PATH%\tools\shutdown_maintenance.py" > "%LOG_PATH%\shutdown_retention.log" 2>&1
set "MAINT_RC=%ERRORLEVEL%"
type "%LOG_PATH%\shutdown_retention.log"
if "%MAINT_RC%"=="11" echo [HOLD] Prune safely skipped because an open position remains.
if not "%MAINT_RC%"=="0" if not "%MAINT_RC%"=="11" echo [ATTENTION] Maintenance did not reach clean PASS. Review the log.


echo.
echo [PASS] Sentinuity processes have been stopped.
echo Other Python applications, tunnels, and unrelated services were not touched.
exit /b 0
