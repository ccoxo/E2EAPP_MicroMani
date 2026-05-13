param(
  [int]$Port = 8091,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$halExe = Join-Path $repo "hal\build\HalServer.exe"
$halNextExe = Join-Path $repo "hal\build\HalServer.next.exe"
$halBuild = Split-Path -Parent $halExe
$leishineBin = Join-Path $repo "hal\vendor\leishine\bin"
$forceDimensionBin = Join-Path $repo "hal\vendor\force_dimension\bin"
$jodellBin = Join-Path $repo "hal\vendor\jodell"
$runtimeConfig = Join-Path $repo "backend\runtime\config.json"
$omegaLeftOpenId = 0
$omegaRightOpenId = 1
$omegaSwapHands = $true

function Promote-HalCandidate {
  if (!(Test-Path $halNextExe)) {
    return
  }
  $shouldPromote = !(Test-Path $halExe)
  if (!$shouldPromote) {
    $shouldPromote = (Get-Item $halNextExe).LastWriteTimeUtc -gt (Get-Item $halExe).LastWriteTimeUtc
  }
  if (!$shouldPromote) {
    return
  }
  if (Test-Path $halExe) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item -LiteralPath $halExe -Destination (Join-Path $halBuild "HalServer.backup-$stamp.exe") -Force
  }
  Copy-Item -LiteralPath $halNextExe -Destination $halExe -Force
  Write-Host "Promoted newer HAL build: $halNextExe -> $halExe"
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

Promote-HalCandidate

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

$env:PATH = "$halBuild;$leishineBin;$forceDimensionBin;$jodellBin;$env:PATH"
if (Test-Path $runtimeConfig) {
  try {
    $config = Get-Content $runtimeConfig -Raw | ConvertFrom-Json
    if ($null -ne $config.teleop) {
      if ($null -ne $config.teleop.leftOpenId) { $omegaLeftOpenId = [int]$config.teleop.leftOpenId }
      if ($null -ne $config.teleop.rightOpenId) { $omegaRightOpenId = [int]$config.teleop.rightOpenId }
      if ($null -ne $config.teleop.swapHands) { $omegaSwapHands = [bool]$config.teleop.swapHands }
    }
  } catch {
    Write-Warning "Failed to read Omega7 teleop config from $runtimeConfig; using ICF defaults. $($_.Exception.Message)"
  }
}
$env:APPSTATION_OMEGA7_LEFT_OPEN_ID = "$omegaLeftOpenId"
$env:APPSTATION_OMEGA7_RIGHT_OPEN_ID = "$omegaRightOpenId"
$env:APPSTATION_OMEGA7_SWAP_HANDS = if ($omegaSwapHands) { "true" } else { "false" }
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
