param(
  [int]$BackendPort = 18082,
  [int]$HalPort = 8091,
  [int]$ObserveSeconds = 0,
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

$axisNames = @("X", "Y", "Z", "Roll", "Pitch", "Yaw")
$axisIoBitNames = @{
  0 = "ALM"
  1 = "EL+"
  2 = "EL-"
  3 = "EMG"
  4 = "ORG"
  6 = "SL+"
  7 = "SL-"
  8 = "INP"
  9 = "EZ"
  10 = "RDY"
  11 = "DSTP"
}
$stopReasonMeanings = @{
  0 = "normal stop"
  5 = "positive hard limit immediate stop"
  6 = "negative hard limit immediate stop"
  7 = "positive hard limit decel stop"
  8 = "negative hard limit decel stop"
}
$updateReturnMeanings = @{
  3006 = "positive hard limit blocks positive PMOVE start"
  3007 = "negative hard limit blocks negative PMOVE start"
  3008 = "ALM signal blocks PMOVE start"
  3009 = "positive soft limit blocks positive PMOVE start"
  3010 = "negative soft limit blocks negative PMOVE start"
  3011 = "axis idle or not in PMOVE mode; target update failed"
  3019 = "PMOVE already ended; online target update failed"
}

function Invoke-Json {
  param([string]$Uri)
  return Invoke-RestMethod -Uri $Uri -TimeoutSec 5
}

function Get-ActionMs {
  param([object]$Action)
  if ($null -eq $Action) {
    return 0.0
  }
  $mono = $Action.PSObject.Properties["monotonicMs"]
  if ($null -ne $mono -and $null -ne $mono.Value) {
    return [double]$mono.Value
  }
  $ts = $Action.PSObject.Properties["ts"]
  if ($null -ne $ts -and $null -ne $ts.Value) {
    return [double]$ts.Value
  }
  return 0.0
}

function Test-NonZeroArray {
  param([object]$Values)
  if ($null -eq $Values) {
    return $false
  }
  foreach ($value in @($Values)) {
    if ([Math]::Abs([double]$value) -gt 1e-9) {
      return $true
    }
  }
  return $false
}

function Add-Count {
  param(
    [hashtable]$Map,
    [string]$Key
  )
  if ([string]::IsNullOrWhiteSpace($Key)) {
    $Key = "-"
  }
  if (!$Map.ContainsKey($Key)) {
    $Map[$Key] = 0
  }
  $Map[$Key] = [int]$Map[$Key] + 1
}

function Convert-CountMap {
  param([hashtable]$Map)
  $ordered = [ordered]@{}
  foreach ($key in @($Map.Keys | Sort-Object)) {
    $ordered[$key] = $Map[$key]
  }
  return $ordered
}

function Get-MapValue {
  param(
    [hashtable]$Map,
    [int]$Key
  )
  if ($Map.ContainsKey($Key)) {
    return $Map[$Key]
  }
  return ""
}

function Get-ActiveAxisIoSignals {
  param([object]$AxisIoStatus)
  if ($null -eq $AxisIoStatus) {
    return @()
  }
  $value = [int64]$AxisIoStatus
  $signals = @()
  foreach ($bit in @($axisIoBitNames.Keys | Sort-Object)) {
    if (($value -band (1 -shl [int]$bit)) -ne 0) {
      $signals += $axisIoBitNames[[int]$bit]
    }
  }
  return $signals
}

function Get-Percentile {
  param(
    [object[]]$SortedValues,
    [double]$Fraction
  )
  if ($SortedValues.Count -eq 0) {
    return $null
  }
  $index = [int][Math]::Floor(($SortedValues.Count - 1) * $Fraction)
  return [Math]::Round([double]$SortedValues[$index], 3)
}

function Get-GapStats {
  param([object[]]$Actions)
  $items = @($Actions | Sort-Object { Get-ActionMs $_ })
  $gaps = @()
  for ($i = 1; $i -lt $items.Count; $i++) {
    $gap = (Get-ActionMs $items[$i]) - (Get-ActionMs $items[$i - 1])
    if ($gap -ge 0) {
      $gaps += $gap
    }
  }
  $sorted = @($gaps | Sort-Object)
  $span = 0.0
  if ($items.Count -gt 1) {
    $span = (Get-ActionMs $items[-1]) - (Get-ActionMs $items[0])
  }
  return [ordered]@{
    count = $items.Count
    spanMs = [Math]::Round($span, 3)
    gapMinMs = Get-Percentile $sorted 0.0
    gapP50Ms = Get-Percentile $sorted 0.5
    gapP90Ms = Get-Percentile $sorted 0.9
    gapP99Ms = Get-Percentile $sorted 0.99
    gapMaxMs = Get-Percentile $sorted 1.0
  }
}

function Get-UpdateReturnStats {
  param([object[]]$Actions)

  $byCode = @{}
  $byAxis = @{}
  $bySide = @{}
  $examples = @()
  $total = 0

  foreach ($action in @($Actions)) {
    $updateReturn = @($action.updateReturn)
    $requestedDeltaPulse = @($action.requestedDeltaPulse)
    $appliedDeltaPulse = @($action.appliedDeltaPulse)
    $targetPulse = @($action.targetPulse)
    $currentPulse = @($action.currentPulse)
    $launchDeltaPulse = @($action.launchDeltaPulse)
    $movingBefore = @($action.movingBefore)
    $moveStarted = @($action.moveStarted)

    for ($i = 0; $i -lt [Math]::Min(6, $updateReturn.Count); $i++) {
      $ret = [int][double]$updateReturn[$i]
      if ($ret -eq 0) {
        continue
      }

      $axis = $axisNames[$i]
      $sideKey = "$($action.sourceSide)->$($action.side)"
      Add-Count $byCode ([string]$ret)
      Add-Count $byAxis $axis
      Add-Count $bySide $sideKey
      $total += 1

      if ($examples.Count -lt 12) {
        $examples += [ordered]@{
          ret = $ret
          meaning = Get-MapValue $updateReturnMeanings $ret
          side = $action.side
          sourceSide = $action.sourceSide
          axis = $axis
          requestedDeltaPulse = if ($i -lt $requestedDeltaPulse.Count) { [double]$requestedDeltaPulse[$i] } else { $null }
          appliedDeltaPulse = if ($i -lt $appliedDeltaPulse.Count) { [double]$appliedDeltaPulse[$i] } else { $null }
          targetPulse = if ($i -lt $targetPulse.Count) { [double]$targetPulse[$i] } else { $null }
          currentPulse = if ($i -lt $currentPulse.Count) { [double]$currentPulse[$i] } else { $null }
          launchDeltaPulse = if ($i -lt $launchDeltaPulse.Count) { [double]$launchDeltaPulse[$i] } else { $null }
          movingBefore = if ($i -lt $movingBefore.Count) { [bool]$movingBefore[$i] } else { $null }
          moveStarted = if ($i -lt $moveStarted.Count) { [bool]$moveStarted[$i] } else { $null }
        }
      }
    }
  }

  return [ordered]@{
    totalNonZero = $total
    updateReturnByCode = Convert-CountMap $byCode
    updateReturnByAxis = Convert-CountMap $byAxis
    updateReturnBySide = Convert-CountMap $bySide
    examples = $examples
  }
}

function Get-ActionStats {
  param([object[]]$Actions)

  $nonZero = @($Actions | Where-Object { Test-NonZeroArray $_.deltas })
  $axisCounts = @{}
  $sideCounts = @{}
  $movingBeforeAxisCounts = @{}
  $moveStartedAxisCounts = @{}
  $updateReturnNonZero = 0
  $clippedCount = 0
  $multiAxisActions = 0

  foreach ($action in $nonZero) {
    Add-Count $axisCounts ([string]$action.axis)
    Add-Count $sideCounts "$($action.sourceSide)->$($action.side)"

    $nonZeroAxes = 0
    $deltas = @($action.deltas)
    for ($i = 0; $i -lt [Math]::Min(6, $deltas.Count); $i++) {
      if ([Math]::Abs([double]$deltas[$i]) -gt 1e-9) {
        $nonZeroAxes += 1
      }
    }
    if ($nonZeroAxes -gt 1) {
      $multiAxisActions += 1
    }

    $movingBefore = @($action.movingBefore)
    $moveStarted = @($action.moveStarted)
    $updateReturn = @($action.updateReturn)
    $clipped = @($action.clipped)
    for ($i = 0; $i -lt $axisNames.Count; $i++) {
      if ($i -lt $movingBefore.Count -and [bool]$movingBefore[$i]) {
        Add-Count $movingBeforeAxisCounts $axisNames[$i]
      }
      if ($i -lt $moveStarted.Count -and [bool]$moveStarted[$i]) {
        Add-Count $moveStartedAxisCounts $axisNames[$i]
      }
      if ($i -lt $updateReturn.Count -and [Math]::Abs([double]$updateReturn[$i]) -gt 1e-9) {
        $updateReturnNonZero += 1
      }
      if ($i -lt $clipped.Count -and [bool]$clipped[$i]) {
        $clippedCount += 1
      }
    }
  }

  $bySide = [ordered]@{}
  foreach ($sideKey in @($sideCounts.Keys | Sort-Object)) {
    $sideActions = @($nonZero | Where-Object { "$($_.sourceSide)->$($_.side)" -eq $sideKey })
    $bySide[$sideKey] = Get-GapStats $sideActions
  }
  $updateReturnStats = Get-UpdateReturnStats $Actions

  return [ordered]@{
    all = Get-GapStats $Actions
    nonZero = Get-GapStats $nonZero
    zeroActionCount = @($Actions).Count - $nonZero.Count
    axisCounts = Convert-CountMap $axisCounts
    sideCounts = Convert-CountMap $sideCounts
    bySide = $bySide
    movingBeforeAxisCounts = Convert-CountMap $movingBeforeAxisCounts
    moveStartedAxisCounts = Convert-CountMap $moveStartedAxisCounts
    multiAxisActions = $multiAxisActions
    updateReturnNonZero = $updateReturnNonZero
    updateReturnByCode = $updateReturnStats.updateReturnByCode
    updateReturnByAxis = $updateReturnStats.updateReturnByAxis
    updateReturnBySide = $updateReturnStats.updateReturnBySide
    updateReturnExamples = $updateReturnStats.examples
    clippedCount = $clippedCount
  }
}

function Get-AxisDiagnosticsSummary {
  param([object]$AxisDiagnostics)

  $summary = @()
  foreach ($axis in @($AxisDiagnostics.axes)) {
    $signals = Get-ActiveAxisIoSignals $axis.axisIoStatus
    $stopReason = 0
    if ($null -ne $axis.stopReason) {
      $stopReason = [int]$axis.stopReason
    }

    $summary += [ordered]@{
      side = $axis.side
      axis = $axis.axis
      card = $axis.card
      physicalAxis = $axis.physicalAxis
      pulse = $axis.pulse
      uiPosition = $axis.uiPosition
      enabled = $axis.enabled
      commandedEnabled = $axis.commandedEnabled
      checkDone = $axis.checkDone
      axisIoStatus = $axis.axisIoStatus
      axisIoStatusHex = $axis.axisIoStatusHex
      activeSignals = $signals
      hardLimitActive = ($signals -contains "EL+" -or $signals -contains "EL-")
      stopReason = $axis.stopReason
      stopReasonMeaning = Get-MapValue $stopReasonMeanings $stopReason
      elEnable = $axis.elEnable
      elLogic = $axis.elLogic
      elMode = $axis.elMode
    }
  }
  return $summary
}

$backendBase = "http://127.0.0.1:$BackendPort"
$halBase = "http://127.0.0.1:$HalPort"

$settings = Invoke-Json -Uri "http://127.0.0.1:$BackendPort/api/settings"
$teleopState = Invoke-Json -Uri "http://127.0.0.1:$BackendPort/api/teleop/state"
$halHealth = Invoke-Json -Uri "http://127.0.0.1:$HalPort/health"
$axisDiagnostics = Invoke-Json -Uri "http://127.0.0.1:$HalPort/motion/axis_diagnostics"
$initialStatus = Invoke-Json -Uri "http://127.0.0.1:$HalPort/teleop/native/status"

if ($ObserveSeconds -gt 0) {
  Start-Sleep -Seconds $ObserveSeconds
}

$status = Invoke-Json -Uri "http://127.0.0.1:$HalPort/teleop/native/status"
$actions = @($status.actionHistory)
$actionStats = Get-ActionStats $actions
$axisDiagnosticsSummary = Get-AxisDiagnosticsSummary $axisDiagnostics
$warnings = @()

if ($settings.teleop.kalmanFilterEnabled) {
  $warnings += "teleop.kalmanFilterEnabled=true: native deltas are filtered and intent-weighted before pulse gating."
}
if ($status.lastError) {
  $warnings += "HAL-native lastError is not empty: $($status.lastError)"
}
if (!$halHealth.ltdmc_ok -or !$halHealth.omega7_ok) {
  $warnings += "HAL health is not fully ok: ltdmc_ok=$($halHealth.ltdmc_ok), omega7_ok=$($halHealth.omega7_ok)"
}
if ($actions.Count -eq 0) {
  $warnings += "No native actionHistory entries were available."
}
if ($actionStats.updateReturnNonZero -gt 0) {
  $warnings += "HAL native actionHistory contains non-zero LTDMC updateReturn values. Check actionHistory.updateReturnByCode and axisDiagnostics."
}
foreach ($axis in @($axisDiagnosticsSummary)) {
  if ($axis.hardLimitActive) {
    $warnings += "HAL axis hard limit active: $($axis.side) $($axis.axis) card=$($axis.card) physicalAxis=$($axis.physicalAxis) signals=$($axis.activeSignals -join ',') stopReason=$($axis.stopReason) $($axis.stopReasonMeaning)"
  } elseif ($axis.stopReason -and [int]$axis.stopReason -ne 0) {
    $warnings += "HAL axis non-normal stop reason: $($axis.side) $($axis.axis) card=$($axis.card) physicalAxis=$($axis.physicalAxis) stopReason=$($axis.stopReason) $($axis.stopReasonMeaning)"
  }
}

$result = [ordered]@{
  capturedAt = (Get-Date).ToString("o")
  endpoints = [ordered]@{
    backendBase = $backendBase
    halBase = $halBase
  }
  config = [ordered]@{
    engine = $settings.teleop.engine
    controlMode = $settings.teleop.controlMode
    kalmanFilterEnabled = [bool]$settings.teleop.kalmanFilterEnabled
    nativeLoopHz = [int]$settings.teleop.nativeLoopHz
    commandIntervalMs = [double]$settings.teleop.commandIntervalMs
    translationInputEpsilon = [double]$settings.teleop.translationInputEpsilon
    rotationInputEpsilon = [double]$settings.teleop.rotationInputEpsilon
    translationMinActivePulse = [double]$settings.teleop.translationMinActivePulse
    rotationMinActivePulse = [double]$settings.teleop.rotationMinActivePulse
    diagLog = [bool]$settings.teleop.diagLog
  }
  health = [ordered]@{
    ltdmc_ok = [bool]$halHealth.ltdmc_ok
    omega7_ok = [bool]$halHealth.omega7_ok
    version = $halHealth.version
    uptime_s = $halHealth.uptime_s
  }
  native = [ordered]@{
    initiallyRunning = [bool]$initialStatus.running
    running = [bool]$status.running
    controlMode = $status.controlMode
    lastError = $status.lastError
    logicalConnected = @($status.logicalConnected)
    blockers = $status.blockers
    inputs = $status.inputs
    lastAction = $status.lastAction
  }
  axisDiagnostics = $axisDiagnosticsSummary
  teleopState = [ordered]@{
    timestamp_ms = $teleopState.data.timestamp_ms
    received_timestamp_ms = $teleopState.data.received_timestamp_ms
    hands = $teleopState.data.hands
  }
  actionHistory = $actionStats
  warnings = $warnings
}

$json = $result | ConvertTo-Json -Depth 16
if (![string]::IsNullOrWhiteSpace($OutputPath)) {
  $parent = Split-Path -Parent $OutputPath
  if (![string]::IsNullOrWhiteSpace($parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $json | Set-Content -Path $OutputPath -Encoding UTF8
}
$json
