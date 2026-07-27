@echo off
setlocal
cd /d "%~dp0\.."

echo ==================================================================================
echo SENTINUITY SAFE DB PRUNE ONLY
echo ==================================================================================
python SENTINUITY_SAFE_AUDIT_PRUNE.py --db sentinuity_matrix.db --prune --target-mb 80
echo.
pause
endlocal
