@echo off
setlocal
cd /d "%~dp0\.."

echo ==================================================================================
echo SENTINUITY DB AUDIT ONLY
echo ==================================================================================
python SENTINUITY_SAFE_AUDIT_PRUNE.py --db sentinuity_matrix.db --audit-only
echo.
pause
endlocal
