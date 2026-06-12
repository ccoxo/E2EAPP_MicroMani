$ErrorActionPreference = "Stop"
$currentPid = $PID
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$halBuild = Join-Path $repo "hal\build"

function Stop-ProcessTree {
  param([int]$RootPid)

  if ($RootPid -eq $currentPid) {
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

function Stop-BackendProcessTrees {
  $allProcesses = Get-CimInstance Win32_Process
  $escapedRepo = [regex]::Escape($repo)
  $backendProcesses = @($allProcesses | Where-Object {
      $process = $_
      $commandLine = $process.CommandLine
      if (-not $commandLine) {
        return $false
      }
      if ($commandLine -notmatch "backend\.app:create_app" -or $commandLine -notmatch "--port\s+(18080|18082)") {
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
        $parent.CommandLine -match "--port\s+(18080|18082)"
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
        $parent.CommandLine -match "--port\s+(18080|18082)"
      ) {
        $rootPid = $parent.ProcessId
      }
      $rootPid
    } | Sort-Object -Unique)

  foreach ($rootPid in $rootPids) {
    Stop-ProcessTree -RootPid $rootPid
    Write-Host "Stopped backend process tree $rootPid for $repo"
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

foreach ($port in @(5173, 5174, 18080, 18082, 8091, 8092)) {
  $pidsOnPort = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess |
    Sort-Object -Unique)
  foreach ($pidOnPort in $pidsOnPort) {
    if (-not $pidOnPort) {
      continue
    }
    $rootPid = $pidOnPort
    if ($port -in @(18080, 18082)) {
      $allProcesses = Get-CimInstance Win32_Process
      $backendProcess = $allProcesses | Where-Object { $_.ProcessId -eq $pidOnPort } | Select-Object -First 1
      $backendParent = $allProcesses | Where-Object { $_.ProcessId -eq $backendProcess.ParentProcessId } | Select-Object -First 1
      if ($backendParent -and $backendParent.CommandLine -match "backend\.app:create_app" -and $backendParent.CommandLine -match "--port\s+$port") {
        $rootPid = $backendParent.ProcessId
      }
    }
    Stop-ProcessTree -RootPid $rootPid
    Write-Host "Stopped process tree $rootPid on port $port"
  }
}

Stop-HalRuntimeProcessTrees
Stop-BackendProcessTrees
