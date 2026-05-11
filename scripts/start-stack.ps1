param(
  [int]$BackendPort = 18082,
  [int]$FrontendPort = 5174
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Stop-ProcessTree {
  param([int]$RootPid)

  $allProcesses = Get-CimInstance Win32_Process
  $children = $allProcesses | Where-Object { $_.ParentProcessId -eq $RootPid }
  foreach ($child in $children) {
    Stop-ProcessTree -RootPid $child.ProcessId
  }
  Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

& (Join-Path $PSScriptRoot "start-hal.ps1") -Restart | Out-Host

$backendPid = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -First 1
if ($backendPid) {
  $backendRootPid = $backendPid
  $allProcesses = Get-CimInstance Win32_Process
  $backendProcess = $allProcesses | Where-Object { $_.ProcessId -eq $backendPid } | Select-Object -First 1
  $backendParent = $allProcesses | Where-Object { $_.ProcessId -eq $backendProcess.ParentProcessId } | Select-Object -First 1
  if ($backendParent -and $backendParent.CommandLine -match "backend\.app:create_app" -and $backendParent.CommandLine -match "--port\s+$BackendPort") {
    $backendRootPid = $backendParent.ProcessId
  }
  Stop-ProcessTree -RootPid $backendRootPid
}

$frontendPid = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -First 1
if ($frontendPid) {
  Stop-Process -Id $frontendPid -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

$env:APPSTATION_HAL_MODE = "real"
$env:APPSTATION_HAL_BASE_URL = "http://127.0.0.1:8091"
$backend = Start-Process `
  -FilePath (Join-Path $repo "backend\.venv\Scripts\python.exe") `
  -ArgumentList @("-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "$BackendPort") `
  -WorkingDirectory $repo `
  -WindowStyle Hidden `
  -PassThru

$frontendCommand = "`$env:VITE_MOCK_MODE='false'; `$env:VITE_AUTO_SHUTDOWN_ON_CLOSE='true'; `$env:VITE_API_BASE='http://127.0.0.1:$BackendPort'; `$env:VITE_WS_URL='ws://127.0.0.1:$BackendPort/ws'; npm run dev -- --host 127.0.0.1 --port $FrontendPort"
$frontend = Start-Process `
  -FilePath "powershell.exe" `
  -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) `
  -WorkingDirectory (Join-Path $repo "frontend") `
  -WindowStyle Hidden `
  -PassThru

Start-Sleep -Seconds 3

[pscustomobject]@{
  backendPid = $backend.Id
  frontendLauncherPid = $frontend.Id
  backend = "http://127.0.0.1:$BackendPort"
  frontend = "http://127.0.0.1:$FrontendPort"
  hal = "http://127.0.0.1:8091"
}
