@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "ROOT_PATH=%%~fI"
cd /d "%ROOT_PATH%"

set "PY=python"
if exist "%ROOT_PATH%\.venv\Scripts\python.exe" set "PY=%ROOT_PATH%\.venv\Scripts\python.exe"
if not exist "%ROOT_PATH%\logs" mkdir "%ROOT_PATH%\logs" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$rx='services[\\/\.](execution_engine|ingest_pipeline|market_intelligence|ws_price_oracle|neural_supervisor|system_guardian|sovereign_governor|substrate_portfolio_supervisor)|Launch_Sentinuity|sovereign_hub|streamlit';" ^
  "$busy=@(Get-CimInstance Win32_Process ^| Where-Object { [string]$_.CommandLine -match $rx });" ^
  "if($busy.Count -gt 0){ Write-Host '[BLOCKED] Trading/runtime processes are active. Shut Sentinuity down before Council build work.' -ForegroundColor Red; $busy ^| Select-Object ProcessId,Name,CommandLine ^| Format-Table -AutoSize; exit 23 }; exit 0"
if errorlevel 1 exit /b %errorlevel%

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dupe=@(Get-CimInstance Win32_Process ^| Where-Object { [string]$_.CommandLine -match 'services\.council_autobuilder' });" ^
  "if($dupe.Count -gt 0){ Write-Host '[BLOCKED] CouncilAutobuilder is already running.' -ForegroundColor Yellow; exit 24 }; exit 0"
if errorlevel 1 exit /b %errorlevel%

echo ============================================================
echo  SENTINUITY COUNCIL AUTOBUILDER - OFFLINE ONLY
echo ============================================================
echo  Trading services are confirmed stopped.
echo  Press Ctrl+C to stop when the offline build window is complete.
echo ============================================================

"%PY%" -m services.council_autobuilder >> "%ROOT_PATH%\logs\council_autobuilder_offline.log" 2>&1
set "RC=%ERRORLEVEL%"
echo CouncilAutobuilder exited with code %RC%.
exit /b %RC%
