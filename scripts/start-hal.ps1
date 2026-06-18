param(
  [int]$Port = 8091,
  [switch]$Restart
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repo "backend\runtime\logs"
$halOutLog = Join-Path $logDir "hal-server.out.log"
$halErrLog = Join-Path $logDir "hal-server.err.log"
$halExe = Join-Path $repo "hal\build\HalServer.exe"
$halNextExe = Join-Path $repo "hal\build\HalServer.next.exe"
$workerExe = Join-Path $repo "hal\build\JodellGripperWorker.exe"
$workerNextExe = Join-Path $repo "hal\build\JodellGripperWorker.next.exe"
$workerRuntimeExe = $workerExe
$halBuild = Split-Path -Parent $halExe
$leishineBin = Join-Path $repo "hal\vendor\leishine\bin"
$forceDimensionBin = Join-Path $repo "hal\vendor\force_dimension\bin"
$jodellBin = Join-Path $repo "hal\vendor\jodell"
# HalServer 链接 Fast-DDS DLL，即使 DDS 默认关闭，也需要把运行库目录放进子进程 PATH。
# Keep runtime DLLs beside HalServer.exe as well as on PATH so direct launches
# and service-style restarts resolve the same vendor dependencies.

function Stop-ProcessTree {
  param([int]$RootPid)

  if ($RootPid -eq $PID) {
    return
  }

  $allProcesses = Get-CimInstance Win32_Process
  $children = $allProcesses | Where-Object { $_.ParentProcessId -eq $RootPid }
  foreach ($child in $children) {
    Stop-ProcessTree -RootPid $child.ProcessId
  }
  Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 200
  if (Get-Process -Id $RootPid -ErrorAction SilentlyContinue) {
    & cmd.exe /c "taskkill.exe /PID $RootPid /T /F >nul 2>nul" | Out-Null
  }
}

function Stop-HalRuntimeProcessTrees {
  $allProcesses = Get-CimInstance Win32_Process
  $escapedHalBuild = [regex]::Escape($halBuild)
  $runtimeProcesses = @($allProcesses | Where-Object {
      $process = $_
      if (-not $process.CommandLine) {
        return $false
      }
      $normalizedCommandLine = $process.CommandLine.Replace('\\', '\')
      ($process.Name -eq "HalServer.exe" -or $process.Name -like "JodellGripperWorker*.exe") -and
      $normalizedCommandLine -match $escapedHalBuild
    })
  $runtimeIds = @{}
  foreach ($process in $runtimeProcesses) {
    $runtimeIds[[int]$process.ProcessId] = $true
  }
  $rootPids = @($runtimeProcesses | Where-Object {
      -not $runtimeIds.ContainsKey([int]$_.ParentProcessId)
    } | ForEach-Object {
      [int]$_.ProcessId
    } | Sort-Object -Unique)

  foreach ($rootPid in $rootPids) {
    Stop-ProcessTree -RootPid $rootPid
    Write-Host "Stopped HAL process tree $rootPid for $halBuild"
  }
}

function Promote-HalCandidate {
  param(
    [string]$CandidateExe,
    [string]$TargetExe
  )
  if (!(Test-Path $CandidateExe)) {
    return
  }
  $shouldPromote = !(Test-Path $TargetExe)
  if (!$shouldPromote) {
    $candidateHash = Get-FileHash -Algorithm SHA256 -LiteralPath $CandidateExe
    $targetHash = Get-FileHash -Algorithm SHA256 -LiteralPath $TargetExe
    $shouldPromote = $candidateHash.Hash -ne $targetHash.Hash
  }
  if (!$shouldPromote) {
    return
  }
  try {
    # Promote *.next.exe builds with a timestamped backup so a bad local build
    # can be rolled back without rebuilding vendor-dependent binaries.
    if (Test-Path $TargetExe) {
      $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
      $backupName = "{0}.backup-{1}.exe" -f [System.IO.Path]::GetFileNameWithoutExtension($TargetExe), $stamp
      Copy-Item -LiteralPath $TargetExe -Destination (Join-Path $halBuild $backupName) -Force
    }
    Copy-Item -LiteralPath $CandidateExe -Destination $TargetExe -Force
  } catch {
    if ($TargetExe -eq $workerExe -and (Test-Path $CandidateExe)) {
      try {
        # Worker executables are often locked by a live child process. A
        # timestamped runtime copy lets HAL use the fresh worker without
        # terminating an unrelated session first.
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss-ffff"
        $workerRuntimeExe = Join-Path $halBuild "JodellGripperWorker.runtime-$stamp.exe"
        Copy-Item -LiteralPath $CandidateExe -Destination $workerRuntimeExe -Force
        $script:workerRuntimeExe = $workerRuntimeExe
        Write-Host "HAL worker target is locked; using runtime worker copy $workerRuntimeExe"
        return
      } catch {
        Write-Warning "HAL worker runtime copy failed for ${CandidateExe}: $($_.Exception.Message)"
      }
    }
    Write-Warning "HAL runtime promotion skipped for ${TargetExe}: $($_.Exception.Message)"
    return
  }
  Write-Host "Promoted newer HAL build: $CandidateExe -> $TargetExe"
}

function Copy-RuntimeDllIfNewer {
  param(
    [string]$SourceDll,
    [string]$TargetDir
  )
  if (!(Test-Path $SourceDll)) {
    return
  }
  $targetDll = Join-Path $TargetDir ([System.IO.Path]::GetFileName($SourceDll))
  if (
    !(Test-Path $targetDll) -or
    (Get-Item $SourceDll).LastWriteTimeUtc -gt (Get-Item $targetDll).LastWriteTimeUtc
  ) {
    Copy-Item -LiteralPath $SourceDll -Destination $targetDll -Force
  }
}

if ($Restart) {
  Stop-HalRuntimeProcessTrees
  Get-ChildItem -LiteralPath $halBuild -Filter "JodellGripperWorker.runtime-*.exe" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
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

Promote-HalCandidate -CandidateExe $halNextExe -TargetExe $halExe
Promote-HalCandidate -CandidateExe $workerNextExe -TargetExe $workerExe

if (!(Test-Path $halExe)) {
  throw "HalServer.exe not found: $halExe"
}

foreach ($runtimeDll in @(
  "F:\opt\ros\jazzy\bin\fastrtps-2.14.dll",
  "F:\opt\ros\jazzy\bin\fastcdr-2.2.dll",
  "F:\opt\ros\jazzy\.pixi\envs\default\Library\bin\tinyxml2.dll",
  "F:\opt\ros\jazzy\.pixi\envs\default\Library\bin\libssl-3-x64.dll",
  "F:\opt\ros\jazzy\.pixi\envs\default\Library\bin\libcrypto-3-x64.dll"
)) {
  Copy-RuntimeDllIfNewer -SourceDll $runtimeDll -TargetDir $halBuild
}

foreach ($required in @(
  (Join-Path $halBuild "JodellGripperWorker.exe"),
  (Join-Path $halBuild "LTDMC.dll"),
  (Join-Path $halBuild "dhd64.dll"),
  (Join-Path $halBuild "drd64.dll"),
  (Join-Path $halBuild "jodellTool.dll"),
  (Join-Path $halBuild "fastrtps-2.14.dll"),
  (Join-Path $halBuild "fastcdr-2.2.dll")
)) {
  if (!(Test-Path $required)) {
    throw "HAL runtime dependency missing: $required"
  }
}

$env:PATH = "$halBuild;$leishineBin;$forceDimensionBin;$jodellBin;F:\opt\ros\jazzy\bin;F:\opt\ros\jazzy\.pixi\envs\default\Library\bin;$env:PATH"
$runtimeConfig = Join-Path $repo "backend\runtime\config.json"
$omegaLeftOpenId = 0
$omegaRightOpenId = 1
$omegaSwapHands = $false
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
$env:APPSTATION_HAL_PORT = "$Port"
$env:APPSTATION_HAL_DDS_ENABLED = "1"
if (-not $env:APPSTATION_DDS_DOMAIN_ID) { $env:APPSTATION_DDS_DOMAIN_ID = "42" }
if (-not $env:APPSTATION_DDS_LAN_DISCOVERY) { $env:APPSTATION_DDS_LAN_DISCOVERY = "0" }
$env:APPSTATION_JODELL_WORKER_EXE = "$workerRuntimeExe"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$process = Start-Process `
  -FilePath $halExe `
  -WorkingDirectory $halBuild `
  -WindowStyle Hidden `
  -RedirectStandardOutput $halOutLog `
  -RedirectStandardError $halErrLog `
  -PassThru
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
