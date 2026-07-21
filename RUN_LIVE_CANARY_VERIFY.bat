@echo off
setlocal EnableExtensions
title SENTINUITY LIMITED LIVE CANARY VERIFY
rem ===========================================================================
rem RUN_LIVE_CANARY_VERIFY.bat  (LIVE_CANARY_V3_20260721)
rem The deployable live-canary verifier the V2 release conditioned live
rem sign-off on. Portable: derives the repo root from its own location.
rem Submits NO transaction. Prints exactly one decisive verdict line and
rem returns a non-zero exit code on any failure.
rem ===========================================================================
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
cd /d "%ROOT%"

rem Windows Python console contract: prevent cp1252 UnicodeEncodeError.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON_CMD="
py -3 -c "import sys" >nul 2>&1 && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD python -c "import sys" >nul 2>&1 && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    echo [FAIL] Python 3 not found on PATH.
    echo FAIL — LIVE REMAINS BLOCKED
    exit /b 2
)

%PYTHON_CMD% "%ROOT%\launch\live_canary_fixtures.py"
set "CANARY_EXIT=%ERRORLEVEL%"
if not "%CANARY_EXIT%"=="0" exit /b %CANARY_EXIT%
exit /b 0
