# Feature Specification: 数据收集完善

**Feature Branch**: `001-data-collection-spec`  
**Created**: 2026-05-12  
**Status**: Draft  
**Input**: User description: "现在需要完善数据收集功能, 帮我根据硬件信息和 cc_data_collection\backend\services\dataset_recorder.py 给出现有的 spec"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 录制可复核的双臂数据 (Priority: P1)

操作员在完成硬件预检后启动一次采集会话，系统持续采集双臂位姿、动作、夹爪、双路力觉和三路相机数据，并在保存 episode 后给出可用于复核的数据记录。

**Why this priority**: 这是数据收集功能的核心价值；没有稳定、完整、可复核的 episode，就无法进行后续数据集管理、模型推理或微调。

**Independent Test**: 使用 Mock HAL/Mock camera 或真实硬件完成一次短采集，保存 episode 后检查该 episode 有帧数、任务描述、12 轴状态、12 维动作、双路 6 维力觉、力觉时间窗口、夹爪状态和三路相机数据或可识别的相机缺失记录。

**Acceptance Scenarios**:

1. **Given** 操作员已完成录制预检且未存在活动采集会话，**When** 操作员输入数据集名称和任务说明并开始采集，**Then** 系统进入 recording 状态并显示会话、episode 序号、帧数、采样率和数据格式。
2. **Given** 系统正在采集 episode，**When** 操作员点击保存，**Then** 系统停止当前 episode 写入，返回 episode 元数据、质量指标和可用于复核的样本。
3. **Given** 保存后的质量报告显示本条数据不可接受，**When** 操作员选择重录，**Then** 系统撤回或标记上一条 episode，并以相同 episode 序号重新开始采集。

---

### User Story 2 - 复核和管理已采集数据集 (Priority: P2)

数据审核人员在数据集页面查看所有本地数据集、episode 列表、质量摘要和抽样数据，能够重命名数据集、标记或删除 episode，并确认数据是否适合保留。

**Why this priority**: 数据收集完成后必须能快速发现掉帧、力觉异常、样本缺失和命名错误，否则低质量数据会进入后续训练或策略评估。

**Independent Test**: 在已有至少一个数据集和一个 episode 的情况下，打开数据集列表，确认每条 episode 显示质量、帧数、时长、相机掉帧、最大力值和抽样轨迹；执行重命名、标记无效和删除操作后刷新列表，状态保持一致。

**Acceptance Scenarios**:

1. **Given** 本地存在多个数据集，**When** 审核人员打开数据集页面，**Then** 系统按更新时间展示数据集，并显示格式、帧率、episode 数量和每条 episode 的复核摘要。
2. **Given** 某条 episode 存在相机掉帧或 late frames，**When** 审核人员查看其质量信息，**Then** 系统明确列出 warning、掉帧计数和最大力觉值。
3. **Given** 审核人员将 episode 标记为无效或删除，**When** 再次查看数据集，**Then** 无效或删除结果不会被误认为可用训练数据。

---

### User Story 3 - 在真实硬件和无硬件环境下保持一致流程 (Priority: P3)

开发者或现场工程师在没有完整硬件、缺少 LeRobot 原生依赖或部分相机不可用时，仍能使用相同的录制流程验证数据契约；在真实硬件可用时，系统自动采集高质量原始数据。

**Why this priority**: 数据收集链路跨越运动控制、相机、力觉和数据集格式。Mock-first 路径能支持开发验证，真实硬件路径能支持现场采集，两者必须共享用户流程和数据语义。

**Independent Test**: 分别在无真实硬件的测试模式和真实硬件模式下启动采集会话，确认会话状态、episode 元数据、数据集列表和复核入口一致；差异仅体现在采集源、图像来源和数据格式。

**Acceptance Scenarios**:

1. **Given** LeRobot 原生数据集能力不可用，**When** 操作员保存 episode，**Then** 系统仍保存兼容的数据记录并允许在数据集页面复核。
2. **Given** LeRobot 原生数据集能力可用，**When** 操作员保存 episode，**Then** 系统保存原生数据集记录，并能读取抽样帧图像用于复核。
3. **Given** 某一路相机截图失败，**When** 系统保存当前 episode，**Then** episode 记录相机掉帧 warning，其他传感器数据继续保存且质量报告反映缺失。

---

### User Story 4 - 用 HAL 状态约束采集可信度 (Priority: P4)

现场工程师在真实硬件采集前查看 HAL 健康、运动状态和 Omega.7 主手状态，确认采集数据来自正确的运动控制卡、语义轴、原始脉冲和主手输入；当 HAL 未就绪或急停激活时，系统仍能保护采集流程并暴露数据可信度风险。

**Why this priority**: 数据集中的双臂状态和动作必须来自受控硬件边界。HAL 状态若不可信，采集出的 episode 即使文件完整，也不能作为高质量训练数据。

**Independent Test**: 启动 HAL 骨架或真实 HAL 后读取健康、运动状态和主手状态，确认采集会话记录的 12 轴状态、脉冲、enabled、estop 和主手按钮/位姿状态能在质量报告或复核视图中定位到可信来源；在 HAL 未初始化、急停或部分 Omega.7 连接时，系统显示明确 warning。

**Acceptance Scenarios**:

1. **Given** HAL 报告 LTDMC 不可用，**When** 操作员尝试真实硬件采集，**Then** 系统不把该 episode 标记为完全可信，并显示运动状态来源降级说明。
2. **Given** HAL 报告 estop_active 为 true，**When** 录制流程仍在运行，**Then** 系统记录急停状态并阻止任何采集流程掩盖该安全状态。
3. **Given** 仅一台 Omega.7 成功连接，**When** 操作员查看采集状态，**Then** 系统显示部分连接 warning，并在动作来源中区分可用与不可用主手。

### Edge Cases

- 活动采集会话已存在时再次创建会话：系统拒绝新会话并保持当前采集不被覆盖。
- 操作员在 episode 采集中结束会话：系统丢弃未保存的当前 episode，并保留已保存 episode。
- 数据集名称包含空格、中文或特殊字符：系统生成安全的数据集标识，同时保留可读显示名称。
- 数据集目录中已有同名数据集：系统不得覆盖已有数据，应复用既有数据集或创建可区分的新数据集。
- HAL 即时位置暂时不可用：系统使用最近遥测状态作为短时兜底，并在质量指标中暴露风险。
- 真实硬件模式下力觉窗口采样失败：系统保留最近力觉值作为兜底，并记录 warning 或质量下降。
- 相机帧缺失、late frames 或采样循环恢复：系统继续采集可用通道，并将异常计入 episode 质量。
- 删除数据集或 episode 时目标路径异常：系统必须拒绝删除数据根目录之外的内容。
- 原生数据集 metadata 可读但 AppStation episode 索引缺失：系统仍应从原生 metadata 恢复可见 episode 列表。
- HAL 只以骨架模式运行且 vendor SDK 不可用：系统必须明确标记真实硬件调用不可用，不得把骨架状态误报为真机验证通过。
- HAL motion state 中某个轴 enabled 为 false 或 moving 状态异常：系统必须在复核或质量信息中暴露该轴状态，便于排查。
- Omega.7 读取失败、按钮状态缺失或夹爪开口不可用：系统必须继续保存可用采集数据，并把主手状态标记为不可完全可信。
- 手动 jog 或回工作原点操作与采集同时发生：采集数据必须保留动作和运动状态变化，质量报告应暴露可能影响数据一致性的运动事件。

## Requirements *(mandatory)*

### Functional Requirements

#### A. 录制会话与 episode 流程

- **FR-001**: 系统 MUST 支持创建一个录制会话，并记录数据集名称、任务说明、会话标识、起始 episode 序号、目标帧率、力觉采样率和当前数据格式。
- **FR-002**: 系统 MUST 在同一时间只允许一个活动录制会话，重复创建会话时必须返回明确错误且不得覆盖当前数据。
- **FR-003**: 系统 MUST 以 30 Hz 为默认目标采样节奏采集 episode，并允许从配置读取 1-60 Hz 范围内的录制帧率。
- **FR-004**: 每个采集帧 MUST 包含 12 轴状态、12 轴原始脉冲、双路 6 维力觉、双路力觉时间窗口、力觉窗口时间偏移、2 维夹爪状态、12 维动作、任务说明、时间戳和三路相机数据引用或图像数组。
- **FR-005**: 12 轴状态 MUST 使用固定语义顺序：左臂 X/Y/Z/Roll/Pitch/Yaw 后接右臂 X/Y/Z/Roll/Pitch/Yaw。
- **FR-006**: 采集数据中的平移状态 MUST 使用微米，旋转状态 MUST 使用 0.001 度；前端复核显示中的旋转值 MUST 转回度。
- **FR-007**: 力觉数据 MUST 使用 N 和 Nm，不得在后端持久化 mN 或 mNm 显示单位。
- **FR-008**: 系统 MUST 在保存 episode 后返回质量信息，至少包含帧数、时长、late frames、三路相机掉帧、左右最大力值和 warning 列表。
- **FR-009**: 系统 MUST 支持保存、重录、跳过复位和结束会话四个录制流程动作，并保持与前端录制状态机一致。
- **FR-010**: 重录当前 episode 时，系统 MUST 删除 fallback 格式下的未采集或已保存文件，或在原生格式下将上一条已保存 episode 标记为不可用，避免误用于训练。
- **FR-011**: 系统 MUST 在采集期间更新实时遥测中的 recording、episodeCount 和 frameCount，以便前端能显示当前录制进度。

#### B. HAL 运动状态与硬件边界

- **FR-012**: 系统 MUST 把 HAL 作为 LTDMC 运动控制卡和 Omega.7 主手的唯一硬件边界；Python Backend 和前端不得直接调用对应 vendor SDK。
- **FR-013**: 系统 MUST 从 HAL 健康状态中区分 LTDMC 可用性、Omega.7 可用性、版本、运行时长和错误信息。
- **FR-014**: 系统 MUST 从 HAL motion state 中读取 12 轴 UI 位置、12 轴原始脉冲、12 轴 enabled 状态和 estop_active 状态，并把它们作为录制可信度依据。
- **FR-015**: HAL motion state 的 12 轴顺序 MUST 与采集帧状态顺序一致，且只暴露语义轴顺序，不得要求上层业务读取物理轴号。
- **FR-016**: HAL MUST 固定左右臂物理轴映射：左臂 X/Y/Z/Roll/Pitch/Yaw 为 0/1/3/5/4/2，右臂为 2/0/5/8/1/7。
- **FR-017**: HAL MUST 固定脉冲当量和方向符号，并向上层提供已换算的 UI 位置和原始脉冲；采集数据必须保留这两类值以便复核。
- **FR-018**: HAL MUST 使用 `dmc_get_position` 语义的内部脉冲计数作为原始位置来源，不得用外部编码器读数替代当前平台定义。
- **FR-019**: HAL 急停状态 MUST 进入采集质量信息；estop_active 为 true 的 episode 不得被默认为高质量样本。
- **FR-020**: HAL 手动 jog MUST 受单步限制约束：平移单步不得超过 5000 微米，旋转单步不得超过 2 度；Yaw 目标不得超过 ±7.5 度安全限制。
- **FR-021**: HAL 回工作原点、单侧使能、单侧回零和手动轴移动发生在采集期间时，系统 MUST 能在日志或质量信息中追踪对应运动事件。
- **FR-022**: HAL 在 vendor SDK 不可用、DLL 缺失、控制卡少于 2 张或必要导出缺失时，系统 MUST 明确标记真实硬件不可用，而不是静默进入真机采集。

#### C. Omega.7 主手与动作来源

- **FR-023**: 系统 MUST 支持记录左右 Omega.7 主手连接状态、openId、deviceId、序列号、系统名、左右手属性、位姿、离合按钮、夹爪按钮、夹爪开口和最近读取错误。
- **FR-024**: 当仅一台 Omega.7 可用或主手状态读取失败时，系统 MUST 保留可用数据并把动作来源标记为部分可信或不可用。
- **FR-025**: 主手位姿前三维 MUST 作为位置输入，后三维 MUST 作为角度输入；夹爪开口显示单位 MUST 与下游夹爪记录单位区分。
- **FR-026**: 系统 MUST 不把 Omega.7 校准状态误报为已完成；校准状态未知或不可读时，采集预检和质量信息必须显示风险。

#### D. 相机、力觉与夹爪数据

- **FR-027**: 系统 MUST 支持三路相机：global、wrist_left、wrist_right，并在 episode 中分别保留图像数据或相机掉帧计数。
- **FR-028**: 系统 MUST 明确区分传感器物理能力分辨率、实际采集分辨率、预览分辨率和数据集保存分辨率。
- **FR-029**: 系统 MUST 支持双 Nano-17 力觉数据，默认硬件事实为左侧 Dev5/ai0:5、右侧 Dev3/ai0:5、200 Hz 起步采样、Fx/Fy/Fz/Mx/My/Mz 顺序。
- **FR-030**: 系统 MUST 根据力觉采样率和录制帧率为每个录制帧保存一段力觉窗口；当显式配置窗口样本数时，必须使用配置值并限制在可复核范围内。
- **FR-031**: 夹爪状态 MUST 以左右开口值进入采集帧；当夹爪数据不可用时，系统必须保留占位值并在质量或日志中暴露来源风险。

#### E. 数据集复核、格式与文件安全

- **FR-032**: 系统 MUST 支持列出本地数据集，并为每个数据集展示标识、显示名称、状态、根路径、帧率、创建/更新时间、格式和可见 episode 列表。
- **FR-033**: 系统 MUST 支持创建、重命名、保存复核状态、统计、划分、清理、导出或推送数据集；当外部推送未启用时，必须明确告知本地数据已就绪但未推送。
- **FR-034**: 系统 MUST 支持对 episode 进行重命名、状态标记和删除，并确保删除或隐藏后的 episode 不再作为可见可用样本出现。
- **FR-035**: 系统 MUST 支持从 fallback 数据记录或原生数据集 metadata 中恢复 episode 抽样数据，用于展示双臂轨迹、力觉和图像预览。
- **FR-036**: 系统 MUST 支持在 LeRobot 原生能力可用时写入原生数据集；不可用时 MUST 自动保留兼容 fallback 格式，且前端录制和复核流程不变。
- **FR-037**: 原生数据集路径 MUST 使用本地根目录和合法 repo 标识，不得把本地路径当作 repo 标识。
- **FR-038**: 系统 MUST 在真实硬件模式下优先读取 HAL 即时运动位置和脉冲；HAL 暂时不可用时允许使用遥测缓存作为短时兜底。
- **FR-039**: 系统 MUST 按工作原点配置将原始脉冲换算为相对 UI 状态；左右臂原点有效性必须可分别处理。
- **FR-040**: 系统 MUST 将采集、保存、丢弃、结束、恢复、HAL 健康、主手状态和异常写入结构化日志，日志通道至少覆盖 LEROBOT、CAMERA、FORCE、SAFETY、HAL 或 BACKEND 中的适用项。
- **FR-041**: 系统 MUST 防止数据集删除、episode 文件删除或临时文件清理越过配置的数据集根目录。
- **FR-042**: 系统 MUST 支持真实硬件缺失时的 Mock HAL/Mock camera 验证路径，且该路径能生成可复核的最小 episode。

### AppStation 宪章要求 *(后端/HAL/硬件/数据功能 mandatory)*

- **AC-001**：受影响的前端契约包括 `/api/record/session/create`、`/api/record/episode/save`、`/api/record/episode/discard`、`/api/record/session/finish`、`/api/record/reset/skip`、`/api/record/status`、`/api/datasets`、`/api/datasets/{dataset_id}`、`/api/datasets/{dataset_id}/episodes/{episode_id}`、`/api/datasets/{dataset_id}/file`、`/api/datasets/{dataset_id}/frame_image`、HAL 代理状态入口，以及 `/ws` 中的 recording、episodeCount、frameCount、jointPositions、forceLeft、forceRight、gripperPositions、cameras、HAL 健康和主手状态。兼容规则是：现有录制和数据集页面无需改变用户流程即可使用完善后的数据收集能力。
- **AC-002**：录制状态机、数据集索引、力觉窗口、相机截图汇聚、LeRobot/fallback 写入和复核数据读取属于 Python Backend；LTDMC 运动控制、Omega.7 SDK 读取、语义轴到物理轴映射、脉冲到 UI 位置换算、急停和 jog 限制属于 C++ HAL；前端只消费状态和发起用户命令；WSL2 PolicyServer 不属于本功能范围。
- **AC-003**：本功能不新增任意自动运动能力，但采集期间必须尊重急停、watchdog、力觉安全、HAL 健康、轴 enabled 状态和主手连接状态；采集不得屏蔽已有急停，真实硬件异常必须通过日志和质量指标暴露。
- **AC-004**：数据形状为 12 轴状态、12 轴脉冲、12 维动作、双路 6 维力觉、双路力觉窗口、2 维夹爪、三路相机、HAL health、motion enabled/estop 和 Omega.7 双主手状态。平移单位为微米，旋转存储单位为 0.001 度，前端显示为度，力/力矩为 N/Nm。持久化配置字段包含数据集根目录、录制帧率、相机分辨率、力觉采样率、力觉窗口样本数、HAL 地址、工作原点、软限位和主手 openId。
- **AC-005**：核心契约验收可使用 HAL 骨架、Mock HAL 或 Mock camera；真实硬件验收必须覆盖 HAL health、motion state、Omega.7 state、三相机截图、NI-DAQmx 力觉采样和 episode 质量报告。

### Key Entities *(include if feature involves data)*

- **录制会话**：一次数据采集活动，包含会话标识、数据集名称、任务说明、当前 episode 序号、采样参数、活动状态和数据格式。
- **Episode**：一次可复核的采集样本，包含帧数、时长、任务、状态、质量指标、数据路径或原生索引范围、删除标记和 warning。
- **采集帧**：episode 内的单帧记录，包含时间戳、双臂状态、脉冲、动作、力觉、夹爪和相机数据。
- **数据集**：本地可管理的数据集合，包含显示名称、稳定标识、格式、帧率、状态、更新时间、episode 列表和统计摘要。
- **质量报告**：保存 episode 后生成的复核信息，包含帧数、掉帧、late frames、最大力觉、时长、warning 和是否建议保留。
- **硬件采集源**：为采集帧提供数据的 HAL、相机、力觉、夹爪和遥操作源；每个源可能处于真实、Mock、降级或不可用状态。
- **HAL 健康状态**：真实硬件边界的可用性摘要，包含 LTDMC 可用性、Omega.7 可用性、版本、运行时长和错误信息。
- **HAL 运动状态**：由 HAL 输出的 12 轴实时状态，包含语义轴 UI 位置、原始脉冲、enabled、moving 和 estop_active。
- **Omega.7 主手状态**：左右主手输入来源，包含连接、openId、deviceId、序列号、位姿、按钮、夹爪开口、读取状态和错误信息。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 操作员在完成预检后，能够在 10 秒内启动一次采集会话并看到 recording、episode 序号和帧数开始更新。
- **SC-002**: 在 30 Hz 目标采样下，一条 20 秒 episode 保存后至少包含 95% 的目标帧数，且 late frames 和相机掉帧被准确计入质量报告。
- **SC-003**: 保存 episode 后，审核人员能够在 5 秒内看到该 episode 的帧数、时长、最大力觉、相机掉帧和抽样轨迹。
- **SC-004**: 对每条已保存 episode，系统能提供不少于 1 个可复核样本；当 episode 超过 300 帧时，抽样数量不超过 300 以保持页面可用。
- **SC-005**: 在 LeRobot 原生能力不可用时，系统仍能完成一次采集、保存、列出、复核和结束会话流程，且不要求用户改变操作步骤。
- **SC-006**: 在真实硬件模式下，三路相机任一路失败不会导致整条采集流程崩溃；失败通道必须在质量报告中显示为掉帧或 warning。
- **SC-007**: 所有保存的力觉值在复核、统计和持久化数据中保持 N/Nm 语义，抽检 10 帧不得发现显示单位被写入后端数据。
- **SC-008**: 重录或删除 episode 后，数据集可见列表中 100% 不再把对应 episode 当作可用样本展示。
- **SC-009**: 真实硬件采集前，现场工程师能在 5 秒内判断 LTDMC、Omega.7、estop 和 12 轴 enabled 状态是否允许高可信采集。
- **SC-010**: 抽检任意 10 帧真实硬件采集数据，12 轴 UI 位置和原始脉冲都能追溯到同一 HAL motion state 语义顺序。
- **SC-011**: 当 HAL vendor SDK 不可用、LTDMC 未初始化或仅一台 Omega.7 连接时，100% 的相关 episode 质量信息包含明确 warning。

## Assumptions

- 数据收集完善的目标阶段对应后端指南中的 P2：相机、力觉与录制闭环；PICO-4、夹爪深度控制、PolicyServer 自动执行和微调任务管理不作为本 feature 的核心范围。
- 当前前端录制状态机保持不变：预检后开始 session，保存后展示质量报告，接受后进入 resetting，重录调用 discard。
- 本地数据集根目录由 settings 配置，默认可在当前工作站持久化；网络上传或远端 Hub 推送不是默认成功路径。
- 真实硬件采集优先使用 HAL 即时状态、OpenCV/DirectShow 三路相机和 NI-DAQmx 力觉；硬件缺失时使用 Mock 或 fallback 路径完成契约验证。
- 数据收集时的动作向量来自最近遥操作动作；若最近 1 秒内无动作，则记录零动作向量。
- 本功能不改变已有安全策略，只要求采集流程不得绕过或隐藏安全状态。
- HAL 当前可在无 vendor SDK 时作为确定性骨架构建，但骨架只用于契约验证；真实硬件验收必须使用已加载 LTDMC 与 Force Dimension SDK 的 HalServer。
- HAL 内部接口运行在本机硬件边界内；前端和数据集 spec 只依赖其外部健康、运动和主手状态语义，不依赖 HAL 内部代码结构。
