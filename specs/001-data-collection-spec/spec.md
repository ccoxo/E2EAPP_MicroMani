# Feature Specification: 数据收集完善

**Feature Branch**: `001-data-collection-spec`  
**Created**: 2026-05-12  
**Status**: Draft  
**Input**: User description: "现在需要完善数据收集功能, 帮我根据硬件信息和 cc_data_collection\backend\services\dataset_recorder.py 给出现有的 spec"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 录制可复核的双臂数据 (Priority: P1)

操作员在完成硬件预检后启动一次采集会话，系统持续采集双臂位姿、动作、夹爪、双路力觉和三路相机数据，并在保存 episode 后给出可用于复核的数据记录。

**Why this priority**: 这是数据收集功能的核心价值；没有稳定、完整、可复核的 episode，就无法进行后续数据集管理、模型推理或微调。

**Independent Test**: 使用 Mock HAL/Mock camera 或真实硬件完成一次短采集，保存 episode 后检查该 episode 有帧数、任务描述、14 维状态、14 维动作、12 轴脉冲、双路 6 维力觉、力觉时间窗口和三路相机数据；当某路相机当前帧不可用时，该路图像使用最近一次有效缓存帧；当写盘变慢时，采集主循环仍按目标 tick 产生稳定递增的帧序号，并在保存前等待所有已排队帧完成持久化。

**Acceptance Scenarios**:

1. **Given** 操作员已完成录制预检且未存在活动采集会话，**When** 操作员输入数据集名称和任务说明并开始采集，**Then** 系统进入 recording 状态并显示会话、episode 序号、帧数、采样率和数据格式。
2. **Given** 系统正在采集 episode，**When** 操作员点击保存，**Then** 系统停止当前 episode 写入，返回 episode 元数据和可用于复核的样本。
3. **Given** 保存后的数据需要重录，**When** 操作员选择重录，**Then** 系统撤回或标记上一条 episode，并以相同 episode 序号重新开始采集。

---

### User Story 2 - 复核和管理已采集数据集 (Priority: P2)

数据审核人员在数据集页面查看所有本地数据集、episode 列表和抽样数据，能够重命名数据集、标记或删除 episode，并确认数据是否适合保留。

**Why this priority**: 数据收集完成后必须能快速发现样本缺失和命名错误，否则不完整数据会进入后续训练或策略评估。

**Independent Test**: 在已有至少一个数据集和一个 episode 的情况下，打开数据集列表，确认每条 episode 显示帧数、时长、力觉摘要、图像样本和抽样轨迹；执行重命名、标记无效和删除操作后刷新列表，状态保持一致。

**Acceptance Scenarios**:

1. **Given** 本地存在多个数据集，**When** 审核人员打开数据集页面，**Then** 系统按更新时间展示数据集，并显示格式、帧率、episode 数量和每条 episode 的抽样入口。
2. **Given** 某条 episode 已保存，**When** 审核人员查看其数据，**Then** 系统展示帧数、时长、最大力觉值和抽样图像。
3. **Given** 审核人员将 episode 标记为无效或删除，**When** 再次查看数据集，**Then** 无效或删除结果不会被误认为可用训练数据。

---

### User Story 3 - 在真实硬件和无硬件环境下保持一致流程 (Priority: P3)

开发者或现场工程师在没有完整硬件、缺少 LeRobot 原生依赖或部分相机不可用时，仍能使用相同的录制流程验证数据契约；在真实硬件可用时，系统自动采集原始数据。

**Why this priority**: 数据收集链路跨越运动控制、相机、力觉和数据集格式。Mock-first 路径能支持开发验证，真实硬件路径能支持现场采集，两者必须共享用户流程和数据语义。

**Independent Test**: 分别在无真实硬件的测试模式和真实硬件模式下启动采集会话，确认会话状态、episode 元数据、数据集列表和复核入口一致；差异仅体现在采集源、图像来源和数据格式。

**Acceptance Scenarios**:

1. **Given** LeRobot 原生数据集能力不可用，**When** 操作员保存 episode，**Then** 系统仍保存兼容的数据记录并允许在数据集页面复核。
2. **Given** LeRobot 原生数据集能力可用，**When** 操作员保存 episode，**Then** 系统保存原生数据集记录，并能读取抽样帧图像用于复核。
3. **Given** 某一路相机截图失败，**When** 系统保存当前 episode，**Then** 其他传感器数据继续保存，该相机通道直接使用上一帧有效缓存；若启动后尚无缓存，才使用占位帧。

---

### Edge Cases

- 活动采集会话已存在时再次创建会话：系统拒绝新会话并保持当前采集不被覆盖。
- 操作员在 episode 采集中结束会话：系统丢弃未保存的当前 episode，并保留已保存 episode。
- 数据集名称包含空格、中文或特殊字符：系统生成安全的数据集标识，同时保留可读显示名称。
- 数据集目录中已有同名数据集：系统不得覆盖已有数据，应复用既有数据集或创建可区分的新数据集。
- HAL 即时位置暂时不可用：系统使用最近遥测状态作为短时兜底。
- 真实硬件模式下力觉窗口采样失败：系统保留最近力觉值作为兜底。
- 相机当前帧缺失或采样循环恢复：系统直接使用该相机上一帧有效缓存；若无缓存，使用占位帧。
- 删除数据集或 episode 时目标路径异常：系统必须拒绝删除数据根目录之外的内容。
- 原生数据集 metadata 可读但 AppStation episode 索引缺失：系统仍应从原生 metadata 恢复可见 episode 列表。
- HAL 只以骨架模式运行且 vendor SDK 不可用：系统必须明确标记真实硬件调用不可用，不得把骨架状态误报为真机验证通过。
- HAL motion state 中某个轴 enabled 为 false 或 moving 状态异常：系统必须保留对应采集帧的状态值，便于排查。
- Omega.7 读取失败、按钮状态缺失或夹爪开口不可用：系统必须继续保存可用采集数据，并把主手状态标记为不可完全可信。
- 手动 jog 或回工作原点操作与采集同时发生：采集数据必须保留动作和运动状态变化。
- 写盘耗时超过单个 30 Hz 采集周期：系统必须让采集主循环继续按目标 tick 排队帧，写盘延迟不得直接变成采集 tick 抖动。
- 待写入队列达到容量上限：系统必须阻塞新的帧入队而不是丢弃帧，并记录 writer 背压帧数用于复核。
- 操作员保存 episode 时仍存在已排队未写入帧：系统必须先等待这些帧全部写入，再生成 episode 元数据和保存结果。
- 写入端消费速度慢于采集速度：系统必须保持帧序号和时间戳顺序，不得因为已写入帧数落后而重复分配同一个帧序号。

## Requirements *(mandatory)*

### Functional Requirements

#### A. 录制会话与 episode 流程

- **FR-001**: 系统 MUST 支持创建一个录制会话，并记录数据集名称、任务说明、会话标识、起始 episode 序号、目标帧率、力觉采样率和当前数据格式。
- **FR-002**: 系统 MUST 在同一时间只允许一个活动录制会话，重复创建会话时必须返回明确错误且不得覆盖当前数据。
- **FR-003**: 系统 MUST 以 30 Hz 为默认目标采样节奏采集 episode，并允许从配置读取 1-60 Hz 范围内的录制帧率。
- **FR-004**: 每个采集帧 MUST 包含 14 维状态、14 维动作、12 轴原始脉冲、双路 6 维力觉、双路力觉时间窗口、力觉窗口时间偏移、任务说明、时间戳和三路相机数据引用或图像数组。
- **FR-005**: 14 维状态 MUST 使用固定语义顺序：左臂 X/Y/Z/Roll/Pitch/Yaw/夹爪实际开口 后接右臂 X/Y/Z/Roll/Pitch/Yaw/夹爪实际开口。
- **FR-006**: 采集数据中的平移状态 MUST 使用微米，旋转状态 MUST 使用 0.001 度，夹爪开口和夹爪目标 MUST 使用毫米；前端复核显示中的旋转值 MUST 转回度。
- **FR-007**: 力觉数据 MUST 使用 N 和 Nm，不得在后端持久化 mN 或 mNm 显示单位。
- **FR-008**: 系统 MUST 在保存 episode 后返回基础保存信息，至少包含帧数、时长、三路相机可用性和左右最大力值。
- **FR-009**: 系统 MUST 支持保存、重录、跳过复位和结束会话四个录制流程动作，并保持与前端录制状态机一致。
- **FR-010**: 重录当前 episode 时，系统 MUST 删除 fallback 格式下的未采集或已保存文件，或在原生格式下将上一条已保存 episode 标记为不可用，避免误用于训练。
- **FR-011**: 系统 MUST 在采集期间更新实时遥测中的 recording、episodeCount 和 frameCount，以便前端能显示当前录制进度。

#### B. HAL 运动状态与硬件边界

- **FR-012**: 系统 MUST 把 HAL 作为 LTDMC 运动控制卡和 Omega.7 主手的唯一硬件边界；Python Backend 和前端不得直接调用对应 vendor SDK。
- **FR-014**: 系统 MUST 从 HAL motion state 中读取 12 轴 UI 位置、12 轴原始脉冲、12 轴 enabled 状态和 estop_active 状态，并把它们作为录制可信度依据。
- **FR-015**: HAL motion state 的 12 轴顺序 MUST 与采集帧状态顺序一致，且只暴露语义轴顺序，不得要求上层业务读取物理轴号。
- **FR-016**: HAL MUST 固定左右臂物理轴映射：左臂 X/Y/Z/Roll/Pitch/Yaw 为 0/1/3/5/4/2，右臂为 2/0/5/8/1/7。
- **FR-017**: HAL MUST 固定脉冲当量和方向符号，并向上层提供已换算的 UI 位置和原始脉冲；采集数据必须保留这两类值以便复核。
- **FR-018**: HAL MUST 使用 `dmc_get_position` 语义的内部脉冲计数作为原始位置来源，不得用外部编码器读数替代当前平台定义。
- **FR-020**: HAL 手动 jog MUST 受单步限制约束：平移单步不得超过 5000 微米，旋转单步不得超过 2 度；Yaw 目标不得超过 ±7.5 度安全限制。
- **FR-021**: HAL 回工作原点、单侧使能、单侧回零和手动轴移动发生在采集期间时，系统 MUST 保留对应运动状态变化。
- **FR-022**: HAL 在 vendor SDK 不可用、DLL 缺失、控制卡少于 2 张或必要导出缺失时，系统 MUST 明确标记真实硬件不可用，而不是静默进入真机采集。

#### C. Omega.7 主手与动作来源

- **FR-023**: 系统 MUST 支持记录左右 Omega.7 主手连接状态、openId、deviceId、序列号、系统名、左右手属性、位姿、离合按钮、夹爪按钮、夹爪开口和最近读取错误。
- **FR-024**: 当仅一台 Omega.7 可用或主手状态读取失败时，系统 MUST 保留可用数据并把动作来源标记为部分可信或不可用。
- **FR-025**: 主手位姿前三维 MUST 作为位置输入，后三维 MUST 作为角度输入；夹爪开口显示单位 MUST 与下游夹爪记录单位区分。
- **FR-026**: 系统 MUST 不把 Omega.7 校准状态误报为已完成。

#### D. 相机、力觉与夹爪数据

- **FR-027**: 系统 MUST 支持三路相机：global、wrist_left、wrist_right，并在 episode 中分别保留图像数据；当任一路相机当前帧不可用时，系统 MUST 直接使用该相机上一帧有效缓存，只有启动后尚无缓存时才允许使用占位帧。
- **FR-028**: 系统 MUST 明确区分传感器物理能力分辨率、实际采集分辨率、预览分辨率和数据集保存分辨率。
- **FR-029**: 系统 MUST 支持双 Nano-17 力觉数据，默认硬件事实为左侧 Dev5/ai0:5、右侧 Dev3/ai0:5、200 Hz 起步采样、Fx/Fy/Fz/Mx/My/Mz 顺序。
- **FR-030**: 系统 MUST 根据力觉采样率和录制帧率为每个录制帧保存一段力觉窗口；当显式配置窗口样本数时，必须使用配置值并限制在 1-512 个样本范围内。
- **FR-031**: 从手夹爪实际开口 MUST 进入 `observation.state` 的左右夹爪维度；夹爪控制目标 MUST 进入 `action` 的左右夹爪目标维度；当夹爪数据不可用时，系统必须保留占位值。

#### E. 数据集复核、格式与文件安全

- **FR-032**: 系统 MUST 支持列出本地数据集，并为每个数据集展示标识、显示名称、状态、根路径、帧率、创建/更新时间、格式和可见 episode 列表。
- **FR-033**: 系统 MUST 支持创建、重命名、保存复核状态、统计、划分、清理、导出或推送数据集；当外部推送未启用时，必须明确告知本地数据已就绪但未推送。
- **FR-034**: 系统 MUST 支持对 episode 进行重命名、状态标记和删除，并确保删除或隐藏后的 episode 不再作为可见可用样本出现。
- **FR-035**: 系统 MUST 支持从 fallback 数据记录或原生数据集 metadata 中恢复 episode 抽样数据，用于展示双臂轨迹、力觉和图像预览。
- **FR-036**: 系统 MUST 支持在 LeRobot 原生能力可用时写入原生数据集；不可用时 MUST 自动保留兼容 fallback 格式，且前端录制和复核流程不变。
- **FR-037**: 原生数据集路径 MUST 使用本地根目录和合法 repo 标识，不得把本地路径当作 repo 标识。
- **FR-038**: 系统 MUST 在真实硬件模式下优先读取 HAL 即时运动位置和脉冲；HAL 暂时不可用时允许使用遥测缓存作为短时兜底。
- **FR-039**: 系统 MUST 按工作原点配置将原始脉冲换算为相对 UI 状态；左右臂原点有效性必须可分别处理。
- **FR-040**: 系统 MUST 将采集、保存、丢弃、结束和恢复写入结构化日志，日志通道至少覆盖 LEROBOT、CAMERA、FORCE、SAFETY、HAL 或 BACKEND 中的适用项。
- **FR-041**: 系统 MUST 防止数据集删除、episode 文件删除或临时文件清理越过配置的数据集根目录。
- **FR-042**: 系统 MUST 支持真实硬件缺失时的 Mock HAL/Mock camera 验证路径，且该路径能生成可复核的最小 episode。

#### F. 多源数据时间对齐

当前差距：历史 `_collect_frame()` 路径按顺序读取 HAL、力觉窗口和相机数据；只有三路相机截图内部并行。仅凭这种顺序采集不能证明 30 Hz 录制和 HAL/相机 skew 阈值，必须把多源采集约束为同一 tick 并发启动并按来源设置 timeout。

- **FR-043**: 系统 MUST 以录制主轴驱动 episode 采集；默认主轴为 30 Hz monotonic tick，每个 tick 都必须拥有稳定的帧序号、目标时间和实际采集时间。
- **FR-044**: 系统 MUST 将 HAL motion state、动作、夹爪、力觉窗口和三路相机数据对齐到同一个录制 tick；任何数据源不得仅凭写入顺序被视为已对齐。
- **FR-045**: HAL motion state 与录制 tick 的时间偏差 SHOULD 不超过 10 ms；当任意 HAL 样本偏差超过 20 ms 时，系统 SHOULD 继续保留该帧的采集时间。
- **FR-046**: 每路相机帧与录制 tick 的时间偏差 SHOULD 不超过 16.7 ms；当触发 FR-027 的缓存或占位兜底时，系统 MUST 记录该相机通道的 timeout/cache/placeholder 状态，不得因此阻塞当前采集帧。
- **FR-047**: 力觉窗口 MUST 覆盖当前录制 tick 前最近一帧周期；在 30 Hz 默认录制下，该窗口必须覆盖 tick 前约 33.3 ms 的最近高频样本，并保留每个窗口样本相对于当前 tick 的时间偏移。
- **FR-050**: 系统 MUST 在同一录制 tick 内并发启动 HAL motion state、力觉窗口、夹爪缓存、Omega 状态和三路相机采集，并对每个来源设置 per-source timeout；任何来源超时或失败时，不得阻塞其他来源写入当前帧。
- **FR-050A**: 仅三路相机内部并行或按写入顺序汇总不得被视为满足多源时间对齐要求；系统 MUST 基于各来源采样时间、完成时间和 timeout 结果判断该帧是否满足对齐契约。

#### G. LeRobot v3 数据结构标准

- LeRobot Dataset v3 示例中的 `so_follower` 是单臂数据，仅作为 metadata/features 结构示范；本功能 MUST 只保留与双臂微装配硬件相关的必要语义字段。
- **FR-051**: 标准数据集 metadata MUST 声明 `codebase_version` 为 `v3.0`、`robot_type` 为 `dual_arm_micro_assembly`，默认 `fps` 为 30；`total_episodes`、`total_frames`、`total_tasks`、`chunks_size`、文件大小、`splits`、`data_path` 和 `video_path` 等 LeRobot 全局统计或路径字段可由 LeRobot 数据集库生成和维护，但不属于 AppStation 手写硬件 feature 契约。
- **FR-052**: 标准 `features.observation.state` MUST 为 `float32`、shape `[14]`，名称顺序必须为 `left_x_um`、`left_y_um`、`left_z_um`、`left_roll_mdeg`、`left_pitch_mdeg`、`left_yaw_mdeg`、`left_gripper_gap_mm`、`right_x_um`、`right_y_um`、`right_z_um`、`right_roll_mdeg`、`right_pitch_mdeg`、`right_yaw_mdeg`、`right_gripper_gap_mm`。
- **FR-053**: 标准 `features.action` MUST 为 `float32`、shape `[14]`，名称顺序必须为 `left_dx_um`、`left_dy_um`、`left_dz_um`、`left_droll_mdeg`、`left_dpitch_mdeg`、`left_dyaw_mdeg`、`left_gripper_target_mm`、`right_dx_um`、`right_dy_um`、`right_dz_um`、`right_droll_mdeg`、`right_dpitch_mdeg`、`right_dyaw_mdeg`、`right_gripper_target_mm`。
- **FR-054**: 标准 `features.observation.pulses` MUST 为 `float32`、shape `[12]`，名称顺序必须为左右臂 X/Y/Z/Roll/Pitch/Yaw 的原始脉冲字段，且不得包含夹爪维度。
- **FR-055**: 标准 `features.observation.force_left` 和 `features.observation.force_right` MUST 为 `float32`、shape `[6]`，名称顺序必须为 `fx`、`fy`、`fz`、`mx`、`my`、`mz`。
- **FR-056**: 标准三路相机 feature MUST 为 `observation.images.global`、`observation.images.wrist_left`、`observation.images.wrist_right`，dtype 必须为 `video`，shape 必须为 `[480, 640, 3]`，names 必须为 `height`、`width`、`channels`；视频 `info` 必须包含 height、width、codec、pix_fmt、is_depth_map、fps、channels 和 has_audio 等必要描述。
- **FR-057**: LeRobot 自动帧索引字段 `timestamp`、`frame_index`、`episode_index`、`index` 和 `task_index` 可作为标准数据集索引字段存在，但不得被当作机器人观测、动作、力觉或相机硬件语义字段。
- **FR-058**: `observation.gripper` 不得作为标准训练主字段替代 14 维 `observation.state` 中的夹爪维度；如需保留旧字段，只能作为兼容、调试或迁移用途，并且不得改变标准 features 的 shape 和字段顺序。

#### H. 采集与写盘解耦

当前差距：历史录制路径在采集循环内完成组帧、三路图片写入、记录写入、刷新和帧数更新；写盘抖动会直接阻塞 30 Hz 主循环，并放大 lateFrames。保存 episode 前也必须明确区分“已排队帧”和“已写入帧”，否则写入端慢于采集端时可能产生重复 frame_index 或不一致 metadata。

- **FR-059**: 系统 MUST 将 30 Hz 采集主循环与磁盘持久化解耦；采集主循环完成组帧后必须把待写入帧交给写入队列，不得在同一 tick 内同步等待图片写入、记录写入或 flush。
- **FR-060**: 每个待写入帧 MUST 包含 episode 序号、稳定递增的 frame_index、采集 timestamp 和完整帧数据；frame_index 必须由采集主循环分配，且不得依赖写入端已完成帧数。
- **FR-061**: 系统 MUST 为每个活动录制会话只运行一个写入消费者，并按 frame_index 顺序串行写入待写入帧，避免原生数据集写入和 fallback 文件写入并发破坏顺序。
- **FR-062**: 写入队列 MUST 设置容量上限，默认上限为 120 个待写入帧；队列满时系统 MUST 阻塞新的帧入队而不是丢帧，并记录 `writerBackpressureFrames`。
- **FR-063**: 系统 MUST 分别维护已排队帧数和已写入帧数；实时录制进度可展示已排队帧数，保存结果和 episode metadata 必须使用已写入帧数。
- **FR-064**: 操作员保存 episode 时，系统 MUST 先停止继续排队新帧，再等待写入队列中的当前 episode 帧全部完成，之后才能生成 episode metadata、返回保存结果或允许下一条 episode 开始。
- **FR-065**: 写入端 MUST 在单个待写入帧失败时保留可诊断错误信息，并确保队列任务状态被正确结算，避免保存流程永久等待。

### AppStation 宪章要求 *(后端/HAL/硬件/数据功能 mandatory)*

- **AC-001**：受影响的前端契约包括 `/api/record/session/create`、`/api/record/episode/save`、`/api/record/episode/discard`、`/api/record/session/finish`、`/api/record/reset/skip`、`/api/record/status`、`/api/datasets`、`/api/datasets/{dataset_id}`、`/api/datasets/{dataset_id}/episodes/{episode_id}`、`/api/datasets/{dataset_id}/file`、`/api/datasets/{dataset_id}/frame_image`，以及 `/ws` 中的 recording、episodeCount、frameCount、jointPositions、forceLeft、forceRight、gripperPositions 和 cameras。兼容规则是：现有录制和数据集页面无需改变用户流程即可使用完善后的数据收集能力。
- **AC-002**：录制状态机、数据集索引、力觉窗口、相机截图汇聚、LeRobot/fallback 写入和复核数据读取属于 Python Backend；LTDMC 运动控制、Omega.7 SDK 读取、语义轴到物理轴映射、脉冲到 UI 位置换算、急停和 jog 限制属于 C++ HAL；前端只消费状态和发起用户命令；WSL2 PolicyServer 不属于本功能范围。
- **AC-003**：本功能不新增任意自动运动能力，但采集期间必须尊重急停、watchdog、力觉安全、轴 enabled 状态和主手连接状态；采集不得屏蔽已有急停。
- **AC-004**：标准数据形状为 14 维 `observation.state`、14 维 `action`、12 维 `observation.pulses`、双路 6 维力觉和三路视频相机；其中夹爪实际开口进入 `observation.state` 的第 7/14 维，夹爪目标进入 `action` 的第 7/14 维。平移单位为微米，旋转存储单位为 0.001 度，夹爪单位为毫米，前端显示旋转为度，力/力矩为 N/Nm。持久化配置字段包含数据集根目录、录制帧率、相机分辨率、力觉采样率、力觉窗口样本数、HAL 地址、工作原点、软限位和主手 openId。
- **AC-005**：核心契约验收可使用 HAL 骨架、Mock HAL 或 Mock camera；真实硬件验收必须覆盖 motion state、Omega.7 state、三相机截图、NI-DAQmx 力觉采样和 episode 保存结果。

### Key Entities *(include if feature involves data)*

- **录制会话**：一次数据采集活动，包含会话标识、数据集名称、任务说明、当前 episode 序号、采样参数、活动状态和数据格式。
- **Episode**：一次可复核的采集样本，包含帧数、时长、任务、状态、数据路径或原生索引范围和删除标记。
- **采集帧**：episode 内的单帧记录，包含时间戳、14 维双臂状态、14 维动作、12 轴脉冲、双路力觉和三路相机数据；夹爪实际开口属于状态维度，夹爪目标属于动作维度。
- **数据集**：本地可管理的数据集合，包含显示名称、稳定标识、格式、帧率、状态、更新时间、episode 列表和统计摘要。
- **硬件采集源**：为采集帧提供数据的 HAL、相机、力觉、夹爪和遥操作源；每个源可能处于真实、Mock、降级或不可用状态。
- **HAL 运动状态**：由 HAL 输出的 12 轴实时状态，包含语义轴 UI 位置、原始脉冲、enabled、moving 和 estop_active。
- **Omega.7 主手状态**：左右主手输入来源，包含连接、openId、deviceId、序列号、位姿、按钮、夹爪开口、读取状态和错误信息。
- **待写入帧**：采集主循环生成但尚未完成持久化的帧，包含 episode 序号、frame_index、timestamp 和完整帧数据。
- **写入队列状态**：录制期间用于复核写盘健康度的状态，包含队列容量、当前积压、已排队帧数、已写入帧数和 writer 背压帧数。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 操作员在完成预检后，能够在 10 秒内启动一次采集会话并看到 recording、episode 序号和帧数开始更新。
- **SC-002**: 在 30 Hz 目标采样下，一条 20 秒 episode 保存后至少包含 95% 的目标帧数。
- **SC-003**: 保存 episode 后，审核人员能够在 5 秒内看到该 episode 的帧数、时长、最大力觉、三路相机样本和抽样轨迹。
- **SC-004**: 对每条已保存 episode，系统能提供不少于 1 个可复核样本；当 episode 超过 300 帧时，抽样数量不超过 300 以保持页面可用。
- **SC-005**: 在 LeRobot 原生能力不可用时，系统仍能完成一次采集、保存、列出、复核和结束会话流程，且不要求用户改变操作步骤。
- **SC-006**: 在真实硬件模式下，三路相机任一路当前帧失败不会导致整条采集流程崩溃；失败通道必须使用上一帧有效缓存，若无缓存则使用占位帧。
- **SC-007**: 所有保存的力觉值在复核、统计和持久化数据中保持 N/Nm 语义，抽检 10 帧不得发现显示单位被写入后端数据。
- **SC-008**: 重录或删除 episode 后，数据集可见列表中 100% 不再把对应 episode 当作可用样本展示。
- **SC-009**: 真实硬件采集前，现场工程师能在 5 秒内判断 LTDMC、Omega.7、estop 和 12 轴 enabled 状态是否允许高可信采集。
- **SC-010**: 抽检任意 10 帧真实硬件采集数据，12 轴 UI 位置和原始脉冲都能追溯到同一 HAL motion state 语义顺序。
- **SC-011**: 在 30 Hz 目标采样下抽检任意 10 条真实硬件 episode，100% 的标准数据集 metadata 只包含必要训练字段和 LeRobot 索引字段。
- **SC-013**: 抽检任意 10 个新保存的数据集 metadata，100% 的标准 features 都与 LeRobot v3.0 契约一致：`observation.state` 和 `action` 为 14 维，`observation.pulses` 为 12 维，双路力觉为 6 维，三路相机为 `[480, 640, 3]` 视频字段且包含必要 video info；LeRobot 索引字段仅用于 timestamp/frame/episode/task 索引，不混入硬件语义维度。
- **SC-014**: 在模拟每帧写入耗时超过 33 ms 的条件下，一条 20 秒 episode 保存后不得出现重复 frame_index，metadata 帧数必须与实际写入帧数一致。
- **SC-015**: 在写入队列达到容量上限的测试中，系统必须记录 writer 背压帧数，且保存后的 episode 不得因为背压丢失已排队帧。

## Assumptions

- 数据收集完善的目标阶段对应后端指南中的 P2：相机、力觉与录制闭环；PICO-4、夹爪深度控制、PolicyServer 自动执行和微调任务管理不作为本 feature 的核心范围。
- 当前前端录制状态机保持不变：预检后开始 session，保存后进入 resetting，重录调用 discard。
- 本地数据集根目录由 settings 配置，默认可在当前工作站持久化；网络上传或远端 Hub 推送不是默认成功路径。
- 真实硬件采集优先使用 HAL 即时状态、OpenCV/DirectShow 三路相机和 NI-DAQmx 力觉；硬件缺失时使用 Mock 或 fallback 路径完成契约验证。
- 数据收集时的动作向量来自最近遥操作动作；若最近 1 秒内无动作，则记录零动作向量。
- 标准训练数据结构以 LeRobot v3.0 metadata 为准；主手状态和对齐指标默认不进入标准数据集 schema。
- 多源数据对齐以 30 Hz 录制主轴为默认验收基线。
- 写入队列默认容量为 120 帧，约等于 30 Hz 采集下 4 秒积压；该上限用于防止磁盘异常变慢时内存无界增长。
- 写盘背压场景优先保持数据完整性，因此默认阻塞入队而不是丢帧；后续如需“低延迟优先”策略应另行明确。
- 本功能不改变已有安全策略，只要求采集流程不得绕过或隐藏安全状态。
- HAL 当前可在无 vendor SDK 时作为确定性骨架构建，但骨架只用于契约验证；真实硬件验收必须使用已加载 LTDMC 与 Force Dimension SDK 的 HalServer。
- HAL 内部接口运行在本机硬件边界内；前端和数据集 spec 只依赖其外部运动和主手状态语义，不依赖 HAL 内部代码结构。
