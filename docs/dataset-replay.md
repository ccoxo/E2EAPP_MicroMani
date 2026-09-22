# 本地示教数据真机回放测试

入口：数据集 → 选择本地数据集和 Episode → 详情下方「示教数据真机回放测试」。

1. 核对参与设备；旧片段没有参与元信息时，必须选择操作者左臂、右臂或双臂，并分别勾选需要回放的夹爪。选择倍率后点击「校验回放数据」。这一步只读完整动作文件及当前配置，并显示时间规划，不使能、不运动。
2. 停止示教遥操作、录制会话和自动策略。保留浏览器控制连接及有效控制租约，按现有流程完成力自检与安全确认。
3. 倍率支持 0.1、0.25、0.5、1，默认 0.25；改变倍率须重新校验。确认工作区可运动并勾选运动确认。
4. 点击「对齐起点并真机回放」。参与臂及夹爪先移动到首帧 observation.state，再按轨迹时间规划逐帧下发 action；末帧等待机械臂到位后停止参与臂连续目标；夹爪不到位仅显示提示。
5. 「停止真机回放」使用硬件急停并锁存。检查设备并重新确认安全后才能再启动；不自动恢复中断片段。

## 数据与执行边界

- 仅支持本地 native LeRobot Parquet 数据。读取完整片段，不能使用预览抽样或补零数据。
- 必须包含完整 `appstation.dual_arm.operator_sides.v2` 契约、连续 frame_index/timestamp、有限的 14 维 action 和 observation.state。
- Episode 必须有 motionOrigin 和 motionCalibration；当前原点脉冲及运动标定必须与录制一致。缺少元信息的历史数据会拒绝执行，不自动猜测或迁移。
- 动作是相对工作原点的绝对目标。backend 统一转换操作者侧到硬件侧、mdeg 到 degree，再叠加原点偏置；原点与标定自身不交换。
- HAL 新命令 `motion.replay_absolute_target` 接收硬件侧绝对 UI 坐标，沿用执行器互斥、单帧脉冲限幅、目标领先限制、软限位、力安全、急停代际与控制租约。它不累加旧目标，不改变原有 `motion.teleop_target_update` 增量语义。
- backend 必须读到 HAL capability `replay_absolute_target_v1`，旧 HAL 不能执行回放。
- `gripper.prepare_replay` 在运动前独立启动夹爪采样；后续 `gripper.replay_target` 复用 worker 队列，不逐帧关闭并重新打开串口。
- 仅参与回放的夹爪必须启用，反馈有效且最近成功采样不超过 1 秒。新增 positionOk/positionSampleTs 区分命令应答与位置采样。目标必须符合当前行程和夹缝保护。
- 整个任务独占后台运动资源并锁定原点修改。运动、遥操作、夹爪或力配置变化、陈旧反馈、租约丢失、执行异常及明显跟踪偏差都会中止，不重试运动命令。

## 当前测试限制

这是保守的本地回放测试工具，未承诺任意录制速度均能被当前执行链跟随。

- 起点对齐容差：平移 10 μm，旋转 0.05°，夹爪 0.2 mm；等待最多 30 秒。末帧机械臂保持上述到位要求；夹爪偏差超过 0.2 mm 仅通过 warning 字段、面板和日志提示，不等待、不判定夹取成败。
- 每段计划时间结束，相对该段动作目标的最大跟踪偏差：平移 500 μm，旋转 1°。连续回放不因夹爪位置差停机或等待，trackingError 仍保留夹爪实际误差。段内尚未要求到达终点，检查反馈是否超出本段起止位置范围加上述偏差；诊断 `targetKind=segment.bounds` 时的目标是被越过的范围边界。
- 示教与回放通过 `backend/core/motion_profile.py` 共用 `teleop` 的起始速度、最大速度和加减速时间。移除原回放额外 1000 μm/s、1°/s 上限；HAL 原有执行限幅继续生效。当前配置为 8000 μm/s、12°/s、加减速各 0.05 秒，属于软件配置，未有逐轴、负载实机验收依据。
- 按单调时钟定时，每段成功下发后独立计时，下一段从当前段完成后开始；不赶时间、不跳帧。每次等待与反馈读取的意外迟到超过 max(250 ms, 两个请求帧间隔) 就停止。计划延时和有界跟随等待不算意外迟到。
- 状态接口返回当前帧、阶段、跟踪误差和失败原因。断连会由现有控制看门狗撤权、请求急停，HAL 租约仍是最终执行侧保护。

## 接口

- `POST /api/datasets/{dataset_id}/episodes/{episode_id}/replay/inspect`：只读校验，接受 `speed`（默认 0.25）及 `participation`，返回 `timing`。
- `POST /api/datasets/{dataset_id}/episodes/{episode_id}/replay/start`：`{"speed":0.25,"confirmMotion":true}`，必须携带有效 `X-Control-Session`。
- `GET /api/replay/status`：全局回放任务状态。
- `POST /api/replay/stop`：停止；停止不依赖当前页面所选片段。

## 构建、部署与验证

本轮使用隔离目录 `hal/build/replay-validation` 成对构建带 SDK/Fast-DDS 的 `HalServer.next.exe` 和 `JodellGripperWorker.next.exe`。未启动候选程序、未驱动设备。构建脚本副本省略了正式程序覆盖步骤。

启动前必须配套部署二者并重启后端。标准 `Start-App.cmd` 调用的 `start-hal.ps1` 会在启动时提升 `hal/build/` 下的 `.next.exe` 候选并备份旧程序；不要只替换其中一个。回退时也需使用匹配的旧程序对及源码。

已验证 Python 回放/Policy/DDS/契约回归、React 交互与控制门闩、类型检查及前端构建；C++ 离线 MotionExecutorTests、ForceCoreTests 及带 SDK 的配套编译。离线浏览器接口全部拦截，检查面板显示与数据校验流程，无页面脚本错误。

未验证：实际 DDS 往返时延、设备轨迹跟随、物理夹爪到位精度、接触状态下的力保护，以及真实数据集的运动验收。不得将上述离线通过表述为真机验收完成。


## 单臂采集与参与侧契约（2026-09-21）

录制页「录制控制」新增「参与设备 · 操作者视角」，至少选择一只臂。夹爪可单独取消选择，选择在会话中锁定；提前连接参与主手、启用参与夹爪并开启夹爪示教映射。回工作原点与下一片段复位只处理参与臂。

`POST /api/record/session/create` 的新增 `participation` 参数示例（操作者左臂，不使用夹爪）：

```json
{"version":"appstation.participation.v1","arms":["left"],"grippers":[]}
```

Episode 扩展元数据保存 `participation`、`actionMask`、`observationMask`，后两项均为 14 维布尔数组，沿用操作者左 7 维、右 7 维顺序。未参与维度写占位零、掩码为 false，不代表测得真实零值；参与设备缺失或过期的反馈会中断片段，不能补零继续。共享相机、全局力安全与急停保持原有检查。

新片段回放使用所保存的参与配置，前端不可改选，后端拒绝与记录不一致的覆盖。旧片段可在 inspect/start 请求中显式携带上述 `participation`，不会改写原始数据。更改选择会清除校验及运动确认。原点、目标与跟踪误差只针对参与维度检查；数据契约、帧连续性、全量 14 维有限数值仍必须有效。

采集的主手连接与输出轴按当前 `swapTeleopChannels` 映射限制；HAL 使用 `leftGripperParticipating`/`rightGripperParticipating` 屏蔽未参与夹爪目标与轮询。回放仅发送参与侧目标，不会自动使能闲置夹爪。安全停止仍可全局作用。

这些掩码保存在 AppStation Episode 元数据中。第三方训练器不会自动读取；训练/策略消费者必须显式使用掩码，不能把占位值当作有效监督或给未参与设备下发目标。本次未改变自动策略执行接口。旧客户端未提供 participation 的录制仍保留旧行为，其片段回放需显式选择参与设备。

本轮候选程序位于 `hal/build/participation-validation/`：已成对构建 `HalServer.exe` 和 `JodellGripperWorker.exe`（SDK/Fast-DDS 开启），没有覆盖正式程序或启动设备。采集及单臂回放要求 HAL 声明 `record_participation_v1` 能力；使用前需配套部署二者并重启后端，旧 HAL 会明确拒绝。

验证：参与契约、左右映射、Episode 元数据落盘、单侧目标隔离、未参与夹爪反馈缺失、旧数据显式选择与重新确认的离线回归；前端类型/构建及隔离浏览器验收。实机采集、DDS 往返与轨迹跟随仍未执行。扩展测试发现 4 项已有失败（2 项相机迁移、2 项既有 API 状态码/租约预期），使用 HEAD 原始模块同样复现，本轮未修改这些预期。


### 页面心跳与执行侧断连保护

页面不再定时应答安全挑战，也不因主线程暂停或 2 秒未收到心跳主动关闭 WebSocket。后端在控制连接存在时独立维护 HAL 租约，首次执行侧确认后下发 `control_lease`（`renewalOwner: backend`）；浏览器实际断开、后端停止或 DDS 控制通道失败仍撤销控制并请求停机。HAL 超时、急停、限位和力安全保持有效。

前后端必须同步更新并刷新页面；新页面会拒绝仍要求旧挑战协议的后端。自动续租不解除安全锁存、不使能、不恢复中断动作。验证使用离线 WebSocket/DDS 替身，覆盖不发送页面心跳、慢页面发送、真实断连、迟到续租回复和执行通道失败；不等同于实机动作验收。

### 运行中跟踪超限诊断

运行检查先更新本次 `trackingError`，再判定是否超过原有阈值。超限时在急停前保存 `trackingFault` 内存快照；急停处理后将同一快照以 `event=replay_tracking_fault` JSON 事件写入后端日志。页面「查看错误详情」自动展开，列出参与机械臂通道的目标、反馈、绝对误差、阈值、时间及超限标记。未参与通道不作为有效测量展示。

`GET /api/replay/status` 的 `trackingFault` 包含数据集、Episode、倍率、帧率和以下信息：

- `targetFrameIndex` / `nextFrameIndex` 为从 0 开始的比较目标帧与尚未下发帧；界面显示从 1 开始。`targetKind` 通常是上一帧 `action`，首次运行检查则是首帧 `observation.state`。`targetTimestampS` 是目标帧在录制数据中的相对时间。
- `targetCommandCompletedAtMs` 是上一帧下发函数完成的后端时间，不等于控制卡实际开始执行时间；首次运行检查为 null。`readStartedAtMs` / `checkedAtMs` 是本次读取反馈的起止时间，均为 Unix 毫秒。
- `channels` 标明操作者侧、硬件侧、通道及显示单位（μm、°）。`sampleTs` 取本次实际读取的 HAL 运动状态 `timestamp_ms`；`sampleAgeMs` 为检查时间减去该时间。无有效来源时间则返回 null、显示「未提供」，不拿后端检查时间冒充采样时间。HAL 运动状态时间不是逐轴独立编码器测量时间，机械臂反馈仍为控制卡脉冲换算值。

快照保留到下一次回放开始，历史记录保存在日志中。此修改不回填旧故障，不调整速度、到位容差、超限阈值或停止保护，也不自动重试真机回放。


### 共用示教参数与回放时间规划（2026-09-21）

按用户确认，先共用当前示教软件配置，明确标记 `profileSource=teleop`、`hardwareValidated=false`；不虚构逐轴速度或负载验收值，不修改设备持久化配置。每次开始重新读取配置并重新规划；执行期间配置变化仍拒绝继续。手动运动的 `motion.leftProfile/rightProfile` 是另外的配置，不作为本次来源。

每个参与运动轴按相邻 action 位移计算时间，首段从首帧 observation.state 开始。设位移 d、最大速度 v、加减速时间和 r：当 d ≥ v·r/2 时，估计 T=d/v+r/2；否则 T=√(2·d·r/v)。这是忽略非零起始速度、按静止到静止估计的保守时间预算，不是已验证的驱动器轨迹模型。旋转 mdeg 统一换算后计算；各参与轴取最慢值，与 1/(fps·请求倍率) 取较大值。保留原始数据及每帧目标，只延长时间。

段内以不大于 50 ms 的等待间隔读取反馈、检查安全状态；实际周期还包括 DDS 和后端处理耗时。HAL 限制目标领先量时，续发已成功应答的同一绝对运动目标，夹爪不重复排队；任何一次命令失败直接终止，不做失败重试。段结束后若误差未超过原有停止阈值，但超过阈值一半（仅机械臂：250 μm / 0.5°），暂缓下一帧，等待最多 2 秒；仍未跟上则保存现场并停机。末帧机械臂仍要求原有严格到位容差，夹爪不到位仅提示。

`timing` 返回请求/计划平均/最慢段倍率、预计动作时长、延时段数及受限通道。预计时间不含起点对齐、通信和额外跟随等待。夹爪速度是设备档位，不能作为 mm/s 计算轨迹时间，夹爪位置差不再延长连续回放，gripperFeedbackLimited 为 false；反馈有效性检查继续保留。`completedFrames` 是完成计划段和跟随检查的帧数；`frame` 是已下发帧数。`elapsedS` 从起点对齐后开始，`effectiveSpeed` 为已完成帧数/fps/运行时间；`feedbackWaitS` 只统计段后额外等待。故障 JSON 及 UI 保留原有诊断和停止路径。

本轮不修改 C++ HAL 或 DDS 契约、不启动设备运动。离线验证不能证明任意负载、接触状态或真实通信延迟下都能跟随；逐轴、负载验收记录仍待实测补齐。
