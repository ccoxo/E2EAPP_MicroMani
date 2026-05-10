$ErrorActionPreference = "Stop"

foreach ($port in @(5173, 5174, 18080, 18082, 8091)) {
  $pidOnPort = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -First 1
  if ($pidOnPort) {
    Stop-Process -Id $pidOnPort -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped process $pidOnPort on port $port"
  }
}
