param(
  [string]$LeftPort = "COM15",
  [string]$RightPort = "COM14",
  [int]$Baudrate = 1000000,
  [double]$DurationSec = 10.0,
  [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
if ($DurationSec -lt 10.0) {
  throw "DurationSec must be at least 10 seconds"
}
if ($LeftPort.Trim().ToUpperInvariant() -eq $RightPort.Trim().ToUpperInvariant()) {
  throw "LeftPort and RightPort must be different"
}
if (-not $OutputDirectory) {
  $repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
  $OutputDirectory = Join-Path $repo "backend\runtime\force-captures"
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

function New-ReadOnlySerialPort {
  param([string]$Name)
  $serial = [System.IO.Ports.SerialPort]::new(
    $Name,
    $Baudrate,
    [System.IO.Ports.Parity]::None,
    8,
    [System.IO.Ports.StopBits]::One
  )
  $serial.Handshake = [System.IO.Ports.Handshake]::None
  $serial.DtrEnable = $false
  $serial.RtsEnable = $false
  $serial.ReadTimeout = 20
  $serial.Open()
  return $serial
}

function Get-ModbusCrc16 {
  param(
    [byte[]]$Bytes,
    [int]$Offset,
    [int]$Length
  )
  [int]$crc = 0xFFFF
  for ($index = 0; $index -lt $Length; $index++) {
    $crc = $crc -bxor [int]$Bytes[$Offset + $index]
    for ($bit = 0; $bit -lt 8; $bit++) {
      if (($crc -band 1) -ne 0) {
        $crc = (($crc -shr 1) -bxor 0xA001)
      } else {
        $crc = $crc -shr 1
      }
    }
  }
  return $crc -band 0xFFFF
}

function Test-HkvlCandidateFrames {
  param([byte[]]$Bytes)
  # Candidate protocol: 28 bytes, header 53 54, six little-endian float32,
  # Fx,Fy,Fz,Mx,My,Mz, then CRC-16/Modbus over the first 26 bytes.
  $validFrames = 0
  $crcErrors = 0
  $nonFiniteFrames = 0
  $resyncBytes = 0
  $cursor = 0
  while ($cursor + 1 -lt $Bytes.Length) {
    if ($Bytes[$cursor] -ne 0x53 -or $Bytes[$cursor + 1] -ne 0x54) {
      $cursor++
      $resyncBytes++
      continue
    }
    if ($cursor + 28 -gt $Bytes.Length) {
      break
    }
    $expected = Get-ModbusCrc16 -Bytes $Bytes -Offset $cursor -Length 26
    $actual = [int]$Bytes[$cursor + 26] -bor ([int]$Bytes[$cursor + 27] -shl 8)
    if ($actual -ne $expected) {
      $crcErrors++
      $cursor++
      $resyncBytes++
      continue
    }
    $finite = $true
    for ($channel = 0; $channel -lt 6; $channel++) {
      $value = [BitConverter]::ToSingle($Bytes, $cursor + 2 + 4 * $channel)
      if ([float]::IsNaN($value) -or [float]::IsInfinity($value)) {
        $finite = $false
        break
      }
    }
    if (-not $finite) {
      $nonFiniteFrames++
      $cursor += 28
      continue
    }
    $validFrames++
    $cursor += 28
  }
  return [ordered]@{
    bytes = $Bytes.Length
    validFrames = $validFrames
    crcErrors = $crcErrors
    nonFiniteFrames = $nonFiniteFrames
    resyncBytes = $resyncBytes
    frameHz = [Math]::Round($validFrames / $DurationSec, 3)
  }
}

$ports = @{}
$buffers = @{
  left = [System.Collections.Generic.List[byte]]::new()
  right = [System.Collections.Generic.List[byte]]::new()
}
$timestamps = [System.Collections.Generic.List[string]]::new()
$timestamps.Add("side,monotonic_ms,unix_ms,byte_offset,byte_count")
$timer = [System.Diagnostics.Stopwatch]::StartNew()

try {
  $ports.left = New-ReadOnlySerialPort -Name $LeftPort
  $ports.right = New-ReadOnlySerialPort -Name $RightPort
  while ($timer.Elapsed.TotalSeconds -lt $DurationSec) {
    foreach ($side in @("left", "right")) {
      $available = $ports[$side].BytesToRead
      if ($available -le 0) {
        continue
      }
      $chunk = [byte[]]::new($available)
      $count = $ports[$side].Read($chunk, 0, $chunk.Length)
      if ($count -le 0) {
        continue
      }
      $offset = $buffers[$side].Count
      if ($count -lt $chunk.Length) {
        $chunk = $chunk[0..($count - 1)]
      }
      $buffers[$side].AddRange($chunk)
      $timestamps.Add(
        "$side,$([Math]::Round($timer.Elapsed.TotalMilliseconds, 3)),$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()),$offset,$count"
      )
    }
    Start-Sleep -Milliseconds 1
  }
} finally {
  foreach ($serial in $ports.Values) {
    if ($null -ne $serial) {
      if ($serial.IsOpen) { $serial.Close() }
      $serial.Dispose()
    }
  }
  $timer.Stop()
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$leftBytes = $buffers.left.ToArray()
$rightBytes = $buffers.right.ToArray()
[System.IO.File]::WriteAllBytes((Join-Path $OutputDirectory "hkvl-$stamp-left.bin"), $leftBytes)
[System.IO.File]::WriteAllBytes((Join-Path $OutputDirectory "hkvl-$stamp-right.bin"), $rightBytes)
[System.IO.File]::WriteAllLines(
  (Join-Path $OutputDirectory "hkvl-$stamp-timestamps.csv"),
  $timestamps,
  [System.Text.UTF8Encoding]::new($false)
)

$summary = [ordered]@{
  capturedAt = [DateTimeOffset]::Now.ToString("o")
  durationSec = $timer.Elapsed.TotalSeconds
  baudrate = $Baudrate
  protocolCandidate = "hkvl_active_v1"
  frameHeader = "53 54"
  left = [ordered]@{
    port = $LeftPort
    validation = Test-HkvlCandidateFrames -Bytes $leftBytes
  }
  right = [ordered]@{
    port = $RightPort
    validation = Test-HkvlCandidateFrames -Bytes $rightBytes
  }
}
$summaryPath = Join-Path $OutputDirectory "hkvl-$stamp-summary.json"
[System.IO.File]::WriteAllText(
  $summaryPath,
  ($summary | ConvertTo-Json -Depth 5),
  [System.Text.UTF8Encoding]::new($false)
)
$summary
