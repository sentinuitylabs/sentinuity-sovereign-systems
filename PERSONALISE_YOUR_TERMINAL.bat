@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run INSTALL_PUBLIC_PAPER.bat first.& pause & exit /b 1)
".venv\Scripts\python.exe" tools\personalise_terminal.py
pause
