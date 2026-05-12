# Quickstart: 数据收集完善

## 1. 准备环境

```powershell
cd F:\code\cc_data_collection
```

后端开发环境使用 `backend/pyproject.toml`。LeRobot 原生数据集能力是可选依赖，但实现必须优先复用 LeRobot 数据集库函数；缺少依赖时才走 fallback 复核路径。

## 2. 后端验证

```powershell
cd backend
pytest
ruff check .
mypy backend
```

重点测试：

- LeRobot v3 metadata features 与 spec 一致。
- `observation.state` 和 `action` 为 14 维。
- `observation.pulses` 为 12 维。
- Mock HAL/Mock camera 能完成 session create、episode save、dataset list、frame image 复核。
- 采集质量报告包含 max skew、avg skew、jitter、late frames、drop counts。
- `_collect_frame()` 在同一 tick 内并发启动 HAL、力觉窗口、夹爪缓存、Omega 和三路相机来源；单来源 timeout 只降级该来源，不阻塞当前帧。

## 3. 前端契约验证

```powershell
cd frontend
npm run build
```

前端不需要改变录制主流程。新增质量字段应以兼容方式展示或忽略。

## 4. Mock 采集冒烟

1. 设置后端为 test/mock HAL 模式。
2. 启动后端服务。
3. 创建录制会话。
4. 保存一个短 episode。
5. 检查数据集 metadata 和质量报告。

验收点：

- 采集流程不依赖真实硬件也可完成。
- 标准 features 使用 LeRobot v3.0 结构。
- 缺失相机或硬件源产生 warning/drop，而不是中断整条流程。

## 5. 真实硬件冒烟

真实硬件机器上执行：

1. 启动 C++ HAL。
2. 确认 `/health`、`/motion/state`、`/omega/state` 可读。
3. 确认三路相机可截图。
4. 确认 NI-DAQmx 双路力觉可采样。
5. 创建 20 秒 episode 并保存。

验收点：

- 20 秒 episode 至少达到 95% 目标帧数。
- HAL skew >20 ms 或相机 skew >33.3 ms 被计入 quality report。
- 人为制造任一来源慢响应时，对应来源出现 timeout/warning/drop/stale，其他来源仍写入当前帧。
- estop、enabled、Omega.7 部分连接等风险进入 warning。
- 生成的 metadata 满足 `contracts/dataset-metadata-v3.md`。

## 6. 当前实现差距记录

- 旧实现：标准 `observation.state`/`action` 为 12 维，夹爪单独写入 `observation.gripper`。
- 目标契约：标准 `observation.state`/`action` 为 14 维，左右从手夹爪分别进入 state/action，标准 features 不再包含 `observation.gripper`。
- 旧实现：原生 LeRobot features 中包含力觉窗口字段。
- 目标契约：LeRobot 标准 features 只包含训练主字段，力觉窗口、skew/jitter、drop 等作为 AppStation 扩展质量信息保存。
- 旧实现：HAL/Omega 状态没有统一读数时间戳。
- 目标契约：HAL motion/Omega 状态至少包含 `timestamp_ms`，后端补充 `received_timestamp_ms` 和 `received_monotonic_ms`。
- 旧实现：`_collect_frame()` 按顺序读取 HAL、力觉窗口和相机，只有三路相机截图内部并行。
- 目标契约：每个录制 tick 同时启动 HAL、力觉窗口、夹爪缓存、Omega 和三路相机来源，并用 per-source timeout 证明 30 Hz 录制与 skew 阈值。
