@echo off
setlocal EnableExtensions EnableDelayedExpansion
title SENTINUITY OFFLINE RETENTION — DIRECT

for %%I in ("%~dp0..") do set "ROOT_PATH=%%~fI"
set "LOG_PATH=%ROOT_PATH%\logs"
cd /d "%ROOT_PATH%"

if not exist "%LOG_PATH%" mkdir "%LOG_PATH%" >nul 2>&1
if not exist "%LOG_PATH%\db_retention" mkdir "%LOG_PATH%\db_retention" >nul 2>&1

set "PY_EXE="
for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PY_EXE set "PY_EXE=%%P"
if not defined PY_EXE for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PY_EXE set "PY_EXE=%%P"

if not defined PY_EXE (
  echo [FAIL] Python could not be resolved.
  pause
  exit /b 5
)

echo ============================================================
echo  SENTINUITY DIRECT OFFLINE MATRIX RETENTION
echo ============================================================
echo Root:   %ROOT_PATH%
echo Python: %PY_EXE%
echo.

echo [1/5] Refusing to run if Sentinuity processes still exist...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$root='%ROOT_PATH%'; $rx=([regex]::Escape($root)+'|sentinuity_matrix\.db|services[\\/\.](execution_engine|ws_price_oracle|system_guardian|active_pipeline_cleaner|freshness_enforcer)|sovereign_hub|streamlit'); $p=Get-CimInstance Win32_Process ^| Where-Object { ([string]$_.CommandLine -match $rx) -and $_.ProcessId -ne $PID }; if(@($p).Count){$p ^| Select ProcessId,Name,CommandLine ^| Format-List; exit 2}else{exit 0}"
if errorlevel 2 (
  echo [FAIL] Matching processes still exist. Run Shutdown_Sentinuity.bat first.
  pause
  exit /b 2
)
echo [PASS] No matching runtime processes.
echo.

echo [2/5] Database quick_check before retention...
"%PY_EXE%" -c "import sqlite3; c=sqlite3.connect(r'%ROOT_PATH%\sentinuity_matrix.db',timeout=30); print('quick_check='+str(c.execute('PRAGMA quick_check').fetchone()[0])); c.close()"
if errorlevel 1 (
  echo [FAIL] Pre-retention quick_check failed.
  pause
  exit /b 3
)
echo.

echo [3/5] Running archive-first retention and VACUUM...
"%PY_EXE%" "%ROOT_PATH%\launch\db_retention_trim.py" ^
  --db "%ROOT_PATH%\sentinuity_matrix.db" ^
  --archive "%ROOT_PATH%\sentinuity_archive.db" ^
  --apply --vacuum ^
  --target-mb 12 --max-safe-mb 20 ^
  --heartbeat-grace-seconds 12 --keep-backups 3 ^
  --json "%LOG_PATH%\db_retention\matrix_retention_v8_latest.json" ^
  > "%LOG_PATH%\db_retention_matrix_v8.log" 2>&1

set "RC=!ERRORLEVEL!"
type "%LOG_PATH%\db_retention_matrix_v8.log"
echo.
if not "!RC!"=="0" (
  echo [FAIL] Retention returned code !RC!.
  echo Review:
  echo   %LOG_PATH%\db_retention_matrix_v8.log
  echo   %LOG_PATH%\db_retention\matrix_retention_v8_latest.json
  pause
  exit /b !RC!
)

echo [4/5] Verifying size and integrity...
"%PY_EXE%" -c "import pathlib,sqlite3,sys; r=pathlib.Path(r'%ROOT_PATH%'); p=r/'sentinuity_matrix.db'; parts=[p,r/'sentinuity_matrix.db-wal',r/'sentinuity_matrix.db-shm']; s={x.name:(x.stat().st_size/1048576 if x.exists() else 0) for x in parts}; total=sum(s.values()); c=sqlite3.connect(p,timeout=30); qc=c.execute('PRAGMA quick_check').fetchone()[0]; c.close(); print('sizes_mb='+str({k:round(v,2) for k,v in s.items()})); print('total_footprint_mb='+str(round(total,2))); print('quick_check='+str(qc)); sys.exit(0 if qc=='ok' and total<=20 else 4)"
set "VERIFY_RC=!ERRORLEVEL!"
if not "!VERIFY_RC!"=="0" (
  echo [FAIL] Database is still above 20 MB or integrity check failed.
  pause
  exit /b !VERIFY_RC!
)

echo.
echo [5/5] COMPLETE — matrix retention passed.
pause
exit /b 0
