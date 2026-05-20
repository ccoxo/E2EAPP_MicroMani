param(
  [int]$Port = 8091,
  [int]$ObserveSeconds = 0,
  [string]$OutputDir = "",
  [switch]$SkipNoMotionProbe,
  [switch]$RequireActions,
  [switch]$RequireLeftAction,
  [switch]$RequireRightAction,
  [switch]$RequireCrossMapping,
  [switch]$RequireAllAxes,
  [switch]$RequireGripperChange,
  [switch]$RequireForceOutput,
  [switch]$RequireGravityCompensation,
  [switch]$RequireZeroStop,
  [switch]$VerifyReport,
  [switch]$Strict
)

$ErrorActionPreference = "Stop"
$strictRequested = [bool]$Strict
if ($Strict) {
  $RequireActions = $true
  $RequireLeftAction = $true
  $RequireRightAction = $true
  $RequireCrossMapping = $true
  $RequireAllAxes = $true
  $RequireGripperChange = $true
  $RequireForceOutput = $true
  $RequireGravityCompensation = $true
  $RequireZeroStop = $true
  $VerifyReport = $true
  if ($ObserveSeconds -le 0) {
    $ObserveSeconds = 60
  }
}
$autoVerifyReport = (
  $ObserveSeconds -gt 0 -or
  [bool]$RequireActions -or
  [bool]$RequireLeftAction -or
  [bool]$RequireRightAction -or
  [bool]$RequireCrossMapping -or
  [bool]$RequireAllAxes -or
  [bool]$RequireGripperChange -or
  [bool]$RequireForceOutput -or
  [bool]$RequireGravityCompensation -or
  [bool]$RequireZeroStop
)
if ($autoVerifyReport) {
  $VerifyReport = $true
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $repo "backend\runtime\acceptance"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$baseUrl = "http://127.0.0.1:$Port"
$directionPolicy = "current-hal-native-cross-map-v1"

function Invoke-HalJson {
  param(
    [string]$Method,
    [string]$Path,
    [object]$Body = $null
  )
  $uri = "$baseUrl$Path"
  if ($null -eq $Body) {
    return Invoke-RestMethod -Uri $uri -Method $Method -TimeoutSec 5
  }
  $json = $Body | ConvertTo-Json -Depth 8
  return Invoke-RestMethod -Uri $uri -Method $Method -Body $json -ContentType "application/json" -TimeoutSec 5
}

function Count-Actions {
  param([object]$Status)
  if ($null -eq $Status -or $null -eq $Status.actionHistory) {
    return 0
  }
  return @($Status.actionHistory).Count
}

function Is-ZeroTargets {
  param([object]$Status)
  if ($null -eq $Status -or $null -eq $Status.gripperTargets) {
    return $false
  }
  $targets = @($Status.gripperTargets)
  return $targets.Count -ge 2 -and [double]$targets[0] -eq 0.0 -and [double]$targets[1] -eq 0.0
}

function Add-ObservedValue {
  param(
    [hashtable]$Set,
    [object]$Value
  )
  if ($null -ne $Value) {
    $text = [string]$Value
    if (![string]::IsNullOrWhiteSpace($text)) {
      $Set[$text] = $true
    }
  }
}

function Get-SortedKeys {
  param([hashtable]$Set)
  return @($Set.Keys | Sort-Object)
}

function Test-BoolPairAllTrue {
  param(
    [object]$Status,
    [string]$PropertyName
  )
  if ($null -eq $Status) {
    return $false
  }
  $property = $Status.PSObject.Properties[$PropertyName]
  if ($null -eq $property -or $null -eq $property.Value) {
    return $false
  }
  $values = @($property.Value)
  return $values.Count -ge 2 -and [bool]$values[0] -and [bool]$values[1]
}

function Get-GripperCommandSpeed {
  param([object]$Status)
  if ($null -eq $Status -or $null -eq $Status.gripperCommand) {
    return 0
  }
  $property = $Status.gripperCommand.PSObject.Properties["speed"]
  if ($null -eq $property -or $null -eq $property.Value) {
    return 0
  }
  return [int]$property.Value
}

function Test-ZeroDeltaLastAction {
  param([object]$Status)
  if ($null -eq $Status -or $null -eq $Status.lastAction -or $null -eq $Status.lastAction.deltaVector) {
    return $false
  }
  $vector = @($Status.lastAction.deltaVector)
  if ($vector.Count -lt 12) {
    return $false
  }
  foreach ($value in $vector[0..11]) {
    if ([Math]::Abs([double]$value) -gt 1e-9) {
      return $false
    }
  }
  return $true
}

function New-AxisDiagnosticsMap {
  $map = [ordered]@{}
  $expectedOutputSigns = @(-1, 1, 1, 1, -1, -1)
  foreach ($side in @("left", "right")) {
    $axes = @("X", "Y", "Z", "Roll", "Pitch", "Yaw")
    for ($axisIndex = 0; $axisIndex -lt $axes.Count; $axisIndex++) {
      $axis = $axes[$axisIndex]
      $map["$($side):$axis"] = [ordered]@{
        sampleCount = 0
        nonZeroSamples = 0
        rawActiveSamples = 0
        requestedPulseSamples = 0
        emittedPulseSamples = 0
        outputSamples = 0
        expectedOutputSign = $expectedOutputSigns[$axisIndex]
        directionMatchSamples = 0
        directionMismatchSamples = 0
        maxAbsRawDelta = 0.0
        maxAbsFilteredDelta = 0.0
        maxAbsRequestedPulse = 0.0
        maxAbsEmittedPulse = 0.0
        maxAbsOutputDelta = 0.0
        sourceSides = @{}
      }
    }
  }
  return $map
}

function Get-ArrayNumber {
  param(
    [object]$Values,
    [int]$Index
  )
  if ($null -eq $Values) {
    return 0.0
  }
  $array = @($Values)
  if ($Index -lt 0 -or $Index -ge $array.Count -or $null -eq $array[$Index]) {
    return 0.0
  }
  return [double]$array[$Index]
}

function Update-AxisDiagnostics {
  param(
    [object]$Map,
    [string]$TargetSide,
    [int]$AxisIndex,
    [string]$SourceSide,
    [double]$RawDelta,
    [double]$FilteredDelta,
    [double]$RequestedPulse,
    [double]$EmittedPulse,
    [double]$OutputDelta
  )
  $axisNames = @("X", "Y", "Z", "Roll", "Pitch", "Yaw")
  if ($AxisIndex -lt 0 -or $AxisIndex -ge $axisNames.Count) {
    return
  }
  if ([string]::IsNullOrWhiteSpace($TargetSide) -or !($TargetSide -in @("left", "right"))) {
    return
  }
  $key = "$($TargetSide):$($axisNames[$AxisIndex])"
  if (!$Map.Contains($key)) {
    return
  }
  $stats = $Map[$key]
  $stats.sampleCount = [int]$stats.sampleCount + 1
  if (![string]::IsNullOrWhiteSpace($SourceSide)) {
    $stats.sourceSides[$SourceSide] = $true
  }
  $stats.maxAbsRawDelta = [Math]::Max([double]$stats.maxAbsRawDelta, [Math]::Abs($RawDelta))
  $stats.maxAbsFilteredDelta = [Math]::Max([double]$stats.maxAbsFilteredDelta, [Math]::Abs($FilteredDelta))
  $stats.maxAbsRequestedPulse = [Math]::Max([double]$stats.maxAbsRequestedPulse, [Math]::Abs($RequestedPulse))
  $stats.maxAbsEmittedPulse = [Math]::Max([double]$stats.maxAbsEmittedPulse, [Math]::Abs($EmittedPulse))
  $stats.maxAbsOutputDelta = [Math]::Max([double]$stats.maxAbsOutputDelta, [Math]::Abs($OutputDelta))
  if ([Math]::Abs($RawDelta) -gt 1e-9) {
    $stats.rawActiveSamples = [int]$stats.rawActiveSamples + 1
  }
  if ([Math]::Abs($RequestedPulse) -gt 1e-9) {
    $stats.requestedPulseSamples = [int]$stats.requestedPulseSamples + 1
  }
  if ([Math]::Abs($EmittedPulse) -gt 1e-9) {
    $stats.emittedPulseSamples = [int]$stats.emittedPulseSamples + 1
  }
  if ([Math]::Abs($OutputDelta) -gt 1e-9) {
    $stats.outputSamples = [int]$stats.outputSamples + 1
  }
  $directionInput = if ([Math]::Abs($FilteredDelta) -gt 1e-9) { $FilteredDelta } else { $RawDelta }
  if ([Math]::Abs($directionInput) -gt 1e-9 -and [Math]::Abs($OutputDelta) -gt 1e-9) {
    $expectedSign = [int]$stats.expectedOutputSign
    $actualSign = if (($directionInput * $OutputDelta) -ge 0.0) { 1 } else { -1 }
    if ($expectedSign -ne 0 -and $actualSign -eq $expectedSign) {
      $stats.directionMatchSamples = [int]$stats.directionMatchSamples + 1
    } elseif ($expectedSign -ne 0) {
      $stats.directionMismatchSamples = [int]$stats.directionMismatchSamples + 1
    }
  }
  if (
    [Math]::Abs($RawDelta) -gt 1e-9 -or
    [Math]::Abs($FilteredDelta) -gt 1e-9 -or
    [Math]::Abs($RequestedPulse) -gt 1e-9 -or
    [Math]::Abs($EmittedPulse) -gt 1e-9 -or
    [Math]::Abs($OutputDelta) -gt 1e-9
  ) {
    $stats.nonZeroSamples = [int]$stats.nonZeroSamples + 1
  }
}

function Convert-AxisDiagnostics {
  param([object]$Map)
  $output = [ordered]@{}
  foreach ($key in ($Map.Keys | Sort-Object)) {
    $stats = $Map[$key]
    $sampleCount = [int]$stats.sampleCount
    $outputSamples = [int]$stats.outputSamples
    $maxRequestedPulse = [double]$stats.maxAbsRequestedPulse
    $directionMatchSamples = [int]$stats.directionMatchSamples
    $directionMismatchSamples = [int]$stats.directionMismatchSamples
    $directionTotal = $directionMatchSamples + $directionMismatchSamples
    $outputDuty = if ($sampleCount -le 0) { 0.0 } else { [double]$outputSamples / [double]$sampleCount }
    $pulseEfficiency = if ($maxRequestedPulse -le 1e-9) { 0.0 } else { [double]$stats.maxAbsEmittedPulse / $maxRequestedPulse }
    $directionMatchRatio = if ($directionTotal -le 0) { 0.0 } else { [double]$directionMatchSamples / [double]$directionTotal }
    $output[$key] = [pscustomobject]@{
      sampleCount = $sampleCount
      nonZeroSamples = [int]$stats.nonZeroSamples
      rawActiveSamples = [int]$stats.rawActiveSamples
      requestedPulseSamples = [int]$stats.requestedPulseSamples
      emittedPulseSamples = [int]$stats.emittedPulseSamples
      outputSamples = $outputSamples
      expectedOutputSign = [int]$stats.expectedOutputSign
      directionMatchSamples = $directionMatchSamples
      directionMismatchSamples = $directionMismatchSamples
      directionMatchRatio = $directionMatchRatio
      outputDuty = $outputDuty
      pulseEfficiency = $pulseEfficiency
      maxAbsRawDelta = [double]$stats.maxAbsRawDelta
      maxAbsFilteredDelta = [double]$stats.maxAbsFilteredDelta
      maxAbsRequestedPulse = [double]$stats.maxAbsRequestedPulse
      maxAbsEmittedPulse = [double]$stats.maxAbsEmittedPulse
      maxAbsOutputDelta = [double]$stats.maxAbsOutputDelta
      sourceSides = @(Get-SortedKeys $stats.sourceSides)
    }
  }
  return $output
}

function New-ObservationSummary {
  param([array]$Samples)
  $axisNames = @("X", "Y", "Z", "Roll", "Pitch", "Yaw")
  $sourceSides = @{}
  $targetSides = @{}
  $sourceTargetPairs = @{}
  $movingSourceTargetPairs = @{}
  $axes = @{}
  $targetAxes = @{}
  $maxActionHistoryCount = 0
  $maxAbsDelta = 0.0
  $gripperMin = @($null, $null)
  $gripperMax = @($null, $null)
  $lastStatus = $null
  $axisDiagnostics = New-AxisDiagnosticsMap

  foreach ($sample in $Samples) {
    $status = $sample.status
    $lastStatus = $status
    $maxActionHistoryCount = [Math]::Max($maxActionHistoryCount, (Count-Actions $status))

    if ($null -ne $status -and $null -ne $status.gripperTargets) {
      $targets = @($status.gripperTargets)
      for ($i = 0; $i -lt [Math]::Min(2, $targets.Count); $i++) {
        $value = [double]$targets[$i]
        if ($null -eq $gripperMin[$i] -or $value -lt [double]$gripperMin[$i]) {
          $gripperMin[$i] = $value
        }
        if ($null -eq $gripperMax[$i] -or $value -gt [double]$gripperMax[$i]) {
          $gripperMax[$i] = $value
        }
      }
    }

    if ($null -ne $status -and $null -ne $status.inputs) {
      foreach ($sourceSide in @("left", "right")) {
        $inputProperty = $status.inputs.PSObject.Properties[$sourceSide]
        if ($null -eq $inputProperty -or $null -eq $inputProperty.Value) {
          continue
        }
        $input = $inputProperty.Value
        $targetSide = [string]$input.targetSide
        for ($axisIndex = 0; $axisIndex -lt $axisNames.Count; $axisIndex++) {
          Update-AxisDiagnostics `
            -Map $axisDiagnostics `
            -TargetSide $targetSide `
            -AxisIndex $axisIndex `
            -SourceSide $sourceSide `
            -RawDelta (Get-ArrayNumber $input.rawDelta $axisIndex) `
            -FilteredDelta (Get-ArrayNumber $input.filteredDelta $axisIndex) `
            -RequestedPulse (Get-ArrayNumber $input.requestedPulse $axisIndex) `
            -EmittedPulse (Get-ArrayNumber $input.emittedPulse $axisIndex) `
            -OutputDelta (Get-ArrayNumber $input.outputDeltaUi $axisIndex)
        }
      }
    }

    if ($null -eq $status -or $null -eq $status.actionHistory) {
      continue
    }
    foreach ($action in @($status.actionHistory)) {
      $sourceSide = [string]$action.sourceSide
      $targetSide = [string]$action.side
      $actionHasMotion = $false
      Add-ObservedValue -Set $sourceSides -Value $action.sourceSide
      Add-ObservedValue -Set $targetSides -Value $action.side
      Add-ObservedValue -Set $axes -Value $action.axis
      if (![string]::IsNullOrWhiteSpace($sourceSide) -and ![string]::IsNullOrWhiteSpace($targetSide)) {
        Add-ObservedValue -Set $sourceTargetPairs -Value "$sourceSide->$targetSide"
      }
      if (![string]::IsNullOrWhiteSpace($targetSide) -and $null -ne $action.axis) {
        Add-ObservedValue -Set $targetAxes -Value "$($targetSide):$($action.axis)"
      }
      if ($null -ne $action.delta) {
        $deltaAbs = [Math]::Abs([double]$action.delta)
        $maxAbsDelta = [Math]::Max($maxAbsDelta, $deltaAbs)
        if ($deltaAbs -gt 1e-9) {
          $actionHasMotion = $true
        }
      }
      if ($null -ne $action.deltaVector) {
        $vector = @($action.deltaVector)
        for ($i = 0; $i -lt $vector.Count; $i++) {
          $delta = [double]$vector[$i]
          $maxAbsDelta = [Math]::Max($maxAbsDelta, [Math]::Abs($delta))
          if ([Math]::Abs($delta) -gt 1e-9) {
            $actionHasMotion = $true
            $axisName = $axisNames[$i % 6]
            $vectorTargetSide = if ($i -lt 6) { "left" } else { "right" }
            Add-ObservedValue -Set $axes -Value $axisName
            Add-ObservedValue -Set $targetAxes -Value "$($vectorTargetSide):$axisName"
          }
        }
      }
      if ($actionHasMotion -and ![string]::IsNullOrWhiteSpace($sourceSide) -and ![string]::IsNullOrWhiteSpace($targetSide)) {
        Add-ObservedValue -Set $movingSourceTargetPairs -Value "$sourceSide->$targetSide"
      }
    }
  }

  $leftRange = if ($null -eq $gripperMin[0] -or $null -eq $gripperMax[0]) { 0.0 } else { [double]$gripperMax[0] - [double]$gripperMin[0] }
  $rightRange = if ($null -eq $gripperMin[1] -or $null -eq $gripperMax[1]) { 0.0 } else { [double]$gripperMax[1] - [double]$gripperMin[1] }

  return [pscustomobject]@{
    directionPolicy = $directionPolicy
    maxActionHistoryCount = $maxActionHistoryCount
    observedSourceSides = @(Get-SortedKeys $sourceSides)
    observedTargetSides = @(Get-SortedKeys $targetSides)
    observedSourceTargetPairs = @(Get-SortedKeys $sourceTargetPairs)
    observedMovingSourceTargetPairs = @(Get-SortedKeys $movingSourceTargetPairs)
    observedAxes = @(Get-SortedKeys $axes)
    observedTargetAxes = @(Get-SortedKeys $targetAxes)
    maxAbsDelta = $maxAbsDelta
    axisDiagnostics = Convert-AxisDiagnostics $axisDiagnostics
    gripperTargetRanges = [pscustomobject]@{
      leftMm = $leftRange
      rightMm = $rightRange
    }
    lastStatus = $lastStatus
  }
}

$startedAt = Get-Date
$health = Invoke-HalJson -Method "GET" -Path "/health"
$before = Invoke-HalJson -Method "GET" -Path "/teleop/native/status"

$noMotion = $null
if (!$SkipNoMotionProbe) {
  $payload = @{
    leftConnected = $false
    rightConnected = $false
    controlMode = "incremental_position"
    nativeLoopHz = 100
    gripperTeleopEnabled = $false
  }
  $start = Invoke-HalJson -Method "POST" -Path "/teleop/native/start" -Body $payload
  Start-Sleep -Milliseconds 250
  $during = Invoke-HalJson -Method "GET" -Path "/teleop/native/status"
  $stop = Invoke-HalJson -Method "POST" -Path "/teleop/native/stop" -Body @{}
  Start-Sleep -Milliseconds 100
  $after = Invoke-HalJson -Method "GET" -Path "/teleop/native/status"

  $noMotionPass = (
    [bool]$start.ok -and
    !( [bool]$after.running ) -and
    (Count-Actions $during) -eq 0 -and
    (Count-Actions $after) -eq 0 -and
    (Is-ZeroTargets $during) -and
    (Is-ZeroTargets $after)
  )

  $noMotion = [pscustomobject]@{
    pass = $noMotionPass
    start = $start
    during = $during
    stop = $stop
    after = $after
  }
}

$samples = @()
if ($ObserveSeconds -gt 0) {
  Write-Host "Observation mode only reads /teleop/native/status. Use the UI to connect hands and move under supervision."
  if ($Strict) {
    Write-Host "Strict HAL-native teleop checklist:"
    Write-Host "  1. Move left Omega.7 to drive the right arm."
    Write-Host "  2. Move right Omega.7 to drive the left arm."
    Write-Host "  3. Exercise X/Y/Z/Roll/Pitch/Yaw on both target arms."
    Write-Host "  4. Open and close both grippers."
    Write-Host "  5. Return both Omega.7 hands to center and confirm motion stops."
  }
  $deadline = (Get-Date).AddSeconds($ObserveSeconds)
  while ((Get-Date) -lt $deadline) {
    $sample = Invoke-HalJson -Method "GET" -Path "/teleop/native/status"
    $samples += [pscustomobject]@{
      ts = (Get-Date).ToString("o")
      status = $sample
    }
    Start-Sleep -Milliseconds 250
  }
}

$observationSummary = New-ObservationSummary $samples
$observedActions = $observationSummary.maxActionHistoryCount
$actionCapturePass = $null
if ($RequireActions) {
  $actionCapturePass = $ObserveSeconds -gt 0 -and $observedActions -gt 0 -and [double]$observationSummary.maxAbsDelta -gt 1e-9
}

$statusForGates = $before
if ($samples.Count -gt 0) {
  $statusForGates = $samples[-1].status
} elseif ($null -ne $noMotion) {
  $statusForGates = $noMotion.after
}
$gateFailures = @()
if ($RequireActions -and !$actionCapturePass) {
  $gateFailures += "No non-zero native teleop actions were captured during ObserveSeconds=$ObserveSeconds"
}
if ($RequireLeftAction -and !($observationSummary.observedTargetSides -contains "left")) {
  $gateFailures += "No left target-side native teleop action was captured"
}
if ($RequireRightAction -and !($observationSummary.observedTargetSides -contains "right")) {
  $gateFailures += "No right target-side native teleop action was captured"
}
if ($RequireCrossMapping) {
  if (!($observationSummary.observedSourceTargetPairs -contains "left->right")) {
    $gateFailures += "No left->right native teleop action was captured"
  }
  if (!($observationSummary.observedSourceTargetPairs -contains "right->left")) {
    $gateFailures += "No right->left native teleop action was captured"
  }
  if (!($observationSummary.observedMovingSourceTargetPairs -contains "left->right")) {
    $gateFailures += "No non-zero left->right native teleop action was captured"
  }
  if (!($observationSummary.observedMovingSourceTargetPairs -contains "right->left")) {
    $gateFailures += "No non-zero right->left native teleop action was captured"
  }
}
$requiredTargetAxes = @()
foreach ($targetSide in @("left", "right")) {
  foreach ($axis in @("X", "Y", "Z", "Roll", "Pitch", "Yaw")) {
    $requiredTargetAxes += "$($targetSide):$axis"
  }
}
$missingAxes = @($requiredTargetAxes | Where-Object { !($observationSummary.observedTargetAxes -contains $_) })
if ($RequireAllAxes -and $missingAxes.Count -gt 0) {
  $gateFailures += "Not all semantic axes were captured: $($missingAxes -join ', ')"
}
if ($RequireGripperChange) {
  $leftGripRange = [double]$observationSummary.gripperTargetRanges.leftMm
  $rightGripRange = [double]$observationSummary.gripperTargetRanges.rightMm
  if ($leftGripRange -lt 0.5 -or $rightGripRange -lt 0.5) {
    $gateFailures += "Native gripper targets did not change on both channels by at least 0.5mm"
  }
}
$forceOutputAllEnabled = Test-BoolPairAllTrue -Status $statusForGates -PropertyName "forceOutputEnabled"
$gravityCompensationAllEnabled = Test-BoolPairAllTrue -Status $statusForGates -PropertyName "gravityCompensation"
$logicalConnectedAllEnabled = Test-BoolPairAllTrue -Status $statusForGates -PropertyName "logicalConnected"
$gripperCommandSpeed = Get-GripperCommandSpeed -Status $statusForGates
$gripperCommandSpeedOk = $gripperCommandSpeed -ge 200
$zeroStopObserved = Test-ZeroDeltaLastAction -Status $statusForGates
if ($RequireCrossMapping -and !$logicalConnectedAllEnabled) {
  $gateFailures += "logicalConnected is not true for both Omega.7 hands; connect both logical hands before ObserveSeconds/Strict"
}
if ($RequireGripperChange -and !$gripperCommandSpeedOk) {
  $gateFailures += "Native gripper command speed is $gripperCommandSpeed; expected >=200 to match Omega.7 gripper teleop speed"
}
if ($RequireForceOutput -and !$forceOutputAllEnabled) {
  $gateFailures += "forceOutputEnabled is not true for both Omega.7 devices"
}
if ($RequireGravityCompensation -and !$gravityCompensationAllEnabled) {
  $gateFailures += "gravityCompensation is not true for both Omega.7 devices"
}
if ($RequireZeroStop -and !$zeroStopObserved) {
  $gateFailures += "No final zero-delta native teleop stop was observed"
}
$gatesPass = $gateFailures.Count -eq 0

$result = [pscustomobject]@{
  schema = "hal-native-teleop-acceptance-v1"
  startedAt = $startedAt.ToString("o")
  finishedAt = (Get-Date).ToString("o")
  baseUrl = $baseUrl
  health = $health
  before = $before
  noMotionProbe = $noMotion
  observation = [pscustomobject]@{
    seconds = $ObserveSeconds
    strict = $strictRequested
    sampleCount = $samples.Count
    maxActionHistoryCount = $observedActions
    requireActions = [bool]$RequireActions
    pass = $actionCapturePass
    summary = $observationSummary
    gates = [pscustomobject]@{
      requireLeftAction = [bool]$RequireLeftAction
      requireRightAction = [bool]$RequireRightAction
      requireCrossMapping = [bool]$RequireCrossMapping
      requireAllAxes = [bool]$RequireAllAxes
      missingAxes = $missingAxes
      requireGripperChange = [bool]$RequireGripperChange
      requireForceOutput = [bool]$RequireForceOutput
      requireGravityCompensation = [bool]$RequireGravityCompensation
      requireZeroStop = [bool]$RequireZeroStop
      forceOutputAllEnabled = $forceOutputAllEnabled
      gravityCompensationAllEnabled = $gravityCompensationAllEnabled
      logicalConnectedAllEnabled = $logicalConnectedAllEnabled
      gripperCommandSpeed = $gripperCommandSpeed
      gripperCommandSpeedOk = $gripperCommandSpeedOk
      zeroStopObserved = $zeroStopObserved
      pass = $gatesPass
      gateFailures = $gateFailures
    }
    samples = $samples
  }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$path = Join-Path $OutputDir "hal-native-teleop-$stamp.json"
$result | ConvertTo-Json -Depth 16 | Set-Content -Path $path -Encoding UTF8

[pscustomobject]@{
  report = $path
  health = @{
    ltdmc_ok = $health.ltdmc_ok
    omega7_ok = $health.omega7_ok
  }
  noMotionPass = if ($null -eq $noMotion) { $null } else { $noMotion.pass }
  observationSamples = $samples.Count
  strict = $strictRequested
  maxActionHistoryCount = $observedActions
  observedSourceSides = $observationSummary.observedSourceSides
  observedTargetSides = $observationSummary.observedTargetSides
  observedSourceTargetPairs = $observationSummary.observedSourceTargetPairs
  observedMovingSourceTargetPairs = $observationSummary.observedMovingSourceTargetPairs
  observedAxes = $observationSummary.observedAxes
  observedTargetAxes = $observationSummary.observedTargetAxes
  missingAxes = $missingAxes
  gripperTargetRanges = $observationSummary.gripperTargetRanges
  forceOutputAllEnabled = $forceOutputAllEnabled
  gravityCompensationAllEnabled = $gravityCompensationAllEnabled
  logicalConnectedAllEnabled = $logicalConnectedAllEnabled
  gripperCommandSpeed = $gripperCommandSpeed
  gripperCommandSpeedOk = $gripperCommandSpeedOk
  directionPolicy = $directionPolicy
  zeroStopObserved = $zeroStopObserved
  gateFailures = $gateFailures
  actionCapturePass = $actionCapturePass
}

if ($VerifyReport) {
  $verifyScript = Join-Path $PSScriptRoot "verify-hal-native-teleop-report.ps1"
  if (!(Test-Path -LiteralPath $verifyScript)) {
    [Console]::Error.WriteLine("Verifier script not found: $verifyScript")
    exit 1
  }
  & powershell -ExecutionPolicy Bypass -File $verifyScript -ReportPath $path
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

if (!$gatesPass) {
  [Console]::Error.WriteLine("Native teleop acceptance gates failed: $($gateFailures -join '; '). Report: $path")
  exit 2
}
