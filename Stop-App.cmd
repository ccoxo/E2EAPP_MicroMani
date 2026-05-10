@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-stack.ps1"
echo.
echo App services have stopped. You can close this window.
pause
