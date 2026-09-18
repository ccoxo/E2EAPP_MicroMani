param(
  [string]$ReportPath = "",
  [string]$AcceptanceDir = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($AcceptanceDir)) {
  $AcceptanceDir = Join-Path $repo "backend\runtime\acceptance"
}
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
  $latest = Get-ChildItem -LiteralPath $AcceptanceDir -Filter "hal-native-teleop-*.json" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($null -eq $latest) {
    [Console]::Error.WriteLine("No hal-native-teleop acceptance report found in $AcceptanceDir")
    exit 1
  }
  $ReportPath = $latest.FullName
}

if (!(Test-Path -LiteralPath $ReportPath)) {
  [Console]::Error.WriteLine("Acceptance report not found: $ReportPath")
  exit 1
}

$report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
$failures = New-Object System.Collections.Generic.List[string]
$expectedDirectionPolicy = "current-hal-native-cross-map-v1"

function Add-Failure {
  param([string]$Message)
  if (![string]::IsNullOrWhiteSpace($Message)) {
    $script:failures.Add($Message)
  }
}

function As-Array {
  param([object]$Value)
  if ($null -eq $Value) {
    return @()
  }
  return @($Value)
}

function Has-Value {
  param(
    [object]$Value,
    [string]$Needle
  )
  return (As-Array $Value) -contains $Needle
}

function Number-Value {
  param([object]$Value)
  if ($null -eq $Value) {
    return 0.0
  }
  return [double]$Value
}

function Classify-AxisDiagnostic {
  param([object]$Diagnostic)
  if ($null -eq $Diagnostic) {
    return "missing diagnostics"
  }
  $sampleCount = [int](Number-Value $Diagnostic.sampleCount)
  $maxRaw = Number-Value $Diagnostic.maxAbsRawDelta
  $maxFiltered = Number-Value $Diagnostic.maxAbsFilteredDelta
  $maxRequestedPulse = Number-Value $Diagnostic.maxAbsRequestedPulse
  $maxEmittedPulse = Number-Value $Diagnostic.maxAbsEmittedPulse
  $maxOutput = Number-Value $Diagnostic.maxAbsOutputDelta
  if ($sampleCount -le 0) {
    return "not sampled"
  }
  if ($maxOutput -gt 1e-9 -or $maxEmittedPulse -gt 1e-9) {
    return "output observed"
  }
  if ($maxRaw -le 1e-9) {
    return "no raw input"
  }
  if ($maxFiltered -le 1e-9) {
    return "filtered out before pulse conversion"
  }
  if ($maxRequestedPulse -le 1e-9) {
    return "no requested pulse after conversion"
  }
  if ($maxEmittedPulse -le 1e-9) {
    return "pulse gated or below deadband"
  }
  return "output delta remained zero despite emitted pulse"
}

if ($report.schema -ne "hal-native-teleop-acceptance-v1") {
  Add-Failure "Unexpected report schema: $($report.schema)"
}
if (!([bool]$report.health.ltdmc_ok)) {
  Add-Failure "HAL health ltdmc_ok is not true"
}
if (!([bool]$report.health.omega7_ok)) {
  Add-Failure "HAL health omega7_ok is not true"
}
if (!([bool]$report.noMotionProbe.pass)) {
  Add-Failure "No-motion probe did not pass"
}

$observation = $report.observation
$summary = $observation.summary
$gates = $observation.gates
$directionPolicy = [string]$summary.directionPolicy
if ($directionPolicy -ne $expectedDirectionPolicy) {
  Add-Failure "Unexpected directionPolicy: $directionPolicy; expected $expectedDirectionPolicy"
}
if ([int]$observation.sampleCount -le 0) {
  Add-Failure "Observation mode was not run or captured no samples; collect a backend/UI DDS native teleop report with ObserveSeconds and the required gates while moving both Omega.7 hands"
}
if ([int]$observation.maxActionHistoryCount -le 0) {
  Add-Failure "Observation captured no native teleop actions"
}
if ($observation.requireActions -and !([bool]$observation.pass)) {
  Add-Failure "Observation action gate did not pass"
}
if (!([bool]$gates.pass)) {
  Add-Failure "Observation gates did not pass"
}
foreach ($failure in (As-Array $gates.gateFailures)) {
  Add-Failure ([string]$failure)
}

if (!(Has-Value $summary.observedSourceTargetPairs "left->right")) {
  Add-Failure "Missing left->right source-target pair"
}
if (!(Has-Value $summary.observedSourceTargetPairs "right->left")) {
  Add-Failure "Missing right->left source-target pair"
}
if (!(Has-Value $summary.observedMovingSourceTargetPairs "left->right")) {
  Add-Failure "Missing non-zero left->right source-target pair"
}
if (!(Has-Value $summary.observedMovingSourceTargetPairs "right->left")) {
  Add-Failure "Missing non-zero right->left source-target pair"
}
if ((Number-Value $summary.maxAbsDelta) -le 1e-9) {
  Add-Failure "Observation captured no non-zero native teleop delta"
}

$requiredAxes = @()
foreach ($side in @("left", "right")) {
  foreach ($axis in @("X", "Y", "Z", "Roll", "Pitch", "Yaw")) {
    $requiredAxes += "$($side):$axis"
  }
}
$missingAxes = @()
foreach ($axis in $requiredAxes) {
  if (!(Has-Value $summary.observedTargetAxes $axis)) {
    $missingAxes += $axis
  }
}
foreach ($axis in (As-Array $gates.missingAxes)) {
  if (!($missingAxes -contains $axis)) {
    $missingAxes += [string]$axis
  }
}
if ($missingAxes.Count -gt 0) {
  Add-Failure "Missing semantic axes: $($missingAxes -join ', ')"
}

$axisDiagnostics = $summary.axisDiagnostics
$axisFailureCauses = [ordered]@{}
$axisRows = @()
foreach ($axis in $requiredAxes) {
  $diagnosticProperty = if ($null -eq $axisDiagnostics) { $null } else { $axisDiagnostics.PSObject.Properties[$axis] }
  if ($null -eq $diagnosticProperty -or $null -eq $diagnosticProperty.Value) {
    $axisFailureCauses[$axis] = "missing diagnostics"
    $axisRows += [pscustomobject]@{
      axis = $axis
      samples = 0
      nonZero = 0
      outputDuty = 0.0
      pulseEfficiency = 0.0
      raw = 0.0
      filtered = 0.0
      requestedPulse = 0.0
      emittedPulse = 0.0
      output = 0.0
      cause = "missing diagnostics"
    }
    Add-Failure "Missing axis diagnostics: $axis"
    continue
  }
  $diagnostic = $diagnosticProperty.Value
  $cause = Classify-AxisDiagnostic $diagnostic
  $sampleCount = [int](Number-Value $diagnostic.sampleCount)
  $outputSamples = [int](Number-Value $diagnostic.outputSamples)
  $maxRequestedPulse = Number-Value $diagnostic.maxAbsRequestedPulse
  $maxOutput = Number-Value $diagnostic.maxAbsOutputDelta
  $maxEmittedPulse = Number-Value $diagnostic.maxAbsEmittedPulse
  $expectedOutputSign = [int](Number-Value $diagnostic.expectedOutputSign)
  $directionMatchSamples = [int](Number-Value $diagnostic.directionMatchSamples)
  $directionMismatchSamples = [int](Number-Value $diagnostic.directionMismatchSamples)
  $outputDuty = if ($sampleCount -le 0 -or $maxOutput -le 1e-9) { 0.0 } else { [double]$outputSamples / [double]$sampleCount }
  $pulseEfficiency = if ($maxRequestedPulse -le 1e-9) { 0.0 } else { (Number-Value $diagnostic.maxAbsEmittedPulse) / $maxRequestedPulse }
  $directionTotal = $directionMatchSamples + $directionMismatchSamples
  $directionMatchRatio = if ($directionTotal -le 0) { 0.0 } else { [double]$directionMatchSamples / [double]$directionTotal }
  $axisRows += [pscustomobject]@{
    axis = $axis
    samples = $sampleCount
    nonZero = [int](Number-Value $diagnostic.nonZeroSamples)
    outputDuty = $outputDuty
    pulseEfficiency = $pulseEfficiency
    expectedOutputSign = $expectedOutputSign
    directionMatchSamples = $directionMatchSamples
    directionMismatchSamples = $directionMismatchSamples
    directionMatchRatio = $directionMatchRatio
    raw = Number-Value $diagnostic.maxAbsRawDelta
    filtered = Number-Value $diagnostic.maxAbsFilteredDelta
    requestedPulse = Number-Value $diagnostic.maxAbsRequestedPulse
    emittedPulse = Number-Value $diagnostic.maxAbsEmittedPulse
    output = Number-Value $diagnostic.maxAbsOutputDelta
    cause = $cause
  }
  if ($maxOutput -le 1e-9 -and $maxEmittedPulse -le 1e-9) {
    $axisFailureCauses[$axis] = $cause
    Add-Failure "Axis diagnostics show no non-zero output: $axis ($cause)"
  }
  if ($expectedOutputSign -ne 0 -and $directionMismatchSamples -gt $directionMatchSamples) {
    Add-Failure "Axis direction mismatch: $axis expected raw/output polarity=$expectedOutputSign matches=$directionMatchSamples mismatches=$directionMismatchSamples"
  }
  if ($expectedOutputSign -ne 0 -and $maxOutput -gt 1e-9 -and $directionTotal -le 0) {
    Add-Failure "Axis direction was not proven: $axis has output but no raw/output direction samples"
  }
}

$leftGripRange = Number-Value $summary.gripperTargetRanges.leftMm
$rightGripRange = Number-Value $summary.gripperTargetRanges.rightMm
if ($leftGripRange -lt 0.5 -or $rightGripRange -lt 0.5) {
  Add-Failure "Gripper target range is too small: left=$leftGripRange mm, right=$rightGripRange mm"
}
if (!([bool]$gates.forceOutputAllEnabled)) {
  Add-Failure "forceOutputAllEnabled is not true"
}
if (!([bool]$gates.gravityCompensationAllEnabled)) {
  Add-Failure "gravityCompensationAllEnabled is not true"
}
if (!([bool]$gates.logicalConnectedAllEnabled)) {
  Add-Failure "logicalConnectedAllEnabled is not true; connect both logical Omega.7 hands before running strict observation"
}
$gripperCommandSpeed = [int](Number-Value $gates.gripperCommandSpeed)
if (!([bool]$gates.gripperCommandSpeedOk) -or $gripperCommandSpeed -lt 200) {
  Add-Failure "gripperCommandSpeed is $gripperCommandSpeed; expected >=200 to match Omega.7 gripper teleop speed"
}
if (!([bool]$gates.zeroStopObserved)) {
  Add-Failure "No final zero-delta native teleop stop was observed"
}

$acceptancePass = $failures.Count -eq 0
$summaryObject = [pscustomobject]@{
  report = (Resolve-Path -LiteralPath $ReportPath).Path
  acceptancePass = $acceptancePass
  sampleCount = [int]$observation.sampleCount
  maxActionHistoryCount = [int]$observation.maxActionHistoryCount
  observedSourceTargetPairs = As-Array $summary.observedSourceTargetPairs
  observedMovingSourceTargetPairs = As-Array $summary.observedMovingSourceTargetPairs
  observedTargetAxes = As-Array $summary.observedTargetAxes
  directionPolicy = $directionPolicy
  axisDiagnostics = $summary.axisDiagnostics
  axisRows = $axisRows
  axisFailureCauses = $axisFailureCauses
  gripperTargetRanges = $summary.gripperTargetRanges
  forceOutputAllEnabled = [bool]$gates.forceOutputAllEnabled
  gravityCompensationAllEnabled = [bool]$gates.gravityCompensationAllEnabled
  logicalConnectedAllEnabled = [bool]$gates.logicalConnectedAllEnabled
  gripperCommandSpeed = $gripperCommandSpeed
  gripperCommandSpeedOk = [bool]$gates.gripperCommandSpeedOk
  zeroStopObserved = [bool]$gates.zeroStopObserved
  failures = @($failures)
}
$summaryObject

"Axis diagnostics:"
foreach ($row in $axisRows) {
  "{0} raw={3:g6} filtered={4:g6} reqPulse={5:g6} emitPulse={6:g6} output={7:g6} outputDuty={8:g4} pulseEfficiency={9:g4} directionMatch={11}/{12} expectedSign={13} samples={1} nonzero={2} cause={10}" -f `
    $row.axis,
    $row.samples,
    $row.nonZero,
    $row.raw,
    $row.filtered,
    $row.requestedPulse,
    $row.emittedPulse,
    $row.output,
    $row.outputDuty,
    $row.pulseEfficiency,
    $row.cause,
    $row.directionMatchSamples,
    ($row.directionMatchSamples + $row.directionMismatchSamples),
    $row.expectedOutputSign
}

if (!$acceptancePass) {
  [Console]::Error.WriteLine("HAL-native teleop acceptance report failed: $($failures -join '; ')")
  exit 2
}
