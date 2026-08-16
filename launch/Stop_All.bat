@echo off
setlocal EnableExtensions

rem ============================================================================
rem SENTINUITY STOP_ALL — SIGN-OFF REPLACEMENT 2026-07-30
rem Clean shutdown only. No startup purge, no position reset, no relaunch logic.
rem ============================================================================

for %%I in ("%~dp0..") do set "ROOT_PATH=%%~fI"
if not exist "%ROOT_PATH%\services" set "ROOT_PATH=C:\Users\Polar\.openclaw\workspace\trading-bot"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

cd /d "%ROOT_PATH%"
title SENTINUITY - STOPPING ALL

echo ============================================================
echo  SENTINUITY CLEAN SHUTDOWN
echo  Root: %ROOT_PATH%
echo ============================================================
echo.

rem Stop the optional Cloudflare/OpenClaw outer services first. Best effort only.
echo [1/4] Stopping external launch authorities...
where openclaw >nul 2>&1
if not errorlevel 1 openclaw gateway stop >nul 2>&1
schtasks /End /TN "OpenClaw Gateway" >nul 2>&1
sc stop cloudflared >nul 2>&1
taskkill /F /IM cloudflared.exe /T >nul 2>&1
echo   Done.
echo.

rem Pass 1: kill restart/watchdog authorities before ordinary workers.
echo [2/4] Stopping watchdogs, guardians and launch consoles...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
 "$root=[regex]::Escape('%ROOT_PATH%');" ^
 "$rx='Watchdog_Sentinuity|Sentinuity_Watch|Launch_Sentinuity|Restart_Sentinuity|services[\\/.](sentinuity_watch|system_guardian|sovereign_governor|polaris|polaris_auxiliary|reconnaissance_engine|master_console)';" ^
 "$procs=Get-CimInstance Win32_Process ^| Where-Object { $_.ProcessId -ne $PID -and ([string]$_.CommandLine -match $root) -and ([string]$_.CommandLine -match $rx) };" ^
 "foreach($p in $procs){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} };" ^
 "Write-Host ('  Stopped authority processes: '+@($procs).Count)"

timeout /t 2 /nobreak >nul

rem Pass 2: kill every remaining process whose command line belongs to this repo.
echo [3/4] Stopping remaining Sentinuity process trees...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
 "$root=[regex]::Escape('%ROOT_PATH%');" ^
 "$rx='python|pythonw|py.exe|streamlit|cmd.exe|powershell.exe|node.exe';" ^
 "$procs=Get-CimInstance Win32_Process ^| Where-Object { $_.ProcessId -ne $PID -and ([string]$_.CommandLine -match $root) -and ([string]$_.Name -match $rx) };" ^
 "$ordered=$procs ^| Sort-Object @{Expression={if($_.Name -match 'cmd|powershell'){0}else{1}}};" ^
 "foreach($p in $ordered){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} };" ^
 "Write-Host ('  Stopped repo processes: '+@($procs).Count)"

timeout /t 3 /nobreak >nul

rem Final verification and one retry for anything that raced during shutdown.
echo [4/4] Verifying shutdown...
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command ^
 "$root=[regex]::Escape('%ROOT_PATH%');" ^
 "$self=$PID;" ^
 "$left=Get-CimInstance Win32_Process ^| Where-Object { $_.ProcessId -ne $self -and ([string]$_.CommandLine -match $root) -and ([string]$_.CommandLine -match 'services[\\/.]|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|Sentinuity_Watch|streamlit' };" ^
 "foreach($p in $left){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} };" ^
 "Start-Sleep -Seconds 2;" ^
 "$remain=Get-CimInstance Win32_Process ^| Where-Object { $_.ProcessId -ne $self -and ([string]$_.CommandLine -match $root) -and ([string]$_.CommandLine -match 'services[\\/.]|Launch_Sentinuity|Restart_Sentinuity|Watchdog_Sentinuity|Sentinuity_Watch|streamlit' };" ^
 "if(@($remain).Count -eq 0){ Write-Host '  PASS: no Sentinuity services remain.'; exit 0 } else { Write-Host '  FAIL: processes still remain:'; $remain ^| Select-Object ProcessId,Name,CommandLine ^| Format-Table -AutoSize; exit 1 }"

if errorlevel 1 (
  echo.
  echo ============================================================
  echo  SHUTDOWN INCOMPLETE - remaining processes printed above
  echo ============================================================
  exit /b 1
)

echo.
echo ============================================================
echo  ALL SENTINUITY SERVICES STOPPED
 echo  Safe to replace files, verify, or relaunch.
echo ============================================================
exit /b 0
