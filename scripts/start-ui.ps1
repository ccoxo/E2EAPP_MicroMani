# 阅读导航 08｜启动、部署与工具
# 职责：无 HAL 依赖快速拉起前端界面；复用已健康的前后端进程并打开浏览器。
# 先看：Test-HttpOk → Ensure-Backend → Ensure-Frontend → Open-AppBrowser。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

param(
  [int]$BackendPort = 18082,
  [int]$FrontendPort = 5174,
  [switch]$NoBrowser,
  [switch]$Stop
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repo "backend\runtime\logs"
$frontendOutLog = Join-Path $logDir "frontend-ui.out.log"
$frontendErrLog = Join-Path $logDir "frontend-ui.err.log"
$backendOutLog = Join-Path $logDir "backend-ui.out.log"
$backendErrLog = Join-Path $logDir "backend-ui.err.log"
$frontendDist = Join-Path $repo "frontend\dist\index.html"
$backendPython = Join-Path $repo "backend\.venv\Scripts\python.exe"
$frontendUrl = "http://127.0.0.1:$FrontendPort/"
$backendHealthUrl = "http://127.0.0.1:$BackendPort/docs"

function Test-HttpOk([string]$Url, [int]$TimeoutSeconds = 2) {
  try {
    $response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
    return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-HttpOk $Url 2) {
      return $true
    }
    Start-Sleep -Milliseconds 400
  }
  return $false
}

function Find-Browser {
  $candidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }
  return $null
}

function Stop-UiStack {
  & (Join-Path $PSScriptRoot "stop-stack.ps1") | Out-Host
  Write-Host "UI stack stopped (ports $BackendPort / $FrontendPort)."
}

function Ensure-Backend {
  if (Test-HttpOk $backendHealthUrl 3) {
    Write-Host "Backend already healthy: $backendHealthUrl"
    return
  }

  if (-not (Test-Path $backendPython)) {
    throw "Backend venv missing: $backendPython"
  }

  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
  Write-Host "Starting backend (test HAL) on port $BackendPort..."
  $env:APPSTATION_HAL_MODE = "test"
  $null = Start-Process `
    -FilePath $backendPython `
    -ArgumentList @("-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "$BackendPort") `
    -WorkingDirectory $repo `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendOutLog `
    -RedirectStandardError $backendErrLog `
    -PassThru

  if (-not (Wait-HttpOk $backendHealthUrl 60)) {
    $errTail = if (Test-Path $backendErrLog) { (Get-Content $backendErrLog -Tail 20) -join "`n" } else { "<no log>" }
    throw "Backend failed to become healthy at $backendHealthUrl`n$errTail"
  }
  Write-Host "Backend ready: $backendHealthUrl"
}

function Ensure-Frontend {
  if (Test-HttpOk $frontendUrl 2) {
    Write-Host "Frontend already healthy: $frontendUrl"
    return
  }

  if (-not (Test-Path $frontendDist)) {
    Write-Host "Frontend dist missing; building..."
    Push-Location (Join-Path $repo "frontend")
    try {
      & npm run build
      if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE"
      }
    } finally {
      Pop-Location
    }
  }

  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
  Write-Host "Starting frontend server on port $FrontendPort..."
  $null = Start-Process `
    -FilePath "node" `
    -ArgumentList @("scripts/serve-dist.mjs", "--host", "127.0.0.1", "--port", "$FrontendPort") `
    -WorkingDirectory (Join-Path $repo "frontend") `
    -WindowStyle Hidden `
    -RedirectStandardOutput $frontendOutLog `
    -RedirectStandardError $frontendErrLog `
    -PassThru

  if (-not (Wait-HttpOk $frontendUrl 30)) {
    $errTail = if (Test-Path $frontendErrLog) { (Get-Content $frontendErrLog -Tail 20) -join "`n" } else { "<no log>" }
    throw "Frontend failed to become healthy at $frontendUrl`n$errTail"
  }
  Write-Host "Frontend ready: $frontendUrl"
}

function Open-AppBrowser {
  if ($NoBrowser) {
    return
  }
  $browser = Find-Browser
  if (-not $browser) {
    Write-Warning "No supported browser found. Open $frontendUrl manually."
    return
  }
  Write-Host "Opening browser: $frontendUrl"
  Start-Process -FilePath $browser -ArgumentList @("--app=$frontendUrl", "--no-first-run", "--disable-extensions")
}

if ($Stop) {
  Stop-UiStack
  exit 0
}

Write-Host "MicroMani quick UI (no HAL / test HAL mode)"
Ensure-Backend
Ensure-Frontend
Open-AppBrowser

Write-Host ""
Write-Host "UI is ready."
Write-Host "  Frontend: $frontendUrl"
Write-Host "  Backend:  $backendHealthUrl"
Write-Host "  HAL mode: test (no real hardware motion)"
Write-Host ""
Write-Host "Stop with: Start-UI.cmd stop   or   powershell -File scripts\stop-stack.ps1"
