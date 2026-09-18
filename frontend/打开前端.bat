@echo off
chcp 65001 >nul
title AppStation 前端入口
setlocal

echo ============================================
echo   AppStation 本地界面入口
echo ============================================
echo.
echo   [1] 查看优化预览（配色/排版前后对比）
echo   [2] 启动真实 React 前端
echo   [3] 退出
echo.
set /p CHOICE=请选择 (1/2/3):

if "%CHOICE%"=="1" (
  start "" "%~dp0output\ui-redesign\启动优化预览.bat"
  exit /b 0
)
if "%CHOICE%"=="2" (
  start "" "%~dp0启动真实前端.bat"
  exit /b 0
)

endlocal
