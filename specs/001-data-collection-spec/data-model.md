# Data Model: 数据收集完善

## RecordingSession

**Purpose**: 一次数据采集活动。

**Fields**:

- `session_id`: 稳定会话标识
- `dataset_id`: 本地数据集标识
- `dataset_name`: 操作员输入的显示名称
- `task`: 任务说明
- `phase`: `idle`、`recording`、`resetting`、`finished`
- `episode_index`: 当前 episode 序号
- `record_fps`: 录制主轴帧率，默认 30
- `force_sample_hz`: 力觉采样率
- `force_window_samples`: 每帧力觉窗口样本数
- `native_lerobot_available`: 是否可使用 LeRobot 原生数据集

**Validation Rules**:

- 同一时间只能有一个 active session。
- dataset 根路径必须在配置根目录下。
- `record_fps` 必须在 1-60 Hz 范围内。

## CaptureTick

**Purpose**: 30 Hz monotonic 主轴上的单次采集周期。

**Fields**:

- `frame_index`: episode 内帧序号
- `target_monotonic_s`: tick 目标时间
- `captured_monotonic_s`: 汇聚完成时间
- `source_started_monotonic_s`: 各来源任务在同一 tick 内启动的时间
- `source_finished_monotonic_s`: 各来源完成或超时的时间
- `timestamp_wall_s`: 持久化 wall-clock timestamp
- `source_results`: 每个硬件源的采样结果和采样时间
- `skew_by_source_ms`: 每个来源相对 tick 的偏差
- `timeout_by_source`: 每个来源是否触发 per-source timeout
- `stale_by_source`: 每个来源是否使用过期或缓存数据
- `late_flags`: 每个来源是否 late
- `drop_flags`: 每个来源是否 drop

**Validation Rules**:

- 每个 tick 必须有稳定 frame index。
- HAL、力觉、夹爪、Omega 和相机来源必须在同一 tick 内并发启动；仅相机内部并行不满足该模型。
- 每个来源必须受 per-source timeout 约束，单来源 timeout 不得阻塞其他来源写入当前帧。
- HAL skew >20 ms 产生 warning。
- 相机 skew >33.3 ms 或无可用帧计为 late/drop。
- 同一关键源连续 3 帧无法对齐产生 warning，连续 10 帧或整段不可用标记 invalid。

## DatasetFrame

**Purpose**: 写入 LeRobot 或 fallback 的标准训练帧。

**Standard Fields**:

- `observation.state`: `float32[14]`
- `action`: `float32[14]`
- `observation.pulses`: `float32[12]`
- `observation.force_left`: `float32[6]`
- `observation.force_right`: `float32[6]`
- `observation.images.global`: video frame `[480, 640, 3]`
- `observation.images.wrist_left`: video frame `[480, 640, 3]`
- `observation.images.wrist_right`: video frame `[480, 640, 3]`
- `task`: task text

**State Names**:

`left_x_um`, `left_y_um`, `left_z_um`, `left_roll_mdeg`, `left_pitch_mdeg`, `left_yaw_mdeg`, `left_gripper_gap_mm`, `right_x_um`, `right_y_um`, `right_z_um`, `right_roll_mdeg`, `right_pitch_mdeg`, `right_yaw_mdeg`, `right_gripper_gap_mm`

**Action Names**:

`left_dx_um`, `left_dy_um`, `left_dz_um`, `left_droll_mdeg`, `left_dpitch_mdeg`, `left_dyaw_mdeg`, `left_gripper_target_mm`, `right_dx_um`, `right_dy_um`, `right_dz_um`, `right_droll_mdeg`, `right_dpitch_mdeg`, `right_dyaw_mdeg`, `right_gripper_target_mm`

**Validation Rules**:

- State/action shape 必须为 14。
- Pulses shape 必须为 12，且只包含 LTDMC 运动轴。
- 力觉字段必须为 N/Nm 语义。
- 标准 features 不包含 `observation.gripper` 作为训练主字段。

## HardwareSampleSet

**Purpose**: 一个 tick 上各硬件来源的原始采样集合。

**Fields**:

- `hal_motion`: positions、pulses、enabled、estop、sample_time
- `hal_health`: LTDMC/Omega.7 可用性、版本、消息
- `omega_hands`: 左右主手连接、openId、deviceId、pose、按钮、gripperGap、read status
- `force`: left/right scalar force、left/right windows、window offsets
- `cameras`: global/wrist_left/wrist_right image or drop reason
- `gripper`: slave actual gap and target gap
- `teleop_action`: latest 14D action target
- `timeout_status`: `hal`、`force`、`camera`、`gripper`、`omega` 的 timeout/stale/drop 结果

**Validation Rules**:

- 所有来源必须携带采样时间或可推导的 tick 偏移。
- 单个来源失败不得阻塞其他来源写入。
- 来源失败必须进入 quality report。
- 来源 timeout 必须和普通采样失败区分记录，便于判断是硬件慢、不可用还是缓存降级。

## EpisodeQualityReport

**Purpose**: 保存 episode 后提供给审核人员的质量摘要。

**Fields**:

- `frames`: 保存帧数
- `duration_s`: episode 时长
- `late_frames`: late frame 总数
- `drop_counts`: 按相机和关键来源统计的 drop 次数
- `timeout_counts`: 按 HAL、相机、力觉、夹爪和 Omega 来源统计的 timeout 次数
- `max_force_left`, `max_force_right`: 最大力觉幅值
- `max_skew_ms`: 按来源统计的最大偏差
- `avg_skew_ms`: 按来源统计的平均偏差
- `jitter_ms`: 按来源统计的 tick 间抖动
- `warnings`: warning 列表
- `status`: `ok`、`warning`、`invalid`

**Validation Rules**:

- 质量报告必须包含 max skew、avg skew、jitter、late frames、drop counts。
- 质量报告必须能区分 late、drop、timeout 和 stale，且按来源定位。
- estop_active 为 true 的 episode 不得默认为 `ok`。
- HAL/Omega/相机/力觉/夹爪不可用必须有可定位 warning。

## DatasetMetadata

**Purpose**: 数据集级 metadata。

**Fields**:

- `codebase_version`: `v3.0`
- `robot_type`: `dual_arm_micro_assembly`
- `fps`: 默认 30
- `features`: 标准 LeRobot features
- `appstation_info`: AppStation 扩展信息，包括采集配置、质量扩展、硬件来源、对齐策略

**Validation Rules**:

- 标准 `features` 必须与 contract 完全一致。
- AppStation 扩展不得改变标准 features shape、dtype、names。
