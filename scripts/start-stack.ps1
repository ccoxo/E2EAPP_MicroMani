param(
  [int]$BackendPort = 18082,
  [int]$FrontendPort = 5174,
  [int]$HalPort = 8091,
  [switch]$SkipStartupHome
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repo "backend\runtime\logs"
$frontendOutLog = Join-Path $logDir "frontend-launch.out.log"
$frontendErrLog = Join-Path $logDir "frontend-launch.err.log"

function Stop-ProcessTree {
  param([int]$RootPid)

  $allProcesses = Get-CimInstance Win32_Process
  $children = $allProcesses | Where-Object { $_.ParentProcessId -eq $RootPid }
  foreach ($child in $children) {
    Stop-ProcessTree -RootPid $child.ProcessId
  }
  Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Stop-BackendProcessTrees {
  $allProcesses = Get-CimInstance Win32_Process
  $escapedRepo = [regex]::Escape($repo)
  $backendProcesses = @($allProcesses | Where-Object {
      $process = $_
      $commandLine = $process.CommandLine
      if (-not $commandLine) {
        return $false
      }
      if ($commandLine -notmatch "backend\.app:create_app" -or $commandLine -notmatch "--port\s+$BackendPort") {
        return $false
      }
      if ($commandLine -match $escapedRepo) {
        return $true
      }
      $parent = $allProcesses | Where-Object { $_.ProcessId -eq $process.ParentProcessId } | Select-Object -First 1
      return (
        $parent -and
        $parent.CommandLine -and
        $parent.CommandLine -match $escapedRepo -and
        $parent.CommandLine -match "backend\.app:create_app" -and
        $parent.CommandLine -match "--port\s+$BackendPort"
      )
    })

  $rootPids = @($backendProcesses | ForEach-Object {
      $process = $_
      $rootPid = $process.ProcessId
      $parent = $allProcesses | Where-Object { $_.ProcessId -eq $process.ParentProcessId } | Select-Object -First 1
      if (
        $parent -and
        $parent.CommandLine -and
        $parent.CommandLine -match $escapedRepo -and
        $parent.CommandLine -match "backend\.app:create_app" -and
        $parent.CommandLine -match "--port\s+$BackendPort"
      ) {
        $rootPid = $parent.ProcessId
      }
      $rootPid
    } | Sort-Object -Unique)

  foreach ($rootPid in $rootPids) {
    Stop-ProcessTree -RootPid $rootPid
  }
}

Stop-BackendProcessTrees

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

$activeHalPort = $HalPort
try {
  & (Join-Path $PSScriptRoot "start-hal.ps1") -Restart -Port $activeHalPort | Out-Host
} catch {
  $halStartError = $_.Exception.Message
  if ($HalPort -ne 8091 -or $halStartError -notmatch "/health failed") {
    throw
  }
  Write-Warning "HAL start on port 8091 failed; retrying on 8092. $halStartError"
  $activeHalPort = 8092
  & (Join-Path $PSScriptRoot "start-hal.ps1") -Restart -Port $activeHalPort | Out-Host
}

Start-Sleep -Seconds 1

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Remove-Item -LiteralPath $frontendOutLog, $frontendErrLog -Force -ErrorAction SilentlyContinue

$env:APPSTATION_HAL_MODE = "real"
$env:APPSTATION_HAL_BASE_URL = "http://127.0.0.1:$activeHalPort"
$env:APPSTATION_SKIP_STARTUP_HOME = if ($SkipStartupHome) { "true" } else { "false" }
$backend = Start-Process `
  -FilePath (Join-Path $repo "backend\.venv\Scripts\python.exe") `
  -ArgumentList @("-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "$BackendPort") `
  -WorkingDirectory $repo `
  -WindowStyle Hidden `
  -PassThru

$frontendCommand = "`$env:VITE_MOCK_MODE='false'; `$env:VITE_API_BASE='http://127.0.0.1:$BackendPort'; `$env:VITE_WS_URL='ws://127.0.0.1:$BackendPort/ws'; npm run build; if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }; node scripts/serve-dist.mjs --host 127.0.0.1 --port $FrontendPort"
$frontend = Start-Process `
  -FilePath "powershell.exe" `
  -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) `
  -WorkingDirectory (Join-Path $repo "frontend") `
  -WindowStyle Hidden `
  -RedirectStandardOutput $frontendOutLog `
  -RedirectStandardError $frontendErrLog `
  -PassThru

Start-Sleep -Seconds 3

[pscustomobject]@{
  backendPid = $backend.Id
  frontendLauncherPid = $frontend.Id
  backend = "http://127.0.0.1:$BackendPort"
  frontend = "http://127.0.0.1:$FrontendPort"
  hal = "http://127.0.0.1:$activeHalPort"
  frontendOutLog = $frontendOutLog
  frontendErrLog = $frontendErrLog
  skipStartupHome = [bool]$SkipStartupHome
}
