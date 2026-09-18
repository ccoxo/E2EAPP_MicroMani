@echo off
rem 阅读导航 08｜启动、部署与工具
rem 职责：双击启动入口；切换到项目目录后调用 scripts/launch-app.ps1。
rem 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-app.ps1"
echo.
echo App services have stopped. You can close this window.
pause
