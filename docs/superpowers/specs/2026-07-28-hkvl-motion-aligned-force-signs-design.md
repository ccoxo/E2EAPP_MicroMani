# HKVL-36A Motion-Aligned Six-Axis Force Sign Design

## Goal

Express every HKVL-36A force and moment channel in the positive motion direction of the motion card that carries that sensor.

This design supersedes the “no force-axis sign inversion” statement in the earlier hardware-side port-binding design. The hardware-side port binding remains unchanged.

## Confirmed binding and signs

| Force side | Serial port | Motion card | Axis sign `[Fx,Fy,Fz,Mx,My,Mz]` |
|---|---|---:|---|
| hardware left | COM15 | Card 1 | `[-1,+1,-1,+1,-1,-1]` |
| hardware right | COM14 | Card 0 | `[-1,-1,-1,+1,+1,+1]` |

The signs are derived from the corresponding effective motion conversion:

```text
motion.kinematics.leftSignedPulsePerUnit
motion.kinematics.rightSignedPulsePerUnit
```

For each channel, a negative signed-pulse value produces `-1` and a positive value produces `+1`. Zero and non-finite values are invalid because they do not define a direction.

The channel order remains:

```text
Fx, Fy, Fz, Mx, My, Mz
```

There is no axis exchange. `Fx/Fy/Fz` continue to correspond to slide `X/Y/Z`, and `Mx/My/Mz` correspond to `Roll/Pitch/Yaw`.

## Transform boundary

The serial parser and `HkvlForceDriver` remain in the sensor manufacturer's native coordinate system. After native Tare and native low-pass filtering, `ForceControlRuntime::acceptSample` multiplies both the tared and filtered arrays by the configured per-side signs:

```text
motionAligned[axis] = sensorValue[axis] * axisSign[side][axis]
```

HAL then uses the motion-aligned values for:

- `left` and `right` filtered force state;
- `rawLeft` and `rawRight`, whose existing contract means tared but unfiltered values;
- safety evaluation and signed trip diagnostics;
- X/Z compliance input;
- DDS/backend telemetry and dataset force samples.

The manufacturer-native diagnostic values remain available as:

- top-level `sensorRawLeft` and `sensorRawRight`;
- per-side `sensorTareBias`.

Per-side `tareBias` is motion-aligned so all non-`sensor*` force-state fields share one coordinate contract. Each side also publishes `axisSign`.

## Tare, filtering, safety, and compliance

Tare continues to be calculated and stored in sensor-native coordinates inside the driver. Multiplication by a constant `+1/-1` after subtraction is equivalent to applying the same sign before subtraction.

The low-pass filter also remains in the driver. A constant sign multiplication commutes with this linear filter, so cutoff and amplitude do not change.

Safety consumes the motion-aligned, tared, unfiltered array. Existing thresholds use absolute magnitudes, so warning, stop, `dangerIndex`, three-frame confirmation, and watchdog behavior remain unchanged. Trip `value` now reports the signed value in motion coordinates.

Compliance continues to use the identity X/Z matrix:

```json
"matrix": [1, 0, 0, 1]
```

The force sign must not be multiplied again by teleoperation `DirectionSign`, the compliance matrix, the frontend, or dataset code. Compliance remains disabled and both `mappingConfirmed` flags remain false until physical direction verification.

## Configuration flow

The backend derives `leftAxisSign` and `rightAxisSign` from the motion kinematics whenever it builds the flat HAL force payload. `scripts/start-hal.ps1` performs the same derivation for direct HAL startup and contains the confirmed signs as safe defaults.

HAL parses and validates both arrays. Every element must be exactly `-1` or `+1`.

Changing the signed motion conversion therefore changes the corresponding force coordinate contract through the existing guarded `force.configure` path. The existing apply guard still requires teleoperation stopped, all axes stopped, and servos disabled.

## Non-goals

- Do not change COM15/Card 1 and COM14/Card 0 binding.
- Do not exchange axes.
- Do not change official 30 N / 1 Nm stop limits or provisional warning values.
- Do not Tare, acknowledge safety, enable servos, enable compliance, or confirm mappings automatically.
- Do not modify the raw capture script's byte/frame output; it remains a manufacturer-native protocol diagnostic.

## Verification

Tests must prove:

- backend payload signs match the effective signed-pulse arrays and reject a zero direction;
- PowerShell startup defaults and runtime derivation use the same rule;
- HAL parses and validates both six-element sign arrays;
- HAL standard tared/filtered arrays are motion-aligned for all six channels;
- native raw diagnostics and native Tare bias remain identifiable;
- safety magnitude is unchanged while signed trip values use motion coordinates;
- compliance receives motion-aligned Fx/Fz without another inversion;
- dataset metadata records the signs and both aligned and native Tare bias semantics.
