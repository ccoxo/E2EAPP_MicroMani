# 任务：数据收集完善

**输入**：来自 `specs/001-data-collection-spec/` 的设计文档
**前置文档**：[plan.md](./plan.md)、[spec.md](./spec.md)、[research.md](./research.md)、[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

**测试要求**：必须测试。本功能影响 LeRobot 写入、数据集 schema、硬件集成、录制质量报告、`/api/*` 契约、`/ws` 遥测兼容性和 HAL 安全/健康状态可见性。

**组织方式**：任务按用户故事分组，并按依赖顺序排列，保证每个用户故事都可独立验证。

## 阶段 1：准备工作（共享基础）

**目的**：在修改 recorder/HAL 行为前，先建立测试夹具和契约引用。

- [X] T001 在 `backend/tests/test_dataset_recorder.py` 增加 LeRobot v3 feature 契约常量和期望字段名夹具
- [X] T002 [P] 在 `backend/tests/test_app.py` 增加 session/save/dataset 响应的录制 API 契约示例
- [X] T003 [P] 在 `backend/tests/test_dataset_recorder.py` 增加带 timestamp、enabled、estop 和部分 Omega 状态的 HAL motion/Omega fixture helper
- [X] T004 [P] 在 `backend/tests/test_dataset_recorder.py` 增加带 per-source monotonic timestamp 的 camera/force/gripper fake hardware fixture helper
- [X] T005 在 `specs/001-data-collection-spec/quickstart.md` 按 `contracts/dataset-metadata-v3.md` 记录当前 LeRobot native/fallback 行为差距

---

## 阶段 2：基础能力（阻塞前置）

**目的**：建立所有用户故事共用的 recorder/HAL 基础能力。

**关键要求**：这些任务完成前，不应开始任何用户故事实现。

- [X] T006 在 `backend/services/dataset_recorder.py` 增加 14 维 state/action feature 名称常量，并从 native features 中移除标准 `observation.gripper`
- [X] T007 在 `backend/services/dataset_recorder.py` 增加从 12 维 HAL/UI 状态和左右从手夹爪开口组合 14 维 `observation.state` 的 helper
- [X] T008 在 `backend/services/dataset_recorder.py` 增加根据当前 observation state、主手/teleop delta 和夹爪目标组合 14 维 `action` 的 helper
- [X] T009 在 `backend/services/dataset_recorder.py` 增加生成 LeRobot v3 标准 features 的 helper，视频 shape 为 `[480, 640, 3]`，names 为 `height/width/channels`
- [X] T010 在 `backend/services/dataset_recorder.py` 增加 HAL、camera、force、gripper 和 Omega 共用的 per-source sample result 数据结构
- [X] T011 在 `backend/services/dataset_recorder.py` 增加 monotonic tick 记账字段：target time、actual capture time、source skew、late flags 和 drop flags
- [X] T012 在 `backend/hal_client/client.py` 为 `motion_state()` 和 `omega_state()` 增加 HAL 响应 timestamp 传播与 fallback timing 支持
- [X] T013 [P] 在 `hal/src/HalServer.cpp` 增加 motion state 读取时间和 Omega state 读取时间 JSON 字段
- [X] T014 [P] 在 `hal/include/HalTypes.h` 按需增加 motion/Omega read timestamp C++ HAL 类型字段
- [X] T015 [P] 在 `backend/tests/test_dataset_recorder.py` 增加 14 维 state/action 组合和 feature metadata shape 单元测试
- [X] T016 [P] 在 `backend/tests/test_app.py` 增加真实和 mock HAL client 的 HAL/Omega timestamp 归一化单元测试

**检查点**：Recorder 已能表达新的数据契约，但用户可见流程尚未完整。

---

## 阶段 3：用户故事 1 - 录制可复核的双臂数据（P1，MVP）

**目标**：保存一个短 episode，包含 LeRobot v3 兼容的 14 维 state/action、12 维 pulses、力觉、力觉窗口、图像和质量信息。

**独立测试**：启动 mock 录制会话，保存 episode，并验证帧数、任务、14 维 state、14 维 action、12 维 pulses、力觉数据、相机数据和质量报告。

### 用户故事 1 测试

- [X] T017 [P] [US1] 在 `backend/tests/test_dataset_recorder.py` 增加 LeRobot v3 native metadata features 的失败优先测试
- [X] T018 [P] [US1] 在 `backend/tests/test_app.py` 增加 fallback 录制保留 v3 兼容 feature metadata 的失败优先测试
- [X] T019 [P] [US1] 在 `backend/tests/test_dataset_recorder.py` 增加由 observation state 与 teleop delta 计算 14 维 action 绝对目标的失败优先测试
- [X] T020 [P] [US1] 在 `backend/tests/test_app.py` 增加 `POST /api/record/episode/save` 返回 `contracts/recording-api.md` 质量字段的失败优先测试

### 用户故事 1 实现

- [X] T021 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_features()` fallback metadata，使其输出 LeRobot v3 标准 features
- [X] T022 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_native_features()`，输出 LeRobot v3 标准 features，并排除标准 `observation.gripper`
- [X] T023 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_collect_frame()`，输出 14 维 `observation.state`、14 维 `action`、12 维 `observation.pulses`、force 数据、force windows 和 image payload
- [X] T024 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_write_frame_locked()` fallback writer，持久化 v3 兼容帧字段和 AppStation 质量扩展字段
- [X] T025 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_write_native_frame_locked()`，只把 LeRobot 标准帧字段传给 `LeRobotDataset.add_frame()`，质量扩展移出标准 features
- [X] T026 [US1] 在 `backend/services/dataset_recorder.py` 更新 LeRobot create/resume 路径，复用 `LeRobotDataset.create()`、`LeRobotDataset.resume()`、`add_frame()`、`save_episode()` 和 `finalize()`，不本地重实现
- [X] T027 [US1] 在 `backend/services/dataset_recorder.py` 更新 camera decode/synthetic frame 路径，生成 `[480, 640, 3]` image/video 兼容数组用于 native LeRobot frames
- [X] T028 [US1] 在 `backend/services/dataset_recorder.py` 更新 episode save 响应，包含 `status`、`lateFrames`、`dropCounts`、`maxSkewMs`、`avgSkewMs` 和 `jitterMs`
- [X] T029 [US1] 在 `backend/app.py` 将更新后的 quality response 接入 `/api/record/episode/save`，不破坏 envelope 结构

**检查点**：Mock session create/save 能生成 LeRobot v3 兼容 metadata 和 frame records 时，US1 完成。

---

## 阶段 4：用户故事 2 - 复核和管理已采集数据集（P2）

**目标**：数据集复核能基于新 schema 和质量 metadata 列出、查看、抽样、标记和删除 episode。

**独立测试**：打开已有数据集，执行 rename/status/delete 后，确认每个 episode 显示质量、帧数、时长、drop、力觉摘要和抽样图像。

### 用户故事 2 测试

- [ ] T030 [P] [US2] 在 `backend/tests/test_app.py` 增加列出包含 v3 metadata 与 AppStation quality extension 的数据集失败优先测试
- [ ] T031 [P] [US2] 在 `backend/tests/test_app.py` 增加 episode detail 暴露质量报告和 feature shape 摘要的失败优先测试
- [ ] T032 [P] [US2] 在 `backend/tests/test_app.py` 增加从 `observation.images.global`、`observation.images.wrist_left` 和 `observation.images.wrist_right` 抽样 frame image 的失败优先测试

### 用户故事 2 实现

- [ ] T033 [US2] 在 `backend/services/dataset_recorder.py` 更新 dataset metadata reader，使其理解 LeRobot v3 feature shapes 和 AppStation extension metadata
- [ ] T034 [US2] 在 `backend/services/dataset_recorder.py` 更新从 native metadata 恢复 episode index 的逻辑，保留 quality status、hidden/deleted state 和 sample ranges
- [ ] T035 [US2] 在 `backend/services/dataset_recorder.py` 更新 dataset list summary，包含 v3 format、fps、episode count、status、latest quality 和 visible episode filtering
- [ ] T036 [US2] 在 `backend/services/dataset_recorder.py` 更新 episode detail reader，暴露 14 维 state/action shape、quality report、drops、skew、jitter 和 warning 信息
- [ ] T037 [US2] 在 `backend/services/dataset_recorder.py` 更新 `frame_image` native/fallback 抽样逻辑，读取 v3 video/image keys 并保留旧 camera-key alias
- [ ] T038 [US2] 在 `backend/app.py` 更新 dataset API 响应，新增 additive 复核字段并保持现有前端 key 兼容
- [ ] T039 [P] [US2] 在 `frontend/src/types.ts` 更新前端 dataset/quality TypeScript 类型，新增可选质量字段
- [ ] T040 [US2] 在 `frontend/src/views/DatasetView.tsx` 更新数据集复核 UI，显示新 quality status、skew/jitter 和 drop 摘要，不改变主流程

**检查点**：已有和新数据集都能用 v3 schema 复核时，US2 完成。

---

## 阶段 5：用户故事 3 - 在真实硬件和无硬件环境下保持一致流程（P3）

**目标**：Mock 和真实硬件路径共享同一录制流程，硬件特定失败降级为 warning/drop/timeout，而不是中断整条链路。

**独立测试**：分别运行 mock 会话和真实模式不可用会话，两者都完成 session/save/list/review 流程，差异只体现在 source status 和 warning。

### 用户故事 3 测试

- [ ] T041 [P] [US3] 在 `backend/tests/test_app.py` 增加 Mock HAL/Mock camera 的端到端录制失败优先测试，并验证 v3 metadata
- [ ] T042 [P] [US3] 在 `backend/tests/test_dataset_recorder.py` 增加 LeRobot 不可用时 fallback 录制与复核兼容性的失败优先测试
- [ ] T043 [P] [US3] 在 `backend/tests/test_dataset_recorder.py` 增加单路相机失败产生 warning/drop 且其他来源继续写入的失败优先测试
- [ ] T044 [P] [US3] 在 `backend/tests/test_force_nidaq_driver.py` 增加强制力觉窗口采样失败或 timeout 时使用 latest scalar/ring-buffer fallback 并产生质量 warning 的失败优先测试

### 用户故事 3 实现

- [X] T045 [US3] 在 `backend/services/dataset_recorder.py` 将 `_collect_frame()` 的串行硬件采集替换为同一 tick 的 `asyncio.gather()` 编排，覆盖 HAL motion、camera、force、gripper cache 和 Omega source tasks
- [X] T046 [US3] 在 `backend/services/dataset_recorder.py` 增加 per-source timeout：HAL 目标 <=10ms、>20ms warning；camera 目标 <=16.7ms、>33.3ms late/drop；force/gripper/Omega stale 数据降级而不是阻塞当前帧
- [X] T047 [US3] 在 `backend/services/dataset_recorder.py` 增加 per-source degradation 记录，覆盖 camera failure、force window failure/timeout、HAL temporary failure、Omega stale/failure state 和 gripper stale data
- [ ] T048 [US3] 在 `backend/services/dataset_recorder.py` 保留 `lerobot[dataset]` 可用时的 native LeRobot 写入路径，以及不可用时的 fallback 路径
- [ ] T049 [US3] 在 `backend/services/gripper_worker_service.py` 更新 gripper worker 采样交接，使缓存夹爪值包含 sample timestamp 或 stale marker，供 recorder 使用
- [ ] T050 [US3] 在 `backend/services/telemetry_hub.py` 更新 direct gripper sampling 路径，返回按侧区分的 sample timestamp 和 age 信息，供 recorder 使用
- [ ] T051 [US3] 在 `backend/drivers/force_nidaq.py` 和 `backend/services/dataset_recorder.py` 将力觉窗口记录移动到后台连续采样/ring-buffer 路径，使 30Hz recorder 选择当前 tick 前的最新窗口，而不是每帧阻塞采样

**检查点**：Mock/无硬件路径和真实模式不可用路径都能完成相同用户流程时，US3 完成。

---

## 阶段 6：用户故事 4 - 用 HAL 状态约束采集可信度（P4）

**目标**：HAL health、motion enabled/estop、Omega.7 状态和来源时间信息进入质量报告和复核数据。

**独立测试**：启动 HAL skeleton 或真实 HAL，读取 health/motion/Omega state，并验证录制会话能把 state、pulses、enabled、estop 和主手状态追溯到 trusted 或 warning 来源。

### 用户故事 4 测试

- [ ] T052 [P] [US4] 在 `backend/tests/test_dataset_recorder.py` 增加 HAL estop 和 enabled 状态进入 episode quality metadata 的失败优先测试
- [ ] T053 [P] [US4] 在 `backend/tests/test_app.py` 增加部分 Omega.7 连接进入 warnings 和 action-source status 的失败优先测试
- [ ] T054 [P] [US4] 在 `hal/README.md` 增加 C++ HAL 冒烟检查清单，覆盖 `/health`、`/motion/state` 和 `/omega/state` timestamp 字段

### 用户故事 4 实现

- [ ] T055 [US4] 在 `backend/services/dataset_recorder.py` 将 HAL health、motion enabled array、estop 和 motion read timing 持久化到 AppStation episode extension metadata
- [ ] T056 [US4] 在 `backend/services/dataset_recorder.py` 将 Omega.7 左右连接、openId、deviceId、pose、buttons、gripper gap、lastReadOk 和 read timing 持久化到 AppStation episode extension metadata
- [ ] T057 [US4] 在 `backend/services/dataset_recorder.py` 增加质量 warning 规则：LTDMC unavailable、estop active、任一 motion axis disabled、部分 Omega.7 连接和 Omega read failure
- [ ] T058 [US4] 在 `hal/src/HalServer.cpp` 更新 HAL `/motion/state` JSON，如果 driver state 已有 moving array，则暴露该字段
- [ ] T059 [US4] 在 `hal/src/LTDMCDriver.cpp` 确保 `LTDMCDriver::readState()` 在 vendor 和 skeleton 模式下都一致填充 moving/enabled
- [ ] T060 [US4] 在 `hal/src/Omega7Driver.cpp` 确保 `Omega7Driver::readState()` 在 vendor 和 skeleton 模式下都保留双手 last read errors 和 gripper gap availability
- [ ] T061 [US4] 在 `backend/hal_client/client.py` 更新后端 HAL client 解析 additive motion/Omega 字段，不破坏 mock client 行为

**检查点**：质量报告能证明 HAL 和 Omega 来源是 trusted、degraded 还是 unavailable 时，US4 完成。

---

## 阶段 7：跨故事时间对齐与质量

**目的**：在基础数据录制可用后，强制落实跨来源时间契约。

- [ ] T062 [P] 在 `backend/tests/test_dataset_recorder.py` 增加同一 tick 并发来源采集、HAL skew 阈值、camera skew 阈值、force-window offsets 和连续对齐失败的失败优先单元测试
- [ ] T063 在 `backend/services/dataset_recorder.py` 增加使用 monotonic target time 和稳定 frame index 的 tick scheduler
- [ ] T064 在 `backend/services/dataset_recorder.py` 增加按来源聚合 timestamp/skew 的逻辑，统计 max skew、avg skew、jitter、late frames 和 drop counts
- [ ] T065 在 `backend/services/dataset_recorder.py` 增加连续 3 帧和连续 10 帧来源对齐失败时的 warning/invalid 状态转换
- [ ] T066 在 `backend/services/dataset_recorder.py` 增加相对当前 tick 的 force-window 选择和 offset 计算，将 per-sample offsets 保存在 AppStation extension metadata，且不阻塞 30Hz 录制 tick
- [ ] T067 在 `backend/core/schemas.py` 增加可选 `recordingQuality` 遥测字段，用于实时质量计数
- [ ] T068 在 `backend/services/telemetry_hub.py` 接入可选 `recordingQuality` 遥测更新，同时保留现有 `/ws` 字段
- [ ] T068A 在 `backend/tests/test_app.py` 增加 timing 冒烟测量：记录 per-frame collection duration，并验证 mock 模式下 p95 frame collection time 小于 33.3ms；真实模式不可用路径应报告 warning

---

## 阶段 8：收尾与跨切面验证

**目的**：完成最终验证、文档记录和兼容性检查。

- [ ] T069 [P] 在 `backend/tests/test_dataset_recorder.py` 更新后端测试，断言 v3 metadata 中不出现标准 `observation.gripper` feature
- [ ] T070 [P] 如果实现改变 additive response 字段名，则更新 `specs/001-data-collection-spec/contracts/recording-api.md` 契约文档
- [ ] T071 运行后端 pytest suite，并在 `specs/001-data-collection-spec/quickstart.md` 记录结果
- [ ] T072 在 `backend/` 运行 `ruff check .`，并在 `specs/001-data-collection-spec/quickstart.md` 记录结果
- [ ] T073 从已配置的后端环境运行 `mypy backend`，并在 `specs/001-data-collection-spec/quickstart.md` 记录结果
- [ ] T074 如果 API 或 telemetry response 类型变更，在 `frontend/` 运行前端 `npm run build`，并在 `specs/001-data-collection-spec/quickstart.md` 记录结果
- [ ] T075 执行 `quickstart.md` 中的 Mock HAL/Mock camera 冒烟流程，并在 `specs/001-data-collection-spec/quickstart.md` 记录观测到的 dataset metadata/quality 输出
- [ ] T076 在硬件工作站执行真实硬件冒烟清单，并在 `hal/README.md` 记录 HAL/camera/force/Omega 结果

---

## 依赖与执行顺序

### 阶段依赖

- **阶段 1 准备工作**：无依赖。
- **阶段 2 基础能力**：依赖阶段 1，并阻塞所有用户故事。
- **阶段 3 US1**：依赖阶段 2，是 MVP 范围。
- **阶段 4 US2**：依赖阶段 2；可在理解 US1 metadata/quality 契约测试后启动，但复核实现最好在 US1 之后完成。
- **阶段 5 US3**：依赖阶段 2；核心 recorder 组合能力存在后，可与 US2 并行。
- **阶段 6 US4**：依赖阶段 2 和 HAL timestamp 字段；如果 C++ HAL 改动相互隔离，可与 US2/US3 并行。
- **阶段 7 时间对齐**：依赖 US1 采集路径，并受益于 US3/US4 来源元数据。
- **阶段 8 收尾**：依赖已选择的实现阶段。

### 用户故事依赖

- **US1**：MVP；基础能力之后不依赖其他用户故事。
- **US2**：使用 US1 产生的数据集，但可用 fixture 独立测试。
- **US3**：使用基础 recorder primitives，可用 fake hardware 独立测试。
- **US4**：使用基础 HAL fixtures，不需要完成 UI 复核即可测试。

### 并行机会

- T002-T004 可并行。
- T013-T016 在 T006-T012 范围确定后可并行。
- US1 测试任务 T017-T020 可并行。
- US2 测试任务 T030-T032 可并行。
- US3 测试任务 T041-T044 可并行。
- US4 测试任务 T052-T054 可并行。
- HAL 任务 T058-T060 可在契约稳定后与后端 recorder 任务并行。
- 验证任务 T069-T070 可并行。

---

## 并行示例：用户故事 1

```text
任务："T017 在 backend/tests/test_dataset_recorder.py 增加 LeRobot v3 native metadata features 的失败优先测试"
任务："T018 在 backend/tests/test_app.py 增加 fallback 录制保留 v3 兼容 feature metadata 的失败优先测试"
任务："T019 在 backend/tests/test_dataset_recorder.py 增加 14 维 action 绝对目标计算测试"
任务："T020 在 backend/tests/test_app.py 增加 POST /api/record/episode/save 质量字段测试"
```

## 并行示例：HAL 工作

```text
任务："T058 在 hal/src/HalServer.cpp 更新 HAL /motion/state JSON，暴露已有 moving array"
任务："T059 在 hal/src/LTDMCDriver.cpp 确保 vendor 和 skeleton 模式一致填充 moving/enabled"
任务："T060 在 hal/src/Omega7Driver.cpp 保留双手 last read errors 和 gripper gap availability"
```

---

## 实施策略

### 先完成 MVP

1. 完成阶段 1 和阶段 2。
2. 完成阶段 3 US1。
3. 验证 mock session create/save 和 metadata shape。
4. 如果只需要 MVP，可在扩大复核/真实硬件工作前暂停。

### 增量交付

1. 交付 US1：标准 LeRobot v3 兼容录制。
2. 交付 US2：使用 v3 metadata 和质量报告复核已有/新数据集。
3. 交付 US3：让 mock/真实硬件路径共享异步采集和 fallback 行为。
4. 交付 US4：增加 HAL/Omega 可信度 metadata 和 warnings。
5. 交付阶段 7：强制执行时间质量阈值。

### 安全说明

- recorder 任务不得移动硬件。
- Python Backend 不得调用 LTDMC 或 Force Dimension SDK。
- 标准 features 必须稳定；HAL health、force windows、skew/jitter 和 warnings 放入 AppStation extension metadata。
- 当某个来源 stale 或 unavailable 时，记录 warning/drop/timeout 信息，而不是阻塞所有其他来源。
