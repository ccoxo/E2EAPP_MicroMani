$ErrorActionPreference = "Stop"

function Stop-ProcessTree {
  param([int]$RootPid)

  $allProcesses = Get-CimInstance Win32_Process
  $children = $allProcesses | Where-Object { $_.ParentProcessId -eq $RootPid }
  foreach ($child in $children) {
    Stop-ProcessTree -RootPid $child.ProcessId
  }
  Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

foreach ($port in @(5173, 5174, 18080, 18082, 8091)) {
  $pidOnPort = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -First 1
  if ($pidOnPort) {
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
