param(
  [int]$BackendPort = 18082,
  [int]$FrontendPort = 5174,
  [int]$HalPort = 8091
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$cacheBust = [DateTime]::UtcNow.Ticks
$appUrl = "http://127.0.0.1:$FrontendPort/settings?appver=$cacheBust#manual"
$profileDir = Join-Path $repo ".app-browser-profile"
$stackInfo = $null

function Find-Browser {
  $candidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }
  throw "No supported browser found. Install Microsoft Edge or Google Chrome."
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSeconds) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-HttpOk $Url 3) {
      return
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Timed out waiting for $Url.`n$(Get-LaunchDiagnostics)"
}

function Test-HttpOk([string]$Url, [int]$TimeoutSeconds) {
  try {
    $response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
    return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Get-RecordStatus([int]$TimeoutSeconds) {
  try {
    $response = Invoke-RestMethod "http://127.0.0.1:$BackendPort/api/record/status" -TimeoutSec $TimeoutSeconds
    return $response.data
  } catch {
    return $null
  }
}

function Start-AppStack {
  $script:stackInfo = & (Join-Path $PSScriptRoot "start-stack.ps1") -BackendPort $BackendPort -FrontendPort $FrontendPort -HalPort $HalPort
  $script:stackInfo | Out-Host
}

function Get-ProcessSummary([object]$PidValue) {
  if (-not $PidValue) {
    return "pid=<empty>"
  }
  try {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$PidValue)" -ErrorAction SilentlyContinue
    if (-not $process) {
      return "pid=$PidValue not running"
    }
    return "pid=$($process.ProcessId) name=$($process.Name) parent=$($process.ParentProcessId) commandLine=$($process.CommandLine)"
  } catch {
    return "pid=$PidValue lookup failed: $($_.Exception.Message)"
  }
}

function Get-PortOwnerSummary([int]$Port) {
  try {
    $owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess |
      Sort-Object -Unique)
    if ($owners.Count -eq 0) {
      return "port ${Port}: no listener"
    }
    $ownerLines = @($owners | ForEach-Object { Get-ProcessSummary $_ })
    return "port ${Port}: $($ownerLines -join '; ')"
  } catch {
    return "port ${Port}: lookup failed: $($_.Exception.Message)"
  }
}

function Get-LogTail([string]$Label, [string]$Path) {
  if (-not $Path) {
    return "${Label}: <empty path>"
  }
  if (-not (Test-Path -LiteralPath $Path)) {
    return "${Label}: missing at ${Path}"
  }
  try {
    $tail = @(Get-Content -LiteralPath $Path -Tail 40 -ErrorAction SilentlyContinue)
    if ($tail.Count -eq 0) {
      return "${Label}: empty at $Path"
    }
    return "${Label} ($Path):`n$($tail -join "`n")"
  } catch {
    return "${Label}: read failed at ${Path}: $($_.Exception.Message)"
  }
}

function Get-LaunchDiagnostics {
  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("Launch diagnostics:")
  $lines.Add("urls: frontend=http://127.0.0.1:$FrontendPort backend=http://127.0.0.1:$BackendPort halPort=$HalPort")
  $lines.Add((Get-PortOwnerSummary $FrontendPort))
  $lines.Add((Get-PortOwnerSummary $BackendPort))
  $lines.Add((Get-PortOwnerSummary $HalPort))
  if ($script:stackInfo) {
    foreach ($field in @("backendPid", "frontendLauncherPid", "backend", "frontend", "hal", "frontendOutLog", "frontendErrLog")) {
      $lines.Add("${field}: $($script:stackInfo.$field)")
    }
    $lines.Add("backend process: $(Get-ProcessSummary $script:stackInfo.backendPid)")
    $lines.Add("frontend launcher process: $(Get-ProcessSummary $script:stackInfo.frontendLauncherPid)")
    $lines.Add((Get-LogTail "frontend stdout" $script:stackInfo.frontendOutLog))
    $lines.Add((Get-LogTail "frontend stderr" $script:stackInfo.frontendErrLog))
  } else {
    $lines.Add("stackInfo: <not captured>")
  }
  return $lines -join "`n"
}

function Restart-AppStack([string]$Reason) {
  Write-Warning $Reason
  Start-AppStack
  Wait-HttpOk $appUrl 180
}

function Get-AppBrowserProcesses {
  $escaped = $profileDir.Replace("\", "\\")
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.CommandLine -and
      ($_.CommandLine -like "*$profileDir*" -or $_.CommandLine -like "*$escaped*")
    }
}

New-Item -ItemType Directory -Path $profileDir -Force | Out-Null

try {
  Write-Host "Starting HAL, backend, and frontend..."
  Start-AppStack
  Wait-HttpOk $appUrl 180

  $processes = @(Get-AppBrowserProcesses)
  if ($processes.Count -eq 0) {
    $browser = Find-Browser
    Write-Host "Opening App window: $appUrl"
    Start-Process `
      -FilePath $browser `
      -ArgumentList @(
        "--app=$appUrl",
        "--user-data-dir=$profileDir",
        "--no-first-run",
        "--disable-background-mode",
        "--disable-extensions"
      ) `
      -WorkingDirectory $repo | Out-Null

    $deadline = (Get-Date).AddSeconds(20)
    do {
      Start-Sleep -Milliseconds 500
      $processes = @(Get-AppBrowserProcesses)
    } while ($processes.Count -eq 0 -and (Get-Date) -lt $deadline)
  } else {
    Write-Host "Using existing App window: $appUrl"
  }

  if ($processes.Count -eq 0) {
    throw "App browser process was not detected after launch."
  }

  Write-Host "App is running. Close the App window to stop frontend, backend, and HAL."
  $backendHealthFailures = 0
  $backendHealthFailureLimit = 15
  while (@(Get-AppBrowserProcesses).Count -gt 0) {
    $backendHealthUrl = "http://127.0.0.1:$BackendPort/docs"
    if (Test-HttpOk $backendHealthUrl 3) {
      $backendHealthFailures = 0
    } else {
      $recordStatus = Get-RecordStatus 2
      if ($recordStatus -and ($recordStatus.active -or $recordStatus.recording)) {
        $backendHealthFailures = 0
        Write-Warning "backend health check skipped during active recording"
      } else {
        $backendHealthFailures += 1
        if ($backendHealthFailures -ge $backendHealthFailureLimit) {
          Restart-AppStack "backend health check failed; restarting stack"
          $backendHealthFailures = 0
        }
      }
    }
    Start-Sleep -Seconds 1
  }
} finally {
  Write-Host "Stopping frontend, backend, and HAL..."
  & (Join-Path $PSScriptRoot "stop-stack.ps1") | Out-Host
}
