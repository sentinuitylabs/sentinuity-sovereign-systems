@echo off
cd /d "%~dp0"
call .\launch\Shutdown_Sentinuity.bat
if errorlevel 1 pause
