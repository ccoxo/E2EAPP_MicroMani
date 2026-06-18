param(
  [int]$BackendPort = 18082,
  [int]$HalPort = 8091,
  [int]$ObserveSeconds = 0,
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

throw "Direct HAL teleop latency diagnostics over HTTP were removed. HAL HTTP only supports /health; use backend DDS telemetry or add a backend diagnostics endpoint."
