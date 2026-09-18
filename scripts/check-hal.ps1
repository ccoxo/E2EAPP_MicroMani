# 阅读导航 08｜启动、部署与工具
# 职责：请求 HAL /health 并报告端口所属进程和驱动健康状态。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

param(
  [int]$Port = 8091
)

$ErrorActionPreference = "Stop"

try {
  $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
} catch {
  Write-Error "HAL health check failed: $($_.Exception.Message)"
  exit 1
}

$processId = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -First 1

$result = [pscustomobject]@{
  pid = $processId
  url = "http://127.0.0.1:$Port"
  ltdmc_ok = $health.ltdmc_ok
  omega7_ok = $health.omega7_ok
  version = $health.version
  uptime_s = $health.uptime_s
}

$result

if (!$health.ltdmc_ok) {
  exit 2
}
