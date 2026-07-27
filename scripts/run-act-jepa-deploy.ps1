param(
  [string]$CheckpointDir = "F:\model\grab_screw\act_jepa_tarimg_same_v2\050000",
  [string]$Checkpoint = "pretrained_model",
  [string]$PolicySourceDir = "F:\model",
  [string]$CameraIds = "1,2,0",
  [string]$ControlledSides = "left",
  [string]$CondaExe = "D:\App\Anaconda3\Scripts\conda.exe",
  [string]$CondaEnv = "lero",
  [string]$Device = "cuda",
  [int]$BackendPort = 18082,
  [int]$FrontendPort = 5174,
  [double]$PolicyHttpTimeout = 5.0,
  [double]$MaxTranslationUm = 50.0,
  [double]$MaxRotationDeg = 0.02,
  [double]$MaxGripperMm = 0.2,
  [double]$SmoothingAlpha = 0.35,
  [double]$FlipDamping = 0.2,
  [double]$SmoothTranslationUm = 15.0,
  [double]$SmoothRotationDeg = 0.006,
  [double]$SmoothGripperMm = 0.05,
  [int]$SignConfirmFrames = 3,
  [bool]$DisableRotation = $true,
  [object]$DominantTranslationAxis = $true,
  [int]$DominantAxisHoldFrames = 12,
  [double]$DominantAxisReleaseUm = 15.0,
  [string]$TranslationAxes = "x,y,z",
  [double]$TranslationDeadbandUm = 0.0,
  [object]$SmoothAbsoluteTarget = $false,
  [object]$OpenLoopActionDeltas = $false,
  [object]$WaitForMotionSettled = $false,
  [double]$SettleToleranceUm = 30.0,
  [double]$SettleTimeoutSec = 2.0,
  [double]$SettlePollIntervalSec = 0.05,
  [int]$SettleMaxTargetSteps = 12,
  [int]$PolicyUpdateInterval = 6,
  [int]$ActionStepIndex = 0,
  [int]$StatePrintInterval = 10,
  [string]$InferenceLogPath = "$env:USERPROFILE\Desktop\ACT-JEPA推理日志.txt",
  [switch]$NoInferenceLog,
  [object]$ReplanEveryStep = $true,
  [object]$FreezeUncontrolledState = $true,
  [object]$ClipModelState = $true,
  [object]$ClipActionToStats = $true,
  [switch]$PrintNormalizedState,
  [switch]$Send,
  [switch]$SkipStartupHome,
  [switch]$WithFrontend
)

$ErrorActionPreference = "Stop"

function Convert-ToBooleanParameter {
  param(
    [object]$Value,
    [string]$Name
  )

  if ($Value -is [bool]) {
    return $Value
  }
  if ($Value -is [int] -or $Value -is [long]) {
    return [bool]$Value
  }

  $text = "$Value".Trim().ToLowerInvariant()
  if ($text -in @("true", "$true", "1", "yes", "y")) {
    return $true
  }
  if ($text -in @("false", "$false", "0", "no", "n")) {
    return $false
  }
  throw "Invalid boolean value for ${Name}: $Value. Use true/false or 1/0."
}

$DominantTranslationAxis = Convert-ToBooleanParameter $DominantTranslationAxis "DominantTranslationAxis"
$ReplanEveryStep = Convert-ToBooleanParameter $ReplanEveryStep "ReplanEveryStep"
$FreezeUncontrolledState = Convert-ToBooleanParameter $FreezeUncontrolledState "FreezeUncontrolledState"
$ClipModelState = Convert-ToBooleanParameter $ClipModelState "ClipModelState"
$ClipActionToStats = Convert-ToBooleanParameter $ClipActionToStats "ClipActionToStats"
$SmoothAbsoluteTarget = Convert-ToBooleanParameter $SmoothAbsoluteTarget "SmoothAbsoluteTarget"
$OpenLoopActionDeltas = Convert-ToBooleanParameter $OpenLoopActionDeltas "OpenLoopActionDeltas"
$WaitForMotionSettled = Convert-ToBooleanParameter $WaitForMotionSettled "WaitForMotionSettled"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendUrl = "http://127.0.0.1:$BackendPort"

if (-not (Test-Path -LiteralPath $CondaExe)) {
  throw "Conda executable not found: $CondaExe"
}
if (-not (Test-Path -LiteralPath (Join-Path $CheckpointDir "act_deploy.py"))) {
  throw "act_deploy.py not found in checkpoint directory: $CheckpointDir"
}
if (-not (Test-Path -LiteralPath $PolicySourceDir)) {
  throw "Policy source directory not found: $PolicySourceDir"
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
  Write-Host "Starting HAL and backend for ACT-JEPA..."
  & (Join-Path $repo "scripts\start-hal.ps1") -Restart

  Write-Host "Stopping any existing backend listener on port $BackendPort..."
  $backendPids = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $backendPids) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 500

  Write-Host "Stopping any existing frontend listener on port $FrontendPort..."
  $frontendPid = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -First 1
  if ($frontendPid) {
    Stop-Process -Id $frontendPid -Force -ErrorAction SilentlyContinue
  }

  $env:APPSTATION_HAL_MODE = "real"
  $env:APPSTATION_HAL_BASE_URL = "http://127.0.0.1:8091"
  $env:APPSTATION_SKIP_STARTUP_HOME = if ($SkipStartupHome) { "true" } else { "false" }
  $env:APPSTATION_DISABLE_CAMERA_PROBE = "true"
  Write-Host "Starting backend on $backendUrl..."
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
    if (
      $response.ok -eq $true -and
      $response.data.state.Count -eq 14 -and
      $response.data.pulses.Count -eq 12 -and
      $response.data.force_left.Count -eq 6 -and
      $response.data.force_right.Count -eq 6
    ) {
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
} while ((Get-Date) -lt $deadline)

if ((Get-Date) -ge $deadline) {
  throw "Policy bridge did not become ready at $backendUrl/api/policy/observation"
}

$env:PYTHONPATH = "$PolicySourceDir;$env:PYTHONPATH"
$actArgs = @(
  "run", "--no-capture-output", "-n", $CondaEnv,
  "python", "act_deploy.py",
  "--checkpoint", $Checkpoint,
  "--policy_source_dir", $PolicySourceDir,
  "--device", $Device,
  "--camera_ids", $CameraIds,
  "--controlled_sides", $ControlledSides,
  "--backend_url", $backendUrl,
  "--policy_http_timeout", "$PolicyHttpTimeout",
  "--max_translation_um", "$MaxTranslationUm",
  "--max_rotation_deg", "$MaxRotationDeg",
  "--max_gripper_mm", "$MaxGripperMm",
  "--smoothing_alpha", "$SmoothingAlpha",
  "--flip_damping", "$FlipDamping",
  "--smooth_translation_um", "$SmoothTranslationUm",
  "--smooth_rotation_deg", "$SmoothRotationDeg",
  "--smooth_gripper_mm", "$SmoothGripperMm",
  "--sign_confirm_frames", "$SignConfirmFrames",
  "--dominant_axis_hold_frames", "$DominantAxisHoldFrames",
  "--dominant_axis_release_um", "$DominantAxisReleaseUm",
  "--translation_axes", "$TranslationAxes",
  "--translation_deadband_um", "$TranslationDeadbandUm",
  "--settle_tolerance_um", "$SettleToleranceUm",
  "--settle_timeout_sec", "$SettleTimeoutSec",
  "--settle_poll_interval_sec", "$SettlePollIntervalSec",
  "--settle_max_target_steps", "$SettleMaxTargetSteps",
  "--policy_update_interval", "$PolicyUpdateInterval",
  "--action_step_index", "$ActionStepIndex",
  "--state_print_interval", "$StatePrintInterval"
)
if ($Send) {
  $actArgs += "--send"
}
if ($PrintNormalizedState) {
  $actArgs += "--print_normalized_state"
}
if ($DisableRotation) {
  $actArgs += "--disable_rotation"
}
if ($DominantTranslationAxis) {
  $actArgs += "--dominant_translation_axis"
}
if ($SmoothAbsoluteTarget) {
  $actArgs += "--smooth_absolute_target"
}
if ($OpenLoopActionDeltas) {
  $actArgs += "--open_loop_action_deltas"
}
if ($WaitForMotionSettled) {
  $actArgs += "--wait_for_motion_settled"
}
if ($ReplanEveryStep) {
  $actArgs += "--replan_every_step"
}
if ($FreezeUncontrolledState) {
  $actArgs += "--freeze_uncontrolled_state"
}
if ($ClipModelState) {
  $actArgs += "--clip_model_state"
}
if ($ClipActionToStats) {
  $actArgs += "--clip_action_to_stats"
}

Write-Host "Starting ACT-JEPA deploy. Send=$([bool]$Send), cameras=$CameraIds"
Push-Location -LiteralPath $CheckpointDir
try {
  if ($NoInferenceLog) {
    & $CondaExe @actArgs
  } else {
    $logDir = Split-Path -Parent $InferenceLogPath
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
      New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    @(
      ""
      "============================================================"
      "ACT-JEPA inference started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
      "CheckpointDir: $CheckpointDir"
      "Checkpoint: $Checkpoint"
      "CameraIds: $CameraIds"
      "ControlledSides: $ControlledSides"
      "TranslationAxes: $TranslationAxes"
      "TranslationDeadbandUm: $TranslationDeadbandUm"
      "SmoothAbsoluteTarget: $SmoothAbsoluteTarget"
      "OpenLoopActionDeltas: $OpenLoopActionDeltas"
      "WaitForMotionSettled: $WaitForMotionSettled"
      "SettleToleranceUm: $SettleToleranceUm"
      "SettleTimeoutSec: $SettleTimeoutSec"
      "SettleMaxTargetSteps: $SettleMaxTargetSteps"
      "Send: $([bool]$Send)"
      "PrintNormalizedState: $([bool]$PrintNormalizedState)"
      "FreezeUncontrolledState: $FreezeUncontrolledState"
      "ClipModelState: $ClipModelState"
      "ClipActionToStats: $ClipActionToStats"
      "Command: $CondaExe $($actArgs -join ' ')"
      "============================================================"
    ) | Add-Content -LiteralPath $InferenceLogPath -Encoding UTF8
    Write-Host "Inference log: $InferenceLogPath"
    & $CondaExe @actArgs 2>&1 | Tee-Object -FilePath $InferenceLogPath -Append
  }
} finally {
  Pop-Location
}
