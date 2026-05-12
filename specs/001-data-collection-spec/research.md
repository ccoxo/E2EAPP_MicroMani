# Research: 数据收集完善

## Decision: 优先复用 LeRobotDataset 库函数

**Rationale**: 标准训练数据必须和 LeRobot v3.0 metadata/features 保持一致。使用 `LeRobotDataset.create()`、`LeRobotDataset.resume()`、`add_frame()`、`save_episode()`、`finalize()` 等库函数可以减少本地手写 parquet/video/metadata 格式的风险，并与后续训练工具保持兼容。

**Alternatives considered**:

- 手写 LeRobot v3 文件布局：会重复库内格式细节，容易在 metadata、视频编码或 episode 索引上漂移。
- 继续只写 JSONL/JPEG fallback：能复核但不满足 v3.0 标准训练数据契约，只能作为缺少 LeRobot 能力时的兼容路径。

## Decision: 不导入 Linux-only LeRobot 采集脚本

**Rationale**: 宪章明确 Windows 后端不能整体 import `lerobot.scripts.lerobot_record` 等 Linux-only 采集脚本。当前项目采集源来自 HAL、OpenCV、NI-DAQmx 和夹爪服务，应该只复用数据集库函数，而不是复用 LeRobot 机器人采集入口。

**Alternatives considered**:

- 复用 LeRobot record CLI：会和现有 HAL/前端录制状态机冲突，也不适合 Windows-only 硬件边界。
- 复制大段 LeRobot 内部逻辑：增加维护成本；只有库函数无法覆盖的最小兼容逻辑才允许本地实现。

## Decision: 使用 asyncio 按录制 tick 并发采集不同硬件

**Rationale**: HAL、三路相机、力觉窗口、夹爪和 Omega.7 状态具有不同采样耗时和阻塞模型。按 30 Hz monotonic tick 创建采集上下文，在同一 tick 内先创建所有来源任务，再用 `asyncio.gather()`、`asyncio.to_thread()` 和 per-source timeout 并发读取，可以降低串行等待导致的 skew。相机当前帧不可用时直接使用上一帧有效缓存，仅三路相机内部并行不能证明 HAL/力觉/相机整体满足同一 tick 对齐。

**Alternatives considered**:

- 串行采集所有硬件：简单，但会放大左右夹爪、相机和力觉窗口偏差，不满足时间对齐阈值。
- 为每个硬件新建独立进程：超出当前需求，增加跨进程同步和部署复杂度。
- 只让三路相机内部并行：仍然保留 HAL、力觉窗口和相机之间的串行等待，无法证明 30 Hz 录制和 HAL/相机 skew 阈值。

## Decision: 标准 features 只保留训练必要字段

**Rationale**: 用户给定的标准 features 只需要双臂状态、动作、12 轴脉冲、双路力觉、三路相机和 LeRobot 索引字段。标准训练字段保持 LeRobot v3.0 兼容，不加入额外运行状态字段。

**Alternatives considered**:

- 把额外运行状态塞进 LeRobot features：会改变训练主契约，增加下游读取复杂度。
- 继续保留独立 `observation.gripper`：会与 14 维 state/action 标准结构重复。

## Decision: 14 维 state/action 替代独立 observation.gripper 作为标准主字段

**Rationale**: LeRobot v3.0 标准要求 `observation.state` 和 `action` 各 14 维。夹爪实际开口进入 state，夹爪目标进入 action；`observation.pulses` 保持 12 维运动轴脉冲。旧 `observation.gripper` 只能作为兼容或调试字段。

**Alternatives considered**:

- 保持 12 维 state/action + 独立 gripper：与用户提供的标准 metadata 不一致。
- 把夹爪脉冲加入 pulses：夹爪不是 LTDMC 12 运动轴脉冲，加入会破坏 pulses 语义。
