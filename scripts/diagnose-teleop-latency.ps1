# 阅读导航 08｜启动、部署与工具
# 职责：已停用的旧 HTTP 延迟诊断入口；当前执行会抛出迁移说明。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

param(
  [int]$BackendPort = 18082,
  [int]$HalPort = 8091,
  [int]$ObserveSeconds = 0,
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

throw "Direct HAL teleop latency diagnostics over HTTP were removed. HAL HTTP only supports /health; use backend DDS telemetry or add a backend diagnostics endpoint."
