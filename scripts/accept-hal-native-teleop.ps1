# 阅读导航 08｜启动、部署与工具
# 职责：已停用的旧 HTTP 遥操作验收入口；执行即提示改用后端/UI 的 DDS 路径。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

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
