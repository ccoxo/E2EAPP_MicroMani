# 实施计划：数据收集完善

**分支**：`001-data-collection-spec` | **日期**：2026-05-12 | **规格**：`specs/001-data-collection-spec/spec.md`
**输入**：来自 `specs/001-data-collection-spec/spec.md` 的功能规格

## 概要

完善 AppStation 数据收集链路，使录制 episode 能同时满足真实硬件复核、Mock 验证和 LeRobot v3 标准训练数据要求。实现策略是：后端以 30 Hz monotonic tick 作为录制主轴，在同一 tick 内并发启动 HAL、力觉窗口、夹爪缓存、Omega 状态和三路相机采集；标准训练字段保持 14 维 state、14 维 action、12 维 pulses、双路 6 维力觉和三路视频相机。相机当前帧不可用时直接使用上一帧有效缓存，启动后尚无缓存时才写入占位帧。

## 技术上下文

**语言/版本**：Python 3.11+ 后端、TypeScript/React/Vite 前端、C++17 HAL
**主要依赖**：FastAPI/WebSocket、`asyncio`、OpenCV/DirectShow、NI-DAQmx、LeRobot dataset 库函数、LTDMC 与 Force Dimension Omega.7 SDK 所在的 C++ HAL
**存储**：本地数据集根目录；LeRobot v3 原生能力可用时写原生数据集，不可用时写 JSON/JPEG fallback 复核格式
**测试**：`pytest`、`ruff check .`、`mypy backend`、前端 `npm run build`、Mock HAL/Mock camera 冒烟流程
**目标平台**：控制双臂微装配硬件的 Windows 工作站；WSL2 PolicyServer 不属于本功能范围
**项目类型**：包含 Python Backend、React 前端、C++ HAL 进程和共享数据集契约的 Web 应用
**性能目标**：默认 30 Hz 录制；20 秒 episode 至少保存 95% 目标帧；相机当前帧不可用时使用上一帧有效缓存
**约束**：后端和前端不得直接调用 vendor SDK；单来源 timeout 不得阻塞当前帧；标准 LeRobot features 只保留训练必要字段
**规模/范围**：一个活动录制会话、三路相机、双 Nano-17 力觉、双 Omega.7 主手、12 个 LTDMC 运动轴、14 维 state/action 训练 schema

## 宪章检查

*门禁：第 0 阶段研究前必须通过；第 1 阶段设计后再次检查。*

- **前端契约**：受影响接口包括 `/api/record/session/create`、`/api/record/episode/save`、`/api/record/episode/discard`、`/api/record/session/finish`、`/api/record/reset/skip`、`/api/record/status`、数据集读取接口、帧图像接口和 `/ws` 遥测。改动保持 additive，现有录制流程兼容。
- **进程边界**：Python Backend 负责录制状态、tick 编排、相机截图汇聚、力觉窗口、LeRobot/fallback 写入和数据集复核读取。C++ HAL 负责 LTDMC、Omega.7 SDK、物理轴映射、脉冲、estop 和 jog/home 限制。前端只消费 API/WS 状态并发起用户命令。
- **安全**：本计划不新增自动运动能力。录制不得绕过或隐藏已有急停、watchdog、轴 enabled、力觉安全和主手连接状态。
- **数据与单位**：标准数据使用 14 维 `observation.state`、14 维 `action`、12 维 `observation.pulses`、双路 6 维力觉和三路 `[480, 640, 3]` 视频字段。平移持久化为 um，旋转持久化为 mdeg，夹爪开口/目标为 mm，力/力矩为 N/Nm。
- **验证**：后端检查覆盖 shape/schema、同一 tick 来源编排、相机缓存兜底、Mock HAL/Mock camera 保存流程。硬件冒烟覆盖 motion state、Omega state、三路相机截图、NI-DAQmx 力觉采样和 episode 保存结果。
- **简洁性**：使用一个后端录制循环，按 tick 使用 `asyncio.gather()` 和 per-source wrapper。除非单循环设计经测量无法满足 skew 阈值，否则不引入独立生产者进程或跨进程 timestamp bus。

**门禁结果**：通过。设计保持 API/WS 契约兼容，遵守进程边界，不新增运动权限，并给出可测量的数据与时间契约。

## 项目结构

### 本功能文档

```text
specs/001-data-collection-spec/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   |-- recording-api.md
|   |-- dataset-metadata-v3.md
|   `-- telemetry-ws.md
|-- checklists/
|   `-- requirements.md
`-- tasks.md
```

### 源代码

```text
backend/
|-- services/
|   |-- dataset_recorder.py
|   `-- telemetry_hub.py
|-- hal_client/
|   `-- client.py
`-- tests/
    |-- test_app.py
    `-- test_dataset_recorder.py

hal/
|-- include/
|   |-- HalTypes.h
|   `-- Omega7Driver.h
`-- src/
    |-- HalServer.cpp
    |-- LTDMCDriver.cpp
    `-- Omega7Driver.cpp

frontend/
`-- src/
```

**结构决策**：沿用现有 backend/HAL/frontend 划分。数据集采集逻辑保留在 `backend/services/dataset_recorder.py`；HAL 接口改动留在 `backend/hal_client/client.py` 和 C++ HAL 文件之后；前端只按需展示录制和数据集复核所需字段。

## 第 0 阶段：研究

已完成于 `research.md`。

- 复用 LeRobot 数据集库函数输出标准 v3 数据，而不是手写原生布局。
- Windows 后端不导入 Linux-only 的 LeRobot 录制脚本。
- 每个录制 tick 使用 `asyncio` 并发采集 HAL、力觉、夹爪、Omega 和相机来源。
- 标准 LeRobot features 只保留训练必要字段。
- 使用 14 维 state/action，旧 `observation.gripper` 只允许作为兼容或调试元数据。
- 相机当前帧不可用时使用上一帧有效缓存，无缓存时使用占位帧。

## 第 1 阶段：设计与契约

已完成产物：

- `data-model.md`：RecordingSession、CaptureTick、HardwareSampleSet、DatasetFrame 和 DatasetMetadata。
- `contracts/recording-api.md`：录制会话和数据集复核 API 兼容性。
- `contracts/dataset-metadata-v3.md`：标准 LeRobot v3 metadata 与 AppStation 边界。
- `contracts/telemetry-ws.md`：`/ws` 遥测兼容字段。
- `quickstart.md`：后端、前端、Mock 和真实硬件验证流程。

### Tick 采集设计

每个录制帧中，`_record_loop()` 计算目标 monotonic tick 时间，并调用 `_collect_frame(target_tick)`。`_collect_frame()` 必须在等待结果前，于同一 tick 内创建所有来源任务：

- HAL motion state
- 当前 tick 周期对应的力觉窗口
- 夹爪缓存或最新从手夹爪状态
- Omega.7 主手状态
- 三路相机截图，相机组内部继续并行

每个来源任务都必须由 per-source timeout 包装。timeout 或异常不得阻塞其他来源写入当前帧。三路相机中任一路当前帧不可用时，直接使用该路上一帧有效缓存；若启动后尚无缓存，才使用占位帧。

## 设计后宪章检查

- **前端契约**：通过。录制和数据集 API 变更保持 additive。
- **进程边界**：通过。Vendor SDK 访问仍保留在 HAL 或既有硬件专属后端路径，前端不新增 SDK 访问。
- **安全**：通过。不新增运动命令路径。
- **数据与单位**：通过。标准 features 和单位符合 spec/contract。
- **验证**：通过。计划明确 Mock/真实硬件冒烟以及后端 timing/schema 单元测试。
- **简洁性**：通过。选择扩展现有录制循环，而不是新增独立生产者架构。

## 复杂度追踪

没有需要说明例外的宪章违规项。
