<!--
同步影响报告
版本变更：1.0.0 -> 1.0.1
修改的原则：
- I. 前端契约兼容
- II. 进程边界与硬件隔离
- III. 安全先于运动
- IV. 固定的数据、单位与日志契约
- V. 基于 Mock 路径的增量验证
新增章节：
- AppStation 后端约束
- 开发流程与质量门禁
移除章节：无
需要同步的模板：
- ✅ .specify/templates/plan-template.md
- ✅ .specify/templates/spec-template.md
- ✅ .specify/templates/tasks-template.md
- ✅ .specify/templates/commands/*.md（目录不存在）
- ✅ AGENTS.md
- ✅ cc_data_collection/claude.md
- ✅ cc_data_collection/frontend/README.md
- ✅ cc_data_collection/hal/README.md
后续 TODO：无
-->
# AppStation 宪章

## Core Principles

### I. 前端契约兼容
后端开发 MUST 在逐步替换 Mock 数据源时保持现有前端交互契约不变。Python Backend
MUST 提供与当前 React/Vite 前端兼容的 `/api/*` REST 接口和 `/ws` 实时遥测，包括
`TelemetryFrame`、`AppConfig`、录制会话阶段、质量报告语义和结构化 Log Panel 事件。
前端组件 MUST 继续通过 telemetry store 发起异步命令，MUST NOT 直接调用硬件 SDK、
启动本地进程或写本地文件。

理由：前端已经是可工作的操作员界面。后端改动只有在契约边界上保持真实数据与 Mock
数据行为一致时才有效。

### II. 进程边界与硬件隔离
系统 MUST 把硬件专用 SDK 访问限制在正确的进程边界内。C++ HAL 负责 Windows-only
LTDMC 与 Omega.7 SDK 调用，在 MotionControl Thread 内串行化运动控制卡访问，并只暴露
HAL 内部健康、运动和遥测接口。Python Backend 负责 FastAPI、前端 WebSocket 汇聚、录制
状态、SafetyService、相机、NI-DAQmx 力觉采集、LeRobot 写入、PICO 脚本、夹爪控制、
数据集服务和 PolicyServer 编排。WSL2 PolicyServer MUST 只用于需要 Linux/GPU 依赖的
CUDA 策略推理或训练路径。

Python Backend MUST NOT 直接调用 `LTDMC.dll` 或 Force Dimension SDK。前端 MUST NOT 绕过
Python Backend 或 HAL。业务逻辑 MUST 使用语义臂轴和共享 schema，MUST NOT 把物理轴号
泄漏到 UI 或业务服务代码中。

理由：本项目同时包含 UI、Windows 硬件 SDK 和 GPU 策略执行。清晰的进程所有权可以避免
不安全线程模型、不可测试集成和平台依赖扩散。

### III. 安全先于运动
任何可能移动硬件的命令 MUST 先通过后端安全检查，再进入 HAL；HAL MUST 同时执行自己的
短路径急停、软限位、Yaw 限位和运动参数校验。安全检查 MUST 覆盖力/力矩阈值、运动软限位、
Yaw 受限行程、HAL/相机/力觉/主手/策略服务 watchdog 超时，以及全局急停恢复状态。

`dangerIndex >= 1.0` MUST 触发急停或进入恢复确认状态。急停处理 MUST NOT 只依赖前端动画
或 Python 队列排队。解除急停状态 MUST 由操作员确认。Auto 页面动作注入等调试动作 MUST
要求开发模式或显式确认模式。

理由：AppStation 控制真实双臂设备。安全行为是后端与 HAL 的职责，不是 UI 约定。

### IV. 固定的数据、单位与日志契约
共享数据契约 MUST 明确；持久化契约在需要时 MUST 版本化，并在服务边界测试。12 轴状态
顺序固定为：`left_X, left_Y, left_Z, left_Roll, left_Pitch, left_Yaw, right_X,
right_Y, right_Z, right_Roll, right_Pitch, right_Yaw`。前端遥测 MUST 对平移轴使用微米，
对旋转轴使用度。LeRobot `observation.state` MUST 使用项目固定 schema；当 schema 选择
毫度时，旋转 MUST 存为 0.001 度。脉冲到 UI 与脉冲到 LeRobot 的换算 MUST 集中在
`backend/core/units.py` 或等价的唯一模块中。

力和力矩在后端、HAL-facing schema、LeRobot observation、settings 文件和 telemetry 中
MUST 以 `N` 与 `Nm` 存储和处理。UI 可以格式化显示为 `mN` 或 `mNm`，但后端持久化数据
MUST NOT 使用显示单位。日志 MUST 使用稳定的结构化通道名，例如 HAL、BACKEND、CAMERA、
FORCE、SAFETY、ZMQ、POLICY、LEROBOT 和 GRIPPER。

理由：单位漂移和 schema 漂移是本项目的高概率故障源。集中契约让硬件行为、数据集质量和
UI 显示可审计。

### V. 基于 Mock 路径的增量验证
实现 MUST 按可验证增量推进：P0 后端契约骨架与 Mock HAL，P1 HAL 状态与运动安全，P2
相机/力觉/录制数据闭环，P3 PICO-4 与夹爪，P4 PolicyServer 自动执行，P5 微调与运维增强。
真实硬件不可用时，MUST 保留硬件无关的 Mock HAL 与 Mock camera 路径。

每个功能 MUST 在实现前定义可度量验收检查。后端阶段 MUST 运行适用的最小检查：
`pytest`、`ruff check .`、`mypy backend`；硬件缺失时运行 Mock HAL/Mock camera 冒烟测试；
涉及用户界面契约变更时运行 `VITE_MOCK_MODE=false` 的前端集成构建。改动运动、安全、
单位换算、录制质量报告和接口契约时，相关测试是 mandatory。

理由：这个系统不能只靠代码审查验证。增量门禁可以在安全接入硬件的同时保持前端契约可用。

## AppStation 后端约束

第一个后端里程碑 MUST 提供与当前前端兼容的 FastAPI 接口：`/api/settings`、
`/api/record/session/create`、`/api/record/episode/save`、
`/api/record/episode/discard`、`/api/record/session/finish`、
`/api/record/reset/skip`、`/api/sensors/tare`、`/api/teleop/clutch_toggle`、
`/api/teleop/speed`、`/api/motion/emergency_stop`、`/api/motion/home_all` 和 `/ws`。
所有新的前端接口 MUST 使用 `/api/` 前缀。8091 端口的 HAL 内部接口 MUST NOT 使用 `/api/`
前缀。

Windows 上使用 LeRobot MUST 遵循文档中的 conda 安装路径，安装 `ffmpeg=7.1.1` 和 `av`；
依赖 `consolidate()` 写视频前 MUST 验证 `libsvtav1` 编码器。Windows 代码 MUST NOT 整体
import `lerobot.scripts.lerobot_record` 等 Linux-only 采集脚本；只能使用 Windows 兼容模块
或复制必要工具逻辑。`LeRobotDataset.create()` MUST 使用 `"user/name"` 格式的 `repo_id`
和独立的本地 `root` 路径。

后端指南中的当前硬件默认值在被新验收 spec 取代前具有约束力：Card 0 为右臂，Card 1 为
左臂；右臂 Roll 使用物理轴 8；Yaw 软件限位 MUST 保持在文档行程内；力觉采集先采用
NI-DAQmx，再考虑任何 UDP RDT 备选；PICO-4 视觉配置与 Omega.7、OpenXR 关注点分离。

## 开发流程与质量门禁

编码前，每个 feature plan MUST 写明假设、必要时说明被拒绝的更简单方案，并定义具体可验证
目标。凡影响硬件安全、数据 schema、单位、文件布局或用户可见契约的歧义，MUST 在实现前澄清。

改动 MUST 外科手术式执行。只有当编辑直接支持用户请求或 constitution 要求的一致性更新时，
才可以修改代码、模板和文档。MUST NOT 顺手重构相邻可工作的代码、重命名既有概念，或在没有
明确请求时删除无关遗留代码。

Spec MUST 包含可独立测试的用户故事、后端/前端/HAL 边界影响、安全影响、数据和单位契约、
可观测性要求，以及硬件可用性假设。Plan MUST 在 research/design 前通过 Constitution Check，
并在生成 tasks 前再次检查。Tasks MUST 在功能触及契约、安全、schema 或集成时包含相应验证工作。

## Governance

本宪章优先于 AppStation 后端、HAL、硬件接入、数据集、策略和 Spec Kit 工作流中的冲突性开发
指导。`cc_data_collection/AppStation_后端开发指南_for_AI_Agent_v1.1.md` 是当前技术规则的
来源上下文；未来该指南如改变原则、边界、安全规则或质量门禁，MUST 触发宪章审查。

修订宪章 MUST 记录理由、语义化版本决策，并同步审查 `.specify/templates/plan-template.md`、
`.specify/templates/spec-template.md`、`.specify/templates/tasks-template.md`、存在时的 command
模板，以及相关运行时指导文档。Major 版本用于删除或重新定义原则且产生不兼容治理变化。Minor
版本用于新增原则、章节或实质扩展强制指导。Patch 版本用于不改变合规义务的措辞澄清。

每个 feature plan、spec、task list、code review 和 implementation summary MUST 说明改动如何
满足适用原则，或在 plan 的 Complexity Tracking 中识别并证明例外合理性。未证明的违规会阻塞实现。

**Version**: 1.0.1 | **Ratified**: 2026-05-11 | **Last Amended**: 2026-05-11
