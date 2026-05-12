# 实施计划：数据收集完善

**分支**：`001-data-collection-spec-clean` | **日期**：2026-05-12 | **规格**：`specs/001-data-collection-spec/spec.md`
**输入**：来自 `specs/001-data-collection-spec/spec.md` 的功能规格

## 概要

完善 AppStation 数据收集链路，使录制 episode 能同时满足真实硬件复核、Mock 验证和 LeRobot v3 标准训练数据要求。实现策略是：后端以 30 Hz monotonic tick 作为录制主轴，在同一 tick 内并发启动 HAL、力觉窗口、夹爪缓存、Omega 状态和三路相机采集，并对每个来源设置 timeout 和质量降级；标准训练字段保持 14 维 state、14 维 action、12 维 pulses、双路 6 维力觉和三路视频相机，HAL health、力觉窗口、Omega 状态和 skew/jitter/drop 等信息作为 AppStation 扩展元数据保存。

## 技术上下文

**语言/版本**：Python 3.11+ 后端、TypeScript/React/Vite 前端、C++17 HAL
**主要依赖**：FastAPI/WebSocket 后端、`asyncio`、OpenCV/DirectShow 相机路径、NI-DAQmx 力觉路径、LeRobot 数据集库函数、负责 LTDMC 与 Force Dimension Omega.7 SDK 访问的 C++ HAL
**存储**：本地数据集根目录；LeRobot v3 原生能力可用时写原生数据集，不可用时写 JSON/JPEG fallback 复核格式
**测试**：`pytest`、`ruff check .`、`mypy backend`、前端 `npm run build`、Mock HAL/Mock camera 冒烟流程
**目标平台**：控制双臂微装配硬件的 Windows 工作站；WSL2 PolicyServer 不属于本功能范围
**项目类型**：包含 Python Backend、React 前端、C++ HAL 进程和共享数据集契约的 Web 应用
**性能目标**：默认 30 Hz 录制；20 秒 episode 至少保存 95% 目标帧；HAL skew 目标 <=10 ms，>20 ms 记 warning；相机 skew 目标 <=16.7 ms，>33.3 ms 记 late/drop
**约束**：后端和前端不得直接调用 vendor SDK；单来源 timeout 不得阻塞当前帧；AppStation 质量扩展不得改变标准 LeRobot features
**规模/范围**：一个活动录制会话、三路相机、双 Nano-17 力觉、双 Omega.7 主手、12 个 LTDMC 运动轴、14 维 state/action 训练 schema

## 宪章检查

*门禁：阶段 0 研究前必须通过；阶段 1 设计后再次检查。*

- **前端契约**：受影响接口包括 `/api/record/session/create`、`/api/record/episode/save`、`/api/record/episode/discard`、`/api/record/session/finish`、`/api/record/reset/skip`、`/api/record/status`、数据集读取接口、帧图像接口和 `/ws` 遥测。改动保持 additive，现有录制流程兼容，旧前端可忽略新增质量字段。
- **进程边界**：Python Backend 负责录制状态、tick 编排、相机截图汇聚、力觉窗口、LeRobot/fallback 写入和数据集复核读取。C++ HAL 负责 LTDMC、Omega.7 SDK、物理轴映射、脉冲、estop、jog/home 限制和 HAL health。前端只消费 API/WS 状态并发起用户命令。
- **安全**：本计划不新增自动运动能力。录制必须在质量元数据中保留 estop、enabled、HAL health、力觉安全和 Omega 连接状态，不得隐藏已有安全状态。
- **数据与单位**：标准数据使用 14 维 `observation.state`、14 维 `action`、12 维 `observation.pulses`、双路 6 维力觉和三路 `[480, 640, 3]` 视频字段。平移持久化为 um，旋转持久化为 mdeg，夹爪开口/目标为 mm，力/力矩为 N/Nm。AppStation 扩展记录力觉窗口、HAL health、Omega 状态、来源时间戳、skew、jitter、late/drop 计数和 timeout 状态。
- **验证**：后端检查覆盖 shape/schema、同一 tick 来源编排、per-source timeout 降级、skew 阈值、Mock HAL/Mock camera 保存流程和质量报告聚合。硬件冒烟覆盖 HAL health、motion state、Omega state、三路相机截图、NI-DAQmx 力觉采样和 episode 质量报告。
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

**结构决策**：沿用现有 backend/HAL/frontend 划分。数据集采集逻辑保留在 `backend/services/dataset_recorder.py`；HAL 接口改动留在 `backend/hal_client/client.py` 和 C++ HAL 文件之后；前端只按需展示 additive 质量字段。

## 阶段 0：研究

已完成于 `research.md`。

- 复用 LeRobot 数据集库函数输出标准 v3 数据，而不是手写原生布局。
- Windows 后端不导入 Linux-only 的 LeRobot 录制脚本。
- 每个录制 tick 使用 `asyncio` 并发采集 HAL、力觉、夹爪、Omega 和相机来源。
- 标准 LeRobot features 与 AppStation 质量扩展分离。
- 使用 14 维 state/action，旧 `observation.gripper` 只允许作为兼容或调试元数据。
- 持久化 skew/jitter/late/drop 指标，让复核人员判断 episode 是否可用。

## 阶段 1：设计与契约

已完成产物：

- `data-model.md`：RecordingSession、CaptureTick、HardwareSampleSet、DatasetFrame、EpisodeQualityReport 和 DatasetMetadata。
- `contracts/recording-api.md`：录制/会话和数据集复核 API 兼容性、质量响应结构。
- `contracts/dataset-metadata-v3.md`：标准 LeRobot v3 metadata 与 AppStation 扩展边界。
- `contracts/telemetry-ws.md`：additive `/ws` recording quality 字段。
- `quickstart.md`：后端、前端、Mock 和真实硬件验证流程。

### Tick 采集设计

每个录制帧中，`_record_loop()` 计算目标 monotonic tick 时间，并调用 `_collect_frame(target_tick)`。`_collect_frame()` 必须在等待结果前，于同一 tick 内创建所有来源任务：

- HAL motion state
- 当前 tick 周期对应的力觉窗口
- 夹爪缓存或最新从手夹爪状态
- Omega.7 主手状态
- 三路相机截图，相机组内部继续并行

每个来源任务都必须由 per-source timeout 包装。timeout、异常或 stale 数据只降级该来源，并记录 warning/drop/stale 元数据；不得阻塞其他来源写入当前帧。

### 质量设计

每个来源结果至少记录 source 名称、ok 标记、采样 monotonic 时间、完成 monotonic 时间、耗时、timeout/stale/drop 原因和相对目标 tick 的 skew。Episode 质量聚合：

- 按来源统计 max skew
- 按来源统计 avg skew
- 按来源统计 jitter
- late frames
- drop counts
- 连续对齐失败次数
- warnings 和最终 `ok | warning | invalid` 状态

## 设计后宪章检查

- **前端契约**：通过。录制和数据集 API 变更为 additive，`/ws` 质量字段为可选。
- **进程边界**：通过。vendor SDK 访问仍保留在 HAL 或既有硬件专属后端路径，前端不新增 SDK 访问。
- **安全**：通过。不新增运动命令路径；安全状态保留在元数据和质量状态中。
- **数据与单位**：通过。标准 features 和单位符合 spec/contract，质量扩展分离保存。
- **验证**：通过。计划明确 Mock/真实硬件冒烟以及后端 timing/schema 单元测试。
- **简洁性**：通过。选择扩展现有录制循环，而不是新增独立生产者架构。

## 复杂度追踪

没有需要说明例外的宪章违规项。
