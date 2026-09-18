@echo off
rem 阅读导航 08｜启动、部署与工具
rem 职责：双击快速拉起前端界面（测试 HAL，无需真实硬件）；支持 stop 参数停止服务。
rem 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

setlocal
cd /d "%~dp0"

if /I "%~1"=="stop" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-ui.ps1" -Stop
  echo.
  echo UI services stopped.
  pause
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-ui.ps1"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 (
  echo Start failed with exit code %EXITCODE%.
) else (
  echo UI is running. Close this window anytime; services stay in background.
  echo Stop later with: Start-UI.cmd stop
)
pause
exit /b %EXITCODE%
