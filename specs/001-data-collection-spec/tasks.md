# 任务：数据收集完善

**输入**：来自 `specs/001-data-collection-spec/` 的设计文档  
**前置文档**：`specs/001-data-collection-spec/plan.md`、`specs/001-data-collection-spec/spec.md`、`specs/001-data-collection-spec/research.md`、`specs/001-data-collection-spec/data-model.md`、`specs/001-data-collection-spec/contracts/`、`specs/001-data-collection-spec/quickstart.md`

**测试要求**：必须测试。本功能影响 LeRobot 写入、数据集 schema、硬件集成、`/api/*` 契约、`/ws` 遥测兼容性、录制质量报告和前后端兼容性。  
**组织方式**：任务按用户故事组织，每个用户故事都应能独立实现、独立验证。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**：可并行执行，前提是任务操作不同文件且不依赖未完成任务
- **[Story]**：仅用户故事阶段使用，例如 `[US1]`、`[US2]`、`[US3]`
- 每个任务描述都包含明确文件路径

## 阶段 1：准备工作（共享基础）

**目的**：建立契约测试夹具、确认当前代码入口，并把设计文档中的验证基线落到可执行测试文件。

- [X] T001 在 `backend/tests/test_dataset_recorder.py` 增加 LeRobot v3 feature 契约常量夹具，覆盖 14 维 `observation.state`、14 维 `action`、12 维 `observation.pulses`、双路 6 维力觉和三路相机字段
- [X] T002 [P] 在 `backend/tests/test_app.py` 增加 `/api/record/session/create`、`/api/record/episode/save`、`/api/record/status`、`/api/datasets` 的 API 响应契约样例
- [X] T003 在 `backend/tests/test_dataset_recorder.py` 增加 HAL motion、Omega、force、camera、gripper 的 fake source helper，包含 sample timestamp、timeout 和 stale 标记
- [X] T004 在 `backend/tests/test_app.py` 增加 Mock HAL/Mock camera 的 FastAPI test client 初始化 helper，隔离 `APPSTATION_RUNTIME_DIR` 和 `APPSTATION_HAL_MODE`
- [X] T005 在 `specs/001-data-collection-spec/quickstart.md` 记录当前实现与 `specs/001-data-collection-spec/contracts/dataset-metadata-v3.md` 的差距核对项，并补充 Windows LeRobot 预检：conda 环境、`ffmpeg=7.1.1`、`av`、`libsvtav1`、禁止整体 import `lerobot.scripts.lerobot_record`、`LeRobotDataset.create(repo_id="user/name", root=...)`

---

## 阶段 2：基础能力（阻塞前置）

**目的**：完成所有用户故事共用的 recorder、HAL client、遥测和 schema 基础能力。

**关键要求**：本阶段完成前，不应开始任何用户故事实现。

- [X] T006 在 `backend/services/dataset_recorder.py` 定义 LeRobot v3 标准 feature 名称、shape 和 video info 常量，并确保标准 features 不包含 `observation.gripper`
- [X] T007 在 `backend/services/dataset_recorder.py` 实现 12 维 HAL/UI 位姿加左右夹爪开口到 14 维 `observation.state` 的组合 helper，并通过 `backend/core/units.py` 或等价唯一入口处理工作原点脉冲到 UI 状态换算
- [X] T008 在 `backend/services/dataset_recorder.py` 实现 14 维 `action` 组合 helper，基于当前 state、最近 teleop delta 和左右夹爪目标生成动作
- [X] T009 在 `backend/services/dataset_recorder.py` 实现 LeRobot v3 native/fallback 共用的 metadata features 生成 helper
- [X] T010 在 `backend/services/dataset_recorder.py` 增加每个录制 tick 的 target monotonic time、captured monotonic time、source start/finish time、skew、timeout 和 stale 记账结构
- [X] T011 在 `backend/services/dataset_recorder.py` 增加 per-source timeout wrapper，覆盖 HAL、force、camera、gripper 和 Omega 来源
- [X] T012 在 `backend/hal_client/client.py` 为 `motion_state()` 和 `omega_state()` 增加 HAL response timestamp、received timestamp 和 received monotonic timestamp 归一化
- [X] T013 [P] 在 `hal/include/HalTypes.h` 为 motion state 和 Omega state 增加读取时间字段
- [X] T014 [P] 在 `hal/src/HalServer.cpp` 为 `/motion/state` 和 `/omega/state` JSON 输出增加读取时间字段
- [X] T015 [P] 在 `backend/services/telemetry_hub.py` 保持 `/ws` 现有字段兼容，并仅以 additive 方式暴露 recording、episodeCount、frameCount 和硬件可信度相关字段
- [X] T016 [P] 在 `backend/tests/test_dataset_recorder.py` 增加 14 维 state/action、12 维 pulses、standard features、工作原点换算、HAL telemetry fallback 和 `observation.gripper` 排除测试
- [X] T017 [P] 在 `backend/tests/test_app.py` 增加 HAL/Omega timestamp 归一化测试，覆盖 real client、test client、HAL jog 单步/Yaw 限制和采集中 enabled/home/jog 状态保留

**检查点**：recorder 能表达新的数据契约；HAL/Omega 时间字段能进入后端；API/WS 兼容性有测试保护。

---

## 阶段 3：用户故事 1 - 录制可复核的双臂数据（优先级：P1，MVP）

**目标**：操作员完成一次短采集并保存 episode 后，可复核 14 维 state、14 维 action、12 轴 pulses、双路 6 维力觉和三路相机数据。

**独立测试**：使用 Mock HAL/Mock camera 创建会话、保存 episode，然后检查帧数、任务描述、标准 feature shape、双路力觉、三路图像和相机缓存兜底。

### 用户故事 1 测试

- [X] T018 [P] [US1] 在 `backend/tests/test_dataset_recorder.py` 增加 native LeRobot v3 metadata features 的失败优先测试
- [X] T019 [US1] 在 `backend/tests/test_dataset_recorder.py` 增加 fallback JSON/JPEG metadata features 的失败优先测试
- [X] T020 [US1] 在 `backend/tests/test_dataset_recorder.py` 增加 `_collect_frame()` 输出 14 维 state/action、12 维 pulses、双路力觉和三路相机 payload 的失败优先测试
- [X] T021 [P] [US1] 在 `backend/tests/test_app.py` 增加 `/api/record/episode/save` 返回 `episodeIndex`、`frames`、`durationSec`、三路相机可用性和左右最大力值的失败优先测试
- [X] T022 [US1] 在 `backend/tests/test_dataset_recorder.py` 增加单路相机当前帧不可用时使用上一帧有效缓存、启动无缓存时使用占位帧的失败优先测试

### 用户故事 1 实现

- [X] T023 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_features()` 和 fallback metadata 写入，使其输出 LeRobot v3 标准 features
- [X] T024 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_native_features()`，复用 LeRobot v3 标准 features 并排除标准 `observation.gripper`
- [X] T025 [US1] 在 `backend/services/dataset_recorder.py` 更新 `_collect_frame()`，在每帧生成 14 维 `observation.state`、14 维 `action`、12 维 `observation.pulses`、双路 6 维力觉和 camera payload，且不写入旧 force-window 调试字段
- [X] T026 [US1] 在 `backend/services/dataset_recorder.py` 更新 fallback frame writer，使 `episodes.jsonl` 和 frame JSONL 持久化 v3 兼容字段
- [X] T027 [US1] 在 `backend/services/dataset_recorder.py` 更新 native frame writer，只向 `LeRobotDataset.add_frame()` 写入标准训练字段和 LeRobot 索引字段
- [X] T028 [US1] 在 `backend/services/dataset_recorder.py` 更新 LeRobot native 路径，先执行 Windows 兼容预检且不得整体 import `lerobot.scripts.lerobot_record`，再优先使用 `LeRobotDataset.create(repo_id="user/name", root=...)`、`LeRobotDataset.resume()`、`add_frame()`、`save_episode()` 和 `finalize()`
- [X] T029 [US1] 在 `backend/services/dataset_recorder.py` 更新三路相机解码、resize 和 synthetic placeholder 路径，保证 native frame 图像为 `[480, 640, 3]`，并记录物理能力分辨率、实际采集分辨率、预览分辨率和数据集保存分辨率
- [X] T030 [US1] 在 `backend/services/dataset_recorder.py` 更新 episode finalize 质量摘要，包含 frame count、duration、camera availability、camera cache used、maxForceLeft 和 maxForceRight
- [X] T031 [US1] 在 `backend/app.py` 将更新后的保存响应接入 `/api/record/episode/save`，保持 `ok/data/ts` envelope 不变

**检查点**：Mock session create/save 可生成 LeRobot v3 兼容 episode，且保存响应足够复核。

---

## 阶段 4：用户故事 2 - 复核和管理已采集数据集（优先级：P2）

**目标**：审核人员能列出本地数据集、查看 episode 摘要和抽样数据，并能重命名、标记无效或删除 episode。

**独立测试**：在已有至少一个 v3 数据集和 episode 的情况下，调用数据集 API 并刷新前端复核页面，确认帧数、时长、力觉摘要、图像样本、抽样轨迹和状态变更一致。

### 用户故事 2 测试

- [X] T032 [P] [US2] 在 `backend/tests/test_app.py` 增加 `GET /api/datasets` 读取 v3 metadata、fps、format、episode count 和 visible episode list 的失败优先测试
- [X] T033 [US2] 在 `backend/tests/test_app.py` 增加 `GET /api/datasets/{dataset_id}/episodes/{episode_id}` 暴露 feature shape 摘要和 episode 抽样信息的失败优先测试
- [X] T034 [US2] 在 `backend/tests/test_app.py` 增加 `GET /api/datasets/{dataset_id}/frame_image` 从三路 v3 image key 抽样图像的失败优先测试
- [X] T035 [US2] 在 `backend/tests/test_app.py` 增加数据集 create、rename、review save、stats、split、clean、export、push 以及 episode rename、status mark、delete 后不再作为可用样本展示的失败优先测试

### 用户故事 2 实现

- [X] T036 [US2] 在 `backend/services/dataset_recorder.py` 更新 dataset metadata reader，使其识别 LeRobot v3 native metadata 和 fallback metadata
- [X] T037 [US2] 在 `backend/services/dataset_recorder.py` 更新 native metadata episode index 恢复逻辑，保留 `datasetFromIndex`、`datasetToIndex`、hidden/deleted state 和 sample ranges
- [X] T038 [US2] 在 `backend/services/dataset_recorder.py` 更新 dataset list summary，包含 dataset id、display name、root、format、fps、status、updatedAt 和 visible episode list
- [X] T039 [US2] 在 `backend/services/dataset_recorder.py` 更新 episode detail reader，暴露 14 维 state/action shape、12 维 pulses、双路力觉、三路相机 key 和最大力觉摘要
- [X] T040 [US2] 在 `backend/services/dataset_recorder.py` 更新 `resolve_frame_image()`，支持 `observation.images.global`、`observation.images.wrist_left`、`observation.images.wrist_right` 和旧 camera key alias
- [X] T041 [US2] 在 `backend/services/dataset_recorder.py` 更新 dataset create、update、review save、stats、split、clean、export、push、`update_episode()` 和 `delete_episode()`，确保 invalid/deleted episode 不再进入 visible usable sample 列表，外部推送未启用时明确返回本地数据已就绪但未推送
- [X] T042 [US2] 在 `backend/app.py` 增加或修正 `GET /api/datasets/{dataset_id}/episodes/{episode_id}` 路由，并检查 dataset create、review save、stats、split、clean、export、push 路由都返回兼容 envelope
- [X] T043 [P] [US2] 在 `frontend/src/types.ts` 更新 dataset、episode、feature summary 和 sample image 的 TypeScript 类型
- [X] T044 [US2] 在 `frontend/src/api/index.ts` 更新数据集 API client，消费新增 episode detail、frame image、dataset lifecycle 和本地就绪未推送字段且保持旧字段兼容
- [X] T045 [US2] 在 `frontend/src/views/DatasetView.tsx` 更新数据集复核 UI，显示 v3 feature shape、帧数、时长、力觉摘要、抽样轨迹、三路图像样本和各相机分辨率来源

**检查点**：已有和新保存的数据集都能按 v3 schema 复核；标记或删除后的 episode 不再作为可用训练样本展示。

---

## 阶段 5：用户故事 3 - 在真实硬件和无硬件环境下保持一致流程（优先级：P3）

**目标**：Mock、fallback 和真实硬件路径共享同一录制流程；缺少 LeRobot 原生依赖或部分硬件不可用时，仍能采集、保存、列出和复核。

**独立测试**：分别在无真实硬件测试模式和真实硬件模式下执行 session/save/list/review；人为制造单路相机失败时，该通道使用上一帧缓存，其他来源继续写入当前帧。

### 用户故事 3 测试

- [X] T046 [P] [US3] 在 `backend/tests/test_app.py` 增加 Mock HAL/Mock camera 端到端录制、保存、列出、复核的失败优先测试
- [X] T047 [P] [US3] 在 `backend/tests/test_dataset_recorder.py` 增加 LeRobot 原生依赖不可用时 fallback 录制与复核兼容性的失败优先测试
- [X] T048 [US3] 在 `backend/tests/test_dataset_recorder.py` 增加同一 tick 内并发启动 HAL、force、gripper、Omega 和 camera source tasks 的失败优先测试
- [X] T049 [US3] 在 `backend/tests/test_dataset_recorder.py` 增加 HAL skew、camera skew、source timeout、source stale 和 force cache fallback 的失败优先测试
- [X] T050 [P] [US3] 在 `backend/tests/test_force_nidaq_driver.py` 增加强制力觉窗口采样失败或 timeout 时使用 latest scalar/ring-buffer fallback 的失败优先测试

### 用户故事 3 实现

- [X] T051 [US3] 在 `backend/services/dataset_recorder.py` 将 `_record_loop()` 改为 1-60 Hz 可配置 monotonic tick scheduler，默认 30 Hz 并记录稳定 frame index
- [X] T052 [US3] 在 `backend/services/dataset_recorder.py` 将 `_collect_frame()` 改为同一 tick 内创建 HAL、force、gripper、Omega 和三路 camera tasks 后再等待结果
- [X] T053 [US3] 在 `backend/services/dataset_recorder.py` 为各来源执行 per-source timeout，任何单来源 timeout 或异常都不得阻塞其他来源写入当前帧
- [X] T054 [US3] 在 `backend/services/dataset_recorder.py` 为三路相机维护最近一次有效帧缓存；当前帧不可用时直接使用缓存，启动无缓存时才使用占位帧
- [X] T055 [US3] 在 `backend/services/dataset_recorder.py` 记录 source start/finish、sample time、skew、timeout、stale 和 cache_used，用于判断同一 tick 对齐契约
- [X] T056 [US3] 在 `backend/drivers/force_nidaq.py` 实现或整理力觉连续采样/ring-buffer 窗口接口，使 recorder 可读取当前 tick 前最近一帧周期的缓存
- [X] T057 [US3] 在 `backend/services/dataset_recorder.py` 使用 `backend/drivers/force_nidaq.py` 的缓存接口读取当前 tick 的双路 6 维力觉，避免每帧阻塞采样且不写入旧 force-window offsets
- [X] T058 [US3] 在 `backend/services/gripper_worker_service.py` 为夹爪 worker 缓存值增加 sample timestamp 或 stale marker，供 recorder 采集帧使用
- [X] T059 [US3] 在 `backend/services/telemetry_hub.py` 为直接夹爪采样路径返回按侧区分的 sample timestamp 和 age 信息，供 recorder 降级使用
- [X] T060 [US3] 在 `backend/services/dataset_recorder.py` 保留 LeRobot native 可用时的 native 写入路径，并在不可用时自动回退到 JSON/JPEG fallback 路径
- [X] T061 [P] [US3] 在 `hal/src/LTDMCDriver.cpp` 确认真实硬件不可用、DLL 缺失或控制卡不足时错误状态可由 HAL health 明确暴露，并验证 jog 平移单步、旋转单步、Yaw 限位和采集中 enabled/home/jog 状态保留，不得误报真机验证通过
- [X] T062 [P] [US3] 在 `hal/src/Omega7Driver.cpp` 确认 Omega.7 读取失败、单手缺失或按钮状态缺失时状态字段仍可返回并标记不可完全可信

**检查点**：Mock、fallback 和真实硬件路径共享同一用户流程；部分来源失败时不阻塞 episode 保存。

---

## 最终阶段：收尾与跨切面验证

**目的**：完成质量门禁、文档记录、契约同步和硬件冒烟。

- [X] T063 [P] 在 `backend/tests/test_app.py` 增加 `/ws` telemetry 兼容性和结构化日志测试，确认 `recording`、`episodeCount`、`frameCount`、`jointPositions`、`forceLeft`、`forceRight`、`gripperPositions`、`cameras` 字段未被移除，并断言采集、保存、丢弃、结束、恢复写入 LEROBOT/CAMERA/FORCE/SAFETY/HAL/BACKEND 中的适用通道
- [ ] T064 [P] 在 `backend/tests/test_dataset_recorder.py` 增加 20 秒 mock episode 至少达到 95% 目标帧数的 timing 冒烟测试
- [ ] T065 [P] 如果实现改变 API 或 telemetry response 字段名，在 `specs/001-data-collection-spec/contracts/recording-api.md` 同步更新录制和数据集 API 契约
- [ ] T066 [P] 如果实现改变 `/ws` 字段，在 `specs/001-data-collection-spec/contracts/telemetry-ws.md` 同步更新 telemetry additive 字段说明
- [X] T067 在 `backend/tests/test_dataset_recorder.py` 和 `backend/tests/test_app.py` 运行相关后端测试，并把结果记录到 `specs/001-data-collection-spec/quickstart.md`
- [X] T068 在 `backend/pyproject.toml` 所在后端环境运行 `ruff check .`，并把结果记录到 `specs/001-data-collection-spec/quickstart.md`
- [X] T069 从项目根目录运行 `mypy backend`，并把结果记录到 `specs/001-data-collection-spec/quickstart.md`
- [X] T070 如果前端类型、API client 或 UI 变更，在 `frontend/package.json` 所在目录运行 `npm run build`，并把结果记录到 `specs/001-data-collection-spec/quickstart.md`
- [ ] T071 执行 `specs/001-data-collection-spec/quickstart.md` 的 Windows LeRobot native 预检和 Mock HAL/Mock camera 冒烟流程，并记录 dependency preflight、dataset metadata、episode summary 和 frame image 输出
- [ ] T072 在真实硬件工作站执行 `specs/001-data-collection-spec/quickstart.md` 的真实硬件冒烟清单，并把 HAL、camera、force、Omega 和 episode 保存结果记录到 `hal/README.md`

---

## 依赖与执行顺序

### 阶段依赖

- **阶段 1 准备工作**：无依赖，可立即开始。
- **阶段 2 基础能力**：依赖阶段 1，阻塞所有用户故事。
- **阶段 3 US1**：依赖阶段 2，是 MVP 范围。
- **阶段 4 US2**：依赖阶段 2；可用 fixture 独立测试，但建议在 US1 保存路径可用后完成复核实现。
- **阶段 5 US3**：依赖阶段 2；可与 US2 并行，但其中 timing/fallback 任务会影响 US1 的质量报告。
- **最终阶段**：依赖已选择完成的用户故事。

### 用户故事依赖

- **US1（P1）**：阶段 2 后即可开始，不依赖其他用户故事。
- **US2（P2）**：阶段 2 后即可开始；读取路径可用 fixture 独立验证，完整体验依赖 US1 生成的新 episode。
- **US3（P3）**：阶段 2 后即可开始；与 US1 共享 recorder 采集路径，需要避免同时修改同一函数造成冲突。

### 用户故事内部顺序

- 先写测试任务，并确认测试先失败。
- 先完成 schema/model/helper，再完成 recorder service。
- 先完成 service，再完成 FastAPI endpoint。
- 先完成后端契约，再更新前端类型和 UI。
- 每个用户故事完成后，独立运行对应测试和冒烟检查。

---

## 并行机会

- 阶段 1 中 T002 可在 T001 之后与 T005 并行；T003、T004 需要等同文件测试夹具结构确定后执行。
- 阶段 2 中 T013、T014、T015、T016、T017 可并行。
- US1 中 T018 与 T021 可并行；其余 `backend/tests/test_dataset_recorder.py` 测试按文件内顺序执行。
- US2 中 T032 与 T043 可并行；其余 `backend/tests/test_app.py` 测试按文件内顺序执行。
- US3 中 T046、T047、T050、T061、T062 可并行；T048 和 T049 按 `backend/tests/test_dataset_recorder.py` 文件内顺序执行。
- 最终阶段 T063-T066 可并行。

---

## 并行执行示例：用户故事 1

```text
Task: "T018 [P] [US1] 在 backend/tests/test_dataset_recorder.py 增加 native LeRobot v3 metadata features 的失败优先测试"
Task: "T021 [P] [US1] 在 backend/tests/test_app.py 增加 /api/record/episode/save 返回标准 episode 保存字段的失败优先测试"
```

## 并行执行示例：用户故事 2

```text
Task: "T032 [P] [US2] 在 backend/tests/test_app.py 增加 GET /api/datasets 契约测试"
Task: "T043 [P] [US2] 在 frontend/src/types.ts 更新前端 dataset TypeScript 类型"
```

## 并行执行示例：用户故事 3

```text
Task: "T046 [P] [US3] 在 backend/tests/test_app.py 增加 Mock HAL/Mock camera 端到端录制测试"
Task: "T047 [P] [US3] 在 backend/tests/test_dataset_recorder.py 增加 fallback 录制与复核兼容性测试"
Task: "T050 [P] [US3] 在 backend/tests/test_force_nidaq_driver.py 增加强制力觉窗口 fallback 测试"
Task: "T061 [P] [US3] 在 hal/src/LTDMCDriver.cpp 确认真实硬件不可用错误状态"
Task: "T062 [P] [US3] 在 hal/src/Omega7Driver.cpp 确认 Omega.7 降级状态"
```

---

## 实施策略

### MVP 优先（只做 US1）

1. 完成阶段 1：准备测试夹具和契约样例。
2. 完成阶段 2：基础 recorder/HAL timestamp/schema 能力。
3. 完成阶段 3：US1 录制可复核 episode。
4. 停下验证：运行 US1 相关后端测试，执行 Mock session create/save，检查 metadata shape 和 frame records。

### 增量交付

1. 交付 US1：标准 LeRobot v3 兼容录制。
2. 交付 US2：使用 v3 metadata 复核、管理已有和新数据集。
3. 交付 US3：统一 Mock/fallback/真实硬件路径，落实同一 tick 并发采集和来源降级。
4. 完成最终阶段：运行 quickstart、后端测试、lint、mypy、前端 build 和硬件冒烟。

### 多人并行策略

1. 团队先共同完成阶段 1 和阶段 2。
2. 阶段 2 完成后，开发者 A 处理 US1，开发者 B 处理 US2 后端复核，开发者 C 处理 US3 硬件/fallback。
3. 修改 `backend/services/dataset_recorder.py` 的任务需要排队或频繁同步，因为 US1 和 US3 都集中触碰该文件。

---

## 任务完整性校验

- 所有任务均使用 `- [ ] T###` markdown checklist 格式。
- 用户故事阶段任务均包含 `[US1]`、`[US2]` 或 `[US3]` 标签。
- Setup、Foundational 和最终阶段任务不包含用户故事标签。
- 每个任务都包含明确文件路径。
- 测试任务覆盖 API 契约、schema 单位、LeRobot 写入、Mock 冒烟、硬件降级、时间对齐和前后端兼容性。
