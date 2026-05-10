param(
  [int]$Port = 8091,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$halExe = Join-Path $repo "hal\build\HalServer.exe"
$halBuild = Split-Path -Parent $halExe
$leishineBin = Join-Path $repo "hal\vendor\leishine\bin"
$forceDimensionBin = Join-Path $repo "hal\vendor\force_dimension\bin"
$jodellBin = Join-Path $repo "hal\vendor\jodell"

if (!(Test-Path $halExe)) {
  throw "HalServer.exe not found: $halExe"
}

foreach ($required in @(
  (Join-Path $halBuild "LTDMC.dll"),
  (Join-Path $halBuild "dhd64.dll"),
  (Join-Path $halBuild "drd64.dll"),
  (Join-Path $halBuild "jodellTool.dll")
)) {
  if (!(Test-Path $required)) {
    throw "HAL runtime dependency missing: $required"
  }
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -First 1
if ($existing) {
  if (!$Restart) {
    Write-Host "HAL already listening on 127.0.0.1:$Port, pid=$existing"
    exit 0
  }
  Stop-Process -Id $existing -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

$env:PATH = "$halBuild;$leishineBin;$forceDimensionBin;$jodellBin;$env:PATH"
$process = Start-Process -FilePath $halExe -WorkingDirectory $halBuild -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2

try {
  $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
} catch {
  throw "HAL started pid=$($process.Id), but /health failed: $($_.Exception.Message)"
}

if (!$health.ltdmc_ok) {
  throw "HAL started pid=$($process.Id), but LTDMC is not initialized: $($health.version)"
}

[pscustomobject]@{
  pid = $process.Id
  url = "http://127.0.0.1:$Port"
  ltdmc_ok = $health.ltdmc_ok
  omega7_ok = $health.omega7_ok
  version = $health.version
}
