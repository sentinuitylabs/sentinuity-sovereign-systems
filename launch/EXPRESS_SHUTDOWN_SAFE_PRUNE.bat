@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."

echo ==================================================================================
echo SENTINUITY EXPRESS SHUTDOWN
echo ==================================================================================
echo root: %CD%
echo time: %DATE% %TIME%
echo.

if not exist "launch" mkdir "launch"
if not exist "launch\shutdown_logs" mkdir "launch\shutdown_logs"

set LOG=launch\shutdown_logs\express_shutdown.log

echo [1/4] Disarming trading flags...
python SENTINUITY_SHUTDOWN_HELPER.py --stop >> "%LOG%" 2>&1

echo [2/4] Waiting for handles to close...
timeout /t 3 /nobreak > nul

echo [3/4] Running safe prune...
python SENTINUITY_SAFE_AUDIT_PRUNE.py --db sentinuity_matrix.db --prune --target-mb 80 >> "%LOG%" 2>&1

echo [4/4] Done.
echo.
echo Shutdown complete. Log:
echo %CD%\%LOG%
echo.
pause
endlocal
