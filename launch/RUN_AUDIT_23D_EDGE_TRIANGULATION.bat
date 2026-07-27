@echo off
setlocal
cd /d "%~dp0"
cd ..
echo ============================================================
echo SENTINUITY 23-DAY EDGE TRIANGULATION AUDIT
echo ============================================================
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 tools\AUDIT_SENTINUITY_23D_EDGE_TRIANGULATION.py --days 23
) else (
  python tools\AUDIT_SENTINUITY_23D_EDGE_TRIANGULATION.py --days 23
)
echo.
echo Audit finished. Check the audits folder for the ZIP.
pause
