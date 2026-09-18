# HKVL-36A Hardware-Side Port Binding Design

## Goal

Bind each HKVL-36A sensor to the motion arm that physically carries it, while preserving the existing distinction between hardware side and operator-facing side.

## Confirmed physical mapping

| Force port | Motion card | Hardware side | Operator-facing side |
|---|---:|---|---|
| COM14 | Card 0 | right | left |
| COM15 | Card 1 | left | right |

`force.left` and `force.right` are hardware-side indices throughout HAL, DDS, backend telemetry, datasets, Tare, and compliance. Therefore the serial configuration must be:

```json
{
  "leftPort": "COM15",
  "rightPort": "COM14"
}
```

## Axis semantics

The sensor axes already match the slide-stage semantics:

```text
Fx -> X
Fy -> Y
Fz -> Z
```

No force-axis swap or sign inversion is added. Both X/Z compliance matrices remain the identity matrix:

```json
"matrix": [1, 0, 0, 1]
```

Motion-card pulse signs remain solely owned by the existing motion `DirectionSign`/signed pulse mapping. Compliance must not multiply sensor values by those signs a second time.

## Data flow

The HAL driver assigns `side=0` to `leftPort` and `side=1` to `rightPort`. These indices flow unchanged to:

- HAL `left`/`right` force arrays and health status;
- safety trip side and Tare side;
- DDS/backend `forceLeft` and `forceRight`;
- dataset `observation.force_left` and `observation.force_right`;
- compliance correction for hardware left/Card 1 and hardware right/Card 0.

The frontend alone converts between hardware side and operator-facing side. Operator-left cards consume hardware-right force data; operator-right cards consume hardware-left force data.

## Scope

Update all port defaults, runtime configuration, tests, dataset metadata expectations, frontend fixtures, and the read-only capture script's side labels. Do not:

- change motion card numbers or pulse direction signs;
- change the six-axis force order or signs;
- enable compliance or mark either mapping confirmed;
- automatically Tare or acknowledge the safety latch.

## Verification

Tests must prove:

- backend and frontend defaults bind left to COM15 and right to COM14;
- HAL's default serial configuration uses the same binding;
- the startup and capture scripts use the same hardware-side labels;
- dataset metadata preserves the corrected serial mapping;
- the operator-left UI card shows COM14 while operator-right shows COM15;
- compliance matrices remain identity, disabled, and unconfirmed.

Applying the configuration online must pass the existing HAL guard requiring stopped teleoperation, stopped axes, and disabled servos. A successful reconfiguration intentionally resets Tare and leaves the safety latch set.
