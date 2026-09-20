# AppStation DDS HAL Path

`HalServer.exe` now owns both DDS paths:

- backend control/telemetry: Python backend uses `DdsHalClient` to subscribe HAL telemetry and publish `CommandRequest`.
- teleop data path: HAL reads Omega.7 master-hand frames, performs mapping inside HAL, and sends follower hardware targets through DDS.

HTTP remains in `HalServer.exe` only for the local `/health` diagnostic probe.
The Python backend real-HAL path defaults to DDS and rejects
`APPSTATION_HAL_TRANSPORT=http`.

`HalFastDdsBridge` 已移除。HAL 高频遥操作链路使用固定布局类型、Data Sharing 和 loan；
后端控制与 JSON 遥测经 SHM transport 传输，保留序列化。所有 participant 的发现和数据都限于本机共享内存。
范围、兼容性与验证见 [零拷贝传输说明](dds-zero-copy.md)。

## Backend Control Topics

- `AppStation.Hal.Health`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.MotionState`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.OmegaState`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.NativeTeleopStatus`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.ForceState`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.EmergencyStop`: `HalCommandRequest`，由后端发布，HAL 独立急停线程处理。
- `AppStation.Hal.CommandRequest`: `HalCommandRequest`, published by Python backend.
- `AppStation.Hal.CommandReply`: `HalCommandReply`, published by `HalDdsControlServer` with the matching `request_id`.

## HAL Teleop Topics

- `AppStation.Teleop.LeaderState.V2`: 固定布局的主手控制状态，替代旧 JSON LeaderState。
- `AppStation.Teleop.HardwareTarget`: fixed-size `TeleopHardwareTarget` sample consumed by the follower execution layer.

Python 后端继续通过 `AppStation.Hal.OmegaState` 获取主手遥测；旧 LeaderState 常量仅是历史契约，不能解码 V2。
后端不生产主遥操作链路的 `HardwareTarget`。

## Components

- `HalDdsControlServer`: backend-facing DDS telemetry publisher and command request/reply server.
- `TeleopLeaderPublisher`: publishes `LeaderState` when native teleop reads a master-hand frame.
- `TeleopMappingNode`: subscribes `LeaderState`, calls `NativeTeleopController::processLeaderState`, and publishes `HardwareTarget`.
- `TeleopFollowerTargetSubscriber`: subscribes `HardwareTarget` and calls `TeleopHardwareTargetExecutor`.
- `TeleopHardwareTargetExecutor`: 经共享 `MotionExecutor` 仲裁后调用驱动执行目标。

## Environment

- `APPSTATION_HAL_DDS_ENABLED`: defaults to `1` in the launch scripts.
- `APPSTATION_HAL_TRANSPORT`: backend real-HAL transport; use `dds`.
- `APPSTATION_DDS_DOMAIN_ID`: DDS domain, default `42`.
- DDS 仅使用本机 SHM；已移除 `APPSTATION_DDS_LAN_DISCOVERY` 与启动脚本的 `-LanDiscovery` 参数。
- `APPSTATION_TELEOP_EXECUTOR`: default `dds_follower`; any other value falls back to the legacy in-process execution path.

## Timing Model

`HalDdsControlServer` runs a small DDS control loop for backend command/reply
and telemetry publication. The teleop data path is still driven by native teleop:
master-hand sampling happens only while native teleop is running; mapping runs
when a `LeaderState` DDS sample arrives; follower execution runs when a
`HardwareTarget` DDS sample arrives.

## Build

```powershell
backend\native\build_fastdds_transport.cmd
hal\build_hal.cmd
```

Both builds link against the Fast-DDS/Fast-CDR libraries under `F:\opt\ros\jazzy`.

共享内存版本的 HAL 与后端 DLL 必须同步部署并重启；旧 UDP-only participant 无法发现新 SHM-only participant。
