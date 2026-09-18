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

throw "Direct HAL native teleop HTTP acceptance was removed. HAL HTTP only supports /health; validate native teleop through the backend/UI DDS path."
