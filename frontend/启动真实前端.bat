@echo off
chcp 65001 >nul
title AppStation 真实前端 (Preview)
setlocal
cd /d "%~dp0"

echo ============================================
echo   AppStation 真实 React 前端
echo   模式: dist 静态预览 (已构建产物)
echo   地址: http://127.0.0.1:4173
echo ============================================
echo.

if not exist "dist\index.html" (
  echo [错误] 未找到 dist\index.html，请先构建:
  echo   npm run build
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo [提示] 未找到 node_modules，正在安装依赖...
  call npm install
)

echo 启动服务... 按 Ctrl+C 停止
start "" "http://127.0.0.1:4173"
node scripts\serve-dist.mjs --port 4173

endlocal
