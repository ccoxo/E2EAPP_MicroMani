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
  throw "Timed out waiting for $Url"
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

function Restart-AppStack([string]$Reason) {
  Write-Warning $Reason
  & (Join-Path $PSScriptRoot "start-stack.ps1") -BackendPort $BackendPort -FrontendPort $FrontendPort -HalPort $HalPort | Out-Host
  Wait-HttpOk $appUrl 45
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
  & (Join-Path $PSScriptRoot "start-stack.ps1") -BackendPort $BackendPort -FrontendPort $FrontendPort -HalPort $HalPort | Out-Host
  Wait-HttpOk $appUrl 45

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
