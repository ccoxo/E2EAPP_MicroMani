# Contract: LeRobot v3 Dataset Metadata

## Dataset Header

```json
{
  "codebase_version": "v3.0",
  "robot_type": "dual_arm_micro_assembly",
  "fps": 30
}
```

## Standard Features

### observation.state

- dtype: `float32`
- shape: `[14]`
- names:
  - `left_x_um`
  - `left_y_um`
  - `left_z_um`
  - `left_roll_mdeg`
  - `left_pitch_mdeg`
  - `left_yaw_mdeg`
  - `left_gripper_gap_mm`
  - `right_x_um`
  - `right_y_um`
  - `right_z_um`
  - `right_roll_mdeg`
  - `right_pitch_mdeg`
  - `right_yaw_mdeg`
  - `right_gripper_gap_mm`

### action

- dtype: `float32`
- shape: `[14]`
- names:
  - `left_dx_um`
  - `left_dy_um`
  - `left_dz_um`
  - `left_droll_mdeg`
  - `left_dpitch_mdeg`
  - `left_dyaw_mdeg`
  - `left_gripper_target_mm`
  - `right_dx_um`
  - `right_dy_um`
  - `right_dz_um`
  - `right_droll_mdeg`
  - `right_dpitch_mdeg`
  - `right_dyaw_mdeg`
  - `right_gripper_target_mm`

### observation.pulses

- dtype: `float32`
- shape: `[12]`
- names:
  - `left_x_pulse`
  - `left_y_pulse`
  - `left_z_pulse`
  - `left_roll_pulse`
  - `left_pitch_pulse`
  - `left_yaw_pulse`
  - `right_x_pulse`
  - `right_y_pulse`
  - `right_z_pulse`
  - `right_roll_pulse`
  - `right_pitch_pulse`
  - `right_yaw_pulse`

### observation.force_left / observation.force_right

- dtype: `float32`
- shape: `[6]`
- names: `fx`, `fy`, `fz`, `mx`, `my`, `mz`

### observation.images.global / wrist_left / wrist_right

- dtype: `video`
- shape: `[480, 640, 3]`
- names: `height`, `width`, `channels`

## AppStation Extensions

AppStation may persist additional review metadata outside standard features:

- force windows and force sample offsets
- HAL health
- motion enabled/estop
- Omega.7 master hand state
- per-source timeout/stale status
- per-source skew/jitter/late/drop metrics
- warnings and quality status

Extensions must not change standard feature names, dtype, shape, or order.
