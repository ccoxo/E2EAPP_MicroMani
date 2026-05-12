# Contract: LeRobot v3 Dataset Metadata

## Scope

LeRobot 的 `so_follower` 示例是单臂数据，只用于说明 Dataset v3 metadata/features 的形状。本项目只保留双臂微装配采集必要的硬件语义字段：双臂轴状态、双臂动作、12 轴脉冲、双路力传感器和三路相机。

`total_episodes`、`total_frames`、`total_tasks`、`chunks_size`、文件大小、`splits`、`data_path` 和 `video_path` 等全局统计或路径字段由 LeRobot 数据集库生成和维护，不在 AppStation 手写硬件 feature 契约中重复定义。

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
- info:
  - `video.height`: `480`
  - `video.width`: `640`
  - `video.codec`: `av1`
  - `video.pix_fmt`: `yuv420p`
  - `video.is_depth_map`: `false`
  - `video.fps`: `30`
  - `video.channels`: `3`
  - `has_audio`: `false`

## LeRobot Index Fields

LeRobot may persist these frame bookkeeping fields as standard dataset indices. They are not robot hardware semantic dimensions:

- `timestamp`: dtype `float32`, shape `[1]`, names `null`
- `frame_index`: dtype `int64`, shape `[1]`, names `null`
- `episode_index`: dtype `int64`, shape `[1]`, names `null`
- `index`: dtype `int64`, shape `[1]`, names `null`
- `task_index`: dtype `int64`, shape `[1]`, names `null`
