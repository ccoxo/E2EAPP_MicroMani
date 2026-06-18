# AppStation DDS HAL Path

`HalServer.exe` now owns both DDS paths:

- backend control/telemetry: Python backend uses `DdsHalClient` to subscribe HAL telemetry and publish `CommandRequest`.
- teleop data path: HAL reads Omega.7 master-hand frames, performs mapping inside HAL, and sends follower hardware targets through DDS.

HTTP remains in `HalServer.exe` only for the local `/health` diagnostic probe.
The Python backend real-HAL path defaults to DDS and rejects
`APPSTATION_HAL_TRANSPORT=http`.

`HalFastDdsBridge` was removed. There is no shared-memory or loaned-sample path;
the code uses normal Fast-DDS `write` and `take` calls.

## Backend Control Topics

- `AppStation.Hal.Health`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.MotionState`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.OmegaState`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.NativeTeleopStatus`: `JsonEnvelope`, published by `HalDdsControlServer`.
- `AppStation.Hal.CommandRequest`: `HalCommandRequest`, published by Python backend.
- `AppStation.Hal.CommandReply`: `HalCommandReply`, published by `HalDdsControlServer` with the matching `request_id`.

## HAL Teleop Topics

- `AppStation.Teleop.LeaderState`: JSON envelope containing the current Omega.7 master-hand state.
- `AppStation.Teleop.HardwareTarget`: fixed-size `TeleopHardwareTarget` sample consumed by the follower execution layer.

The Python backend may observe these teleop topics for UI, logs, or diagnostics,
but it does not produce `HardwareTarget` for the main teleoperation chain.

## Components

- `HalDdsControlServer`: backend-facing DDS telemetry publisher and command request/reply server.
- `TeleopLeaderPublisher`: publishes `LeaderState` when native teleop reads a master-hand frame.
- `TeleopMappingNode`: subscribes `LeaderState`, calls `NativeTeleopController::processLeaderState`, and publishes `HardwareTarget`.
- `TeleopFollowerTargetSubscriber`: subscribes `HardwareTarget` and calls `TeleopHardwareTargetExecutor`.
- `TeleopHardwareTargetExecutor`: applies the target through `LTDMCDriver::updateTeleopTargetUi`.

## Environment

- `APPSTATION_HAL_DDS_ENABLED`: defaults to `1` in the launch scripts.
- `APPSTATION_HAL_TRANSPORT`: backend real-HAL transport; use `dds`.
- `APPSTATION_DDS_DOMAIN_ID`: DDS domain, default `42`.
- `APPSTATION_DDS_LAN_DISCOVERY`: default `0`, binds DDS discovery to localhost unless set to `1`.
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
