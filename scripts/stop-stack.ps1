$ErrorActionPreference = "Stop"
$currentPid = $PID
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

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

foreach ($port in @(5173, 5174, 18080, 18082, 8091)) {
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

Stop-BackendProcessTrees
