param(
  [string]$CheckpointDir = "D:\ACT_TEXT\act_text\checkpoints\003000",
  [string]$Checkpoint = "pretrained_model",
  [string]$DeployDir = "",
  [string]$CameraIds = "0,1,2",
  [string]$CondaExe = "D:\App\Anaconda3\Scripts\conda.exe",
  [string]$CondaEnv = "lero",
  [string]$Device = "cuda",
  [int]$BackendPort = 18082,
  [int]$FrontendPort = 5174,
  [double]$PolicyHttpTimeout = 5.0,
  [double]$MaxTranslationUm = 50.0,
  [double]$MaxRotationDeg = 0.02,
  [double]$MaxGripperMm = 0.2,
  [double]$SmoothTranslationUm = 15.0,
  [double]$SmoothRotationDeg = 0.006,
  [double]$SmoothGripperMm = 0.05,
  [double]$SmoothingAlpha = 0.35,
  [double]$FlipDamping = 0.2,
  [int]$SignConfirmFrames = 3,
  [double]$TranslationDeadbandUm = 0.0,
  [int]$PolicyUpdateInterval = 1,
  [switch]$Send,
  [switch]$SkipStartupHome,
  [switch]$WithFrontend
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendUrl = "http://127.0.0.1:$BackendPort"
$cameraIdParts = @($CameraIds.Split(",") | ForEach-Object { $_.Trim() })

if (-not (Test-Path -LiteralPath $CondaExe)) {
  throw "Conda executable not found: $CondaExe"
}
$checkpointPath = Join-Path $CheckpointDir $Checkpoint
if (-not (Test-Path -LiteralPath $checkpointPath)) {
  throw "Checkpoint not found: $checkpointPath"
}
if ([string]::IsNullOrWhiteSpace($DeployDir)) {
  if (Test-Path -LiteralPath (Join-Path $CheckpointDir "act_deploy.py")) {
    $DeployDir = $CheckpointDir
  } elseif (Test-Path -LiteralPath "F:\model\grab_screw\act_jepa_tarimg_same_v2\050000\act_deploy.py") {
    $DeployDir = "F:\model\grab_screw\act_jepa_tarimg_same_v2\050000"
  } else {
    throw "act_deploy.py not found. Pass -DeployDir with a directory that contains act_deploy.py"
  }
}
if (-not (Test-Path -LiteralPath (Join-Path $DeployDir "act_deploy.py"))) {
  throw "act_deploy.py not found in deploy directory: $DeployDir"
}

if ($WithFrontend) {
  $stackArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $repo "scripts\start-stack.ps1"),
    "-BackendPort", "$BackendPort",
    "-FrontendPort", "$FrontendPort"
  )
  if ($SkipStartupHome) {
    $stackArgs += "-SkipStartupHome"
  }

  Write-Host "Starting AppStation stack with frontend..."
  & powershell @stackArgs | Out-Host
} else {
  Write-Host "Starting HAL and backend for ACT..."
  & powershell -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\start-hal.ps1") -Restart
  Write-Host "HAL startup finished. Cleaning previous backend/frontend..."

  $existingBackend = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match "backend\.app:create_app" -and $_.CommandLine -match "--port\s+$BackendPort"
  }
  foreach ($process in $existingBackend) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
  $frontendPid = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -First 1
  if ($frontendPid) {
    Stop-Process -Id $frontendPid -Force -ErrorAction SilentlyContinue
  }

  Write-Host "Launching backend on $backendUrl..."
  $env:APPSTATION_HAL_MODE = "real"
  $env:APPSTATION_HAL_BASE_URL = "http://127.0.0.1:8091"
  $env:APPSTATION_SKIP_STARTUP_HOME = if ($SkipStartupHome) { "true" } else { "false" }
  $env:APPSTATION_DISABLE_CAMERA_PROBE = "true"
  $backend = Start-Process `
    -FilePath (Join-Path $repo "backend\.venv\Scripts\python.exe") `
    -ArgumentList @("-m", "uvicorn", "backend.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "$BackendPort") `
    -WorkingDirectory $repo `
    -WindowStyle Hidden `
    -PassThru
  Start-Sleep -Seconds 3
  [pscustomobject]@{
    backendPid = $backend.Id
    backend = $backendUrl
    hal = "http://127.0.0.1:8091"
    frontend = "disabled"
    cameraProbe = "disabled"
    skipStartupHome = [bool]$SkipStartupHome
  } | Format-List | Out-Host
}

Write-Host "Waiting for policy bridge at $backendUrl..."
$deadline = (Get-Date).AddSeconds(20)
do {
  try {
    $response = Invoke-RestMethod -Uri "$backendUrl/api/policy/observation" -Method GET -TimeoutSec 5
    if ($response.ok -eq $true -and $response.data.state.Count -eq 14) {
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
} while ((Get-Date) -lt $deadline)

if ((Get-Date) -ge $deadline) {
  throw "Policy bridge did not become ready at $backendUrl/api/policy/observation"
}

$actArgs = @(
  "run", "--no-capture-output", "-n", $CondaEnv,
  "python", "act_deploy.py",
  "--checkpoint", $checkpointPath,
  "--device", $Device,
  "--camera_ids", $CameraIds,
  "--backend_url", $backendUrl,
  "--policy_http_timeout", "$PolicyHttpTimeout",
  "--max_translation_um", "$MaxTranslationUm",
  "--max_rotation_deg", "$MaxRotationDeg",
  "--max_gripper_mm", "$MaxGripperMm",
  "--smooth_translation_um", "$SmoothTranslationUm",
  "--smooth_rotation_deg", "$SmoothRotationDeg",
  "--smooth_gripper_mm", "$SmoothGripperMm",
  "--smoothing_alpha", "$SmoothingAlpha",
  "--flip_damping", "$FlipDamping",
  "--sign_confirm_frames", "$SignConfirmFrames",
  "--translation_deadband_um", "$TranslationDeadbandUm",
  "--policy_update_interval", "$PolicyUpdateInterval"
)
if ($Send) {
  $actArgs += "--send"
}

Write-Host "Starting ACT deploy. Send=$([bool]$Send), cameras=$CameraIds, deployDir=$DeployDir"
if ($cameraIdParts.Count -ge 3) {
  Write-Host "Camera mapping: global=$($cameraIdParts[0]), wrist_left=$($cameraIdParts[1]), wrist_right=$($cameraIdParts[2])"
}
Push-Location -LiteralPath $DeployDir
try {
  & $CondaExe @actArgs
} finally {
  Pop-Location
}
