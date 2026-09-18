param(
  [int]$BackendPort = 18082,
  [int]$HalPort = 8091,
  [int]$DomainId = 42,
  [switch]$LanDiscovery,
  [switch]$SkipStartupHome
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo "backend\.venv\Scripts\python.exe"
$logDir = Join-Path $repo "backend\runtime\logs"
$backendOutLog = Join-Path $logDir "backend-dds.out.log"
$backendErrLog = Join-Path $logDir "backend-dds.err.log"

function Stop-RepoProcessByPattern {
  param([string]$Pattern)

  $escapedRepo = [regex]::Escape($repo)
  Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $escapedRepo -and $_.CommandLine -match $Pattern
  } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Remove-Item -LiteralPath $backendOutLog, $backendErrLog -Force -ErrorAction SilentlyContinue

Stop-RepoProcessByPattern "backend\.app:create_app.*--port\s+$BackendPort"

# DDS 是由 HAL C++ 进程直接创建 participant，因此这些环境变量必须先于 start-hal.ps1 设置。
$env:APPSTATION_HAL_DDS_ENABLED = "1"
$env:APPSTATION_DDS_DOMAIN_ID = "$DomainId"
$env:APPSTATION_DDS_LAN_DISCOVERY = if ($LanDiscovery) { "1" } else { "0" }

& (Join-Path $PSScriptRoot "start-hal.ps1") -Restart -Port $HalPort | Out-Host

$env:APPSTATION_HAL_MODE = "real"
$env:APPSTATION_HAL_BASE_URL = "http://127.0.0.1:$HalPort"
$env:APPSTATION_HAL_TRANSPORT = "dds"
$env:APPSTATION_SKIP_STARTUP_HOME = if ($SkipStartupHome) { "true" } else { "false" }

Start-Sleep -Seconds 1

$backend = Start-Process `
  -FilePath $python `
  -ArgumentList @("-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "$BackendPort") `
  -WorkingDirectory $repo `
  -WindowStyle Hidden `
  -RedirectStandardOutput $backendOutLog `
  -RedirectStandardError $backendErrLog `
  -PassThru

[pscustomobject]@{
  backendPid = $backend.Id
  backend = "http://127.0.0.1:$BackendPort"
  hal = "http://127.0.0.1:$HalPort"
  domainId = $DomainId
  lanDiscovery = [bool]$LanDiscovery
  backendOutLog = $backendOutLog
  backendErrLog = $backendErrLog
}
