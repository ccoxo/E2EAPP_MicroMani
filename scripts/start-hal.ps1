# 阅读导航 08｜启动、部署与工具
# 职责：部署候选 HAL 二进制和依赖 DLL，绑定 HKVL 端口、注入力配置并启动健康检查。
# 先看：Stop-ProcessTree → Stop-HalRuntimeProcessTrees → Promote-HalCandidate → Copy-RuntimeDllIfNewer。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

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

function Assert-HalCapabilities {
  param([object]$Health)

  # 版本号不代表协议能力；旧程序可能使用相同版本号。
  $missing = @("force_calibration_state_v1", "control_lease_v1" | Where-Object {
      @($Health.capabilities) -cnotcontains $_
    })
  if ($missing.Count -gt 0) {
    throw "HAL protocol capabilities missing: $($missing -join ', '). Rebuild and deploy both HalServer.exe and JodellGripperWorker.exe from the current branch."
  }
}

function Repair-StaleDdsPorts {
  param([ValidateRange(0, 232)][int]$DomainId)

  $shmDir = Join-Path $env:ProgramData "eProsima\fastrtps_interprocess"
  if (!(Test-Path -LiteralPath $shmDir -PathType Container)) { return }
  $shmDir = (Resolve-Path -LiteralPath $shmDir).Path
  $bootTimeUtc = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).LastBootUpTime.ToUniversalTime()
  # Fast-DDS 默认端口公式：7400 + 250 * domain；不处理其他域或匿名数据段。
  $firstPort = 7400 + 250 * $DomainId
  $groups = @(Get-ChildItem -LiteralPath $shmDir -File -Filter "fastrtps_port*" | Where-Object {
      $_.Name -match '^fastrtps_port(\d+)(?:_(?:el|sl|mutex))?$' -and
      [int]$Matches[1] -ge $firstPort -and [int]$Matches[1] -lt ($firstPort + 250)
    } | Group-Object { $_.Name -replace '_(el|sl|mutex)$', '' })
  $backupDir = $null
  $recovered = 0
  foreach ($group in $groups) {
    $files = @($group.Group)
    if (@($files | Where-Object { $_.LastWriteTimeUtc -ge $bootTimeUtc }).Count -gt 0) { continue }
    $handles = New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
    try {
      try {
        foreach ($file in $files) {
          # 保持句柄到移动结束，拒绝其他读写打开；Delete 共享允许同卷重命名。
          $handle = [IO.File]::Open($file.FullName, 'Open', 'ReadWrite', 'Delete')
          $handles.Add($handle)
          $handle.Lock(0, 1)
        }
      } catch {
        Write-Warning "DDS port recovery skipped (in use or inaccessible): $($group.Name)"
        continue
      }
      # 获取句柄后再检查时间，避免处理枚举之后刚被重新打开的端口。
      foreach ($file in $files) { $file.Refresh() }
      if (@($files | Where-Object { $_.LastWriteTimeUtc -ge $bootTimeUtc }).Count -gt 0) { continue }
      if (!$backupDir) {
        $backupName = "appstation-recovery-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([guid]::NewGuid().ToString('N'))"
        $backupDir = [IO.Path]::GetFullPath((Join-Path $shmDir $backupName))
        if (!$backupDir.StartsWith($shmDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
          throw "DDS recovery backup is outside the shared memory directory"
        }
        New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop | Out-Null
      }
      foreach ($file in $files) {
        if ($file.DirectoryName -ne $shmDir) { throw "Unexpected DDS recovery source: $($file.FullName)" }
        Move-Item -LiteralPath $file.FullName -Destination (Join-Path $backupDir $file.Name) -ErrorAction Stop
        $recovered++
      }
    } finally {
      foreach ($handle in $handles) { $handle.Dispose() }
    }
  }
  if ($recovered -gt 0) {
    Write-Host "Recovered $recovered stale DDS port files for domain $DomainId. Backup: $backupDir"
  }
}

function Resolve-HkvlBoundPort {
  param(
    [string]$Side,
    [string]$InstanceId
  )

  $devices = @(Get-PnpDevice -PresentOnly -Class Ports | Where-Object {
      $_.InstanceId -eq $InstanceId
    })
  if ($devices.Count -ne 1) {
    throw "HKVL serial binding not found for hardware $Side side: $InstanceId"
  }
  $portMatch = [regex]::Match(
    [string]$devices[0].FriendlyName,
    '\((COM\d+)\)\s*$',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
  )
  if (!$portMatch.Success) {
    throw "HKVL serial binding has no COM port for hardware $Side side: $InstanceId"
  }
  return $portMatch.Groups[1].Value.ToUpperInvariant()
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
    $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
    Assert-HalCapabilities -Health $health
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
$hkvlLeftInstanceId = "USB\VID_1A86&PID_55D3\5C7B023865"
$hkvlRightInstanceId = "USB\VID_1A86&PID_55D3\5C7B030018"
$forceRuntimeConfig = [ordered]@{
  source = "hkvl_serial"
  protocol = "hkvl_active_v1"
  leftPort = "COM15"
  rightPort = "COM14"
  leftAxisSign = @(-1.0, 1.0, -1.0, 1.0, -1.0, -1.0)
  rightAxisSign = @(-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
  baudrate = 1000000
  expectedSampleHz = 1000
  lowpassEnabled = $true
  lowpassCutoffHz = 10.0
  fxyWarnN = 2.0
  fxyStopN = 30.0
  fzWarnN = 3.0
  fzStopN = 30.0
  momentWarnNm = 0.02
  momentStopNm = 1.0
  watchdogMs = 50.0
  acknowledgeStableMs = 500.0
  complianceEnabled = $false
  leftMappingConfirmed = $false
  leftComplianceMatrix = @(1.0, 0.0, 0.0, 1.0)
  leftComplianceDeadbandN = @(0.0, 0.0)
  leftComplianceGainUmPerNs = @(0.0, 0.0)
  leftComplianceMaxStepUm = @(0.0, 0.0)
  leftComplianceMaxOffsetUm = @(0.0, 0.0)
  rightMappingConfirmed = $false
  rightComplianceMatrix = @(1.0, 0.0, 0.0, 1.0)
  rightComplianceDeadbandN = @(0.0, 0.0)
  rightComplianceGainUmPerNs = @(0.0, 0.0)
  rightComplianceMaxStepUm = @(0.0, 0.0)
  rightComplianceMaxOffsetUm = @(0.0, 0.0)
}
if (Test-Path $runtimeConfig) {
  try {
    $config = Get-Content $runtimeConfig -Raw | ConvertFrom-Json
    if ($null -ne $config.teleop) {
      if ($null -ne $config.teleop.leftOpenId) { $omegaLeftOpenId = [int]$config.teleop.leftOpenId }
      if ($null -ne $config.teleop.rightOpenId) { $omegaRightOpenId = [int]$config.teleop.rightOpenId }
      if ($null -ne $config.teleop.swapHands) { $omegaSwapHands = [bool]$config.teleop.swapHands }
    }
    if ($null -ne $config.motion -and $null -ne $config.motion.kinematics) {
      foreach ($axisSignMapping in @(
        @("left", "leftSignedPulsePerUnit"),
        @("right", "rightSignedPulsePerUnit")
      )) {
        $side = $axisSignMapping[0]
        $sourceKey = $axisSignMapping[1]
        $values = @($config.motion.kinematics.$sourceKey)
        if ($values.Count -ne 6) {
          throw "motion.kinematics.$sourceKey must contain exactly 6 numbers"
        }
        $signs = @()
        foreach ($item in $values) {
          $value = [double]$item
          if ([double]::IsNaN($value) -or [double]::IsInfinity($value) -or $value -eq 0.0) {
            throw "motion.kinematics.$sourceKey values must be finite and non-zero"
          }
          if ($value -lt 0.0) {
            $signs += -1.0
          } else {
            $signs += 1.0
          }
        }
        $forceRuntimeConfig["${side}AxisSign"] = $signs
      }
    }
    if ($null -ne $config.force) {
      if ($null -ne $config.force.source) { $forceRuntimeConfig.source = [string]$config.force.source }
      if ($null -ne $config.force.lowpassEnabled) { $forceRuntimeConfig.lowpassEnabled = [bool]$config.force.lowpassEnabled }
      if ($null -ne $config.force.lowpassCutoffHz) { $forceRuntimeConfig.lowpassCutoffHz = [double]$config.force.lowpassCutoffHz }
      if ($null -ne $config.force.serial) {
        foreach ($key in @("protocol", "leftPort", "rightPort", "baudrate", "expectedSampleHz")) {
          if ($null -ne $config.force.serial.$key) { $forceRuntimeConfig[$key] = $config.force.serial.$key }
        }
      }
      if ($null -ne $config.force.compliance) {
        if ($null -ne $config.force.compliance.enabled) {
          $forceRuntimeConfig.complianceEnabled = [bool]$config.force.compliance.enabled
        }
        foreach ($side in @("left", "right")) {
          $sideConfig = $config.force.compliance.$side
          if ($null -eq $sideConfig) { continue }
          if ($null -ne $sideConfig.mappingConfirmed) {
            $forceRuntimeConfig["${side}MappingConfirmed"] = [bool]$sideConfig.mappingConfirmed
          }
          foreach ($mapping in @(
            @("matrix", "Matrix"),
            @("deadbandN", "DeadbandN"),
            @("gainUmPerNs", "GainUmPerNs"),
            @("maxStepUm", "MaxStepUm"),
            @("maxOffsetUm", "MaxOffsetUm")
          )) {
            $sourceKey = $mapping[0]
            $targetKey = "${side}Compliance$($mapping[1])"
            if ($null -ne $sideConfig.$sourceKey) {
              $forceRuntimeConfig[$targetKey] = @($sideConfig.$sourceKey | ForEach-Object { [double]$_ })
            }
          }
        }
      }
    }
    if ($null -ne $config.safety) {
      foreach ($key in @(
        "fxyWarnN",
        "fxyStopN",
        "fzWarnN",
        "fzStopN",
        "momentWarnNm",
        "momentStopNm",
        "watchdogMs"
      )) {
        if ($null -ne $config.safety.$key) { $forceRuntimeConfig[$key] = [double]$config.safety.$key }
      }
    }
  } catch {
    Write-Warning "Failed to read HAL runtime config from $runtimeConfig; using ICF defaults. $($_.Exception.Message)"
  }
}
if ([string]$forceRuntimeConfig.source -eq "hkvl_serial") {
  $forceRuntimeConfig.leftPort = Resolve-HkvlBoundPort -Side "left" -InstanceId $hkvlLeftInstanceId
  $forceRuntimeConfig.rightPort = Resolve-HkvlBoundPort -Side "right" -InstanceId $hkvlRightInstanceId
  if ($forceRuntimeConfig.leftPort -eq $forceRuntimeConfig.rightPort) {
    throw "HKVL hardware left and right bindings resolved to the same port: $($forceRuntimeConfig.leftPort)"
  }
  $env:APPSTATION_HKVL_LEFT_PORT = [string]$forceRuntimeConfig.leftPort
  $env:APPSTATION_HKVL_RIGHT_PORT = [string]$forceRuntimeConfig.rightPort
  Write-Host "HKVL bound ports: hardware left=$($forceRuntimeConfig.leftPort), hardware right=$($forceRuntimeConfig.rightPort)"
} else {
  Remove-Item Env:APPSTATION_HKVL_LEFT_PORT -ErrorAction SilentlyContinue
  Remove-Item Env:APPSTATION_HKVL_RIGHT_PORT -ErrorAction SilentlyContinue
}
$env:APPSTATION_OMEGA7_LEFT_OPEN_ID = "$omegaLeftOpenId"
$env:APPSTATION_OMEGA7_RIGHT_OPEN_ID = "$omegaRightOpenId"
$env:APPSTATION_OMEGA7_SWAP_HANDS = if ($omegaSwapHands) { "true" } else { "false" }
$env:APPSTATION_FORCE_CONFIG_JSON = $forceRuntimeConfig | ConvertTo-Json -Compress
$env:APPSTATION_HAL_PORT = "$Port"
$env:APPSTATION_HAL_DDS_ENABLED = "1"
if (-not $env:APPSTATION_DDS_DOMAIN_ID) { $env:APPSTATION_DDS_DOMAIN_ID = "42" }
Repair-StaleDdsPorts -DomainId ([int]$env:APPSTATION_DDS_DOMAIN_ID)
$env:APPSTATION_JODELL_WORKER_EXE = "$workerRuntimeExe"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$process = Start-Process `
  -FilePath $halExe `
  -WorkingDirectory $halBuild `
  -WindowStyle Hidden `
  -RedirectStandardOutput $halOutLog `
  -RedirectStandardError $halErrLog `
  -PassThru

try {
  # 设备和 DDS 初始化结束后才监听 HTTP，冷启动不能只检查一次。
  Write-Host "Waiting for HAL readiness on port $Port (up to 60 seconds)..."
  $deadline = (Get-Date).AddSeconds(60)
  while ($true) {
    $process.Refresh()
    if ($process.HasExited) {
      throw "HAL exited before /health became ready; exit code=$($process.ExitCode)"
    }
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2
      break
    } catch {
      if ((Get-Date) -ge $deadline) {
        throw "Timed out after 60 seconds waiting for HAL /health: $($_.Exception.Message)"
      }
      Start-Sleep -Milliseconds 500
    }
  }
  Assert-HalCapabilities -Health $health
  if (!$health.ltdmc_ok) {
    throw "LTDMC is not initialized: $($health.version)"
  }
} catch {
  $startupError = $_.Exception.Message
  Stop-ProcessTree -RootPid $process.Id
  $logDetails = foreach ($logPath in @($halErrLog, $halOutLog)) {
    $tail = @(Get-Content -LiteralPath $logPath -Tail 20 -ErrorAction SilentlyContinue)
    if ($tail.Count -gt 0) {
      "${logPath}:`n$($tail -join "`n")"
    } else {
      "${logPath}: <empty>"
    }
  }
  throw "HAL startup failed pid=$($process.Id): $startupError`n$($logDetails -join "`n")"
}

[pscustomobject]@{
  pid = $process.Id
  url = "http://127.0.0.1:$Port"
  ltdmc_ok = $health.ltdmc_ok
  omega7_ok = $health.omega7_ok
  version = $health.version
}
