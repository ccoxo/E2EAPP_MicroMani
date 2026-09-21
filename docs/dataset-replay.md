# 本地示教数据真机回放测试

入口：数据集 → 选择本地数据集和 Episode → 详情下方「示教数据真机回放测试」。

1. 点击「校验回放数据」。这一步只读完整动作文件及当前配置，不使能、不运动。
2. 停止示教遥操作、录制会话和自动策略。保留浏览器控制连接及有效控制租约，按现有流程完成力自检与安全确认。
3. 选择倍率（0.1、0.25、0.5、1，默认 0.25），确认工作区可运动并勾选运动确认。
4. 点击「对齐起点并真机回放」。双臂及夹爪先移动到首帧 observation.state，再按数据集帧率乘倍率逐帧下发 action；末帧等待到位后停止双臂连续目标。
5. 「停止真机回放」使用硬件急停并锁存。检查设备并重新确认安全后才能再启动；不自动恢复中断片段。

## 数据与执行边界

- 仅支持本地 native LeRobot Parquet 数据。读取完整片段，不能使用预览抽样或补零数据。
- 必须包含完整 `appstation.dual_arm.operator_sides.v2` 契约、连续 frame_index/timestamp、有限的 14 维 action 和 observation.state。
- Episode 必须有 motionOrigin 和 motionCalibration；当前原点脉冲及运动标定必须与录制一致。缺少元信息的历史数据会拒绝执行，不自动猜测或迁移。
- 动作是相对工作原点的绝对目标。backend 统一转换操作者侧到硬件侧、mdeg 到 degree，再叠加原点偏置；原点与标定自身不交换。
- HAL 新命令 `motion.replay_absolute_target` 接收硬件侧绝对 UI 坐标，沿用执行器互斥、单帧脉冲限幅、目标领先限制、软限位、力安全、急停代际与控制租约。它不累加旧目标，不改变原有 `motion.teleop_target_update` 增量语义。
- backend 必须读到 HAL capability `replay_absolute_target_v1`，旧 HAL 不能执行回放。
- `gripper.prepare_replay` 在运动前独立启动夹爪采样；后续 `gripper.replay_target` 复用 worker 队列，不逐帧关闭并重新打开串口。
- 双侧夹爪必须启用，反馈有效且最近成功采样不超过 1 秒。新增 positionOk/positionSampleTs 区分命令应答与位置采样。目标必须符合当前行程和夹缝保护。
- 整个任务独占后台运动资源并锁定原点修改。运动、遥操作、夹爪或力配置变化、陈旧反馈、租约丢失、执行异常及明显跟踪偏差都会中止，不重试运动命令。

## 当前测试限制

这是保守的本地回放测试工具，未承诺任意录制速度均能被当前执行链跟随。

- 对齐/末帧到位容差：平移 10 μm，旋转 0.05°，夹爪 0.2 mm；等待最多 30 秒。
- 运行中相对上一帧动作的最大跟踪偏差：平移 500 μm，旋转 1°，夹爪 2 mm。
- 平移速度上限取当前配置与 1000 μm/s 的较小值，旋转取当前配置与 1°/s 的较小值；HAL 原有更严格限幅继续生效。
- 按单调时钟定时；落后计划超过 max(250 ms, 两帧间隔) 就停止。不会为了赶时间跳帧，不能以降低倍率消除不正确的数据/标定。
- 状态接口返回当前帧、阶段、跟踪误差和失败原因。断连会由现有控制看门狗撤权、请求急停，HAL 租约仍是最终执行侧保护。

## 接口

- `POST /api/datasets/{dataset_id}/episodes/{episode_id}/replay/inspect`：只读校验。
- `POST /api/datasets/{dataset_id}/episodes/{episode_id}/replay/start`：`{"speed":0.25,"confirmMotion":true}`，必须携带有效 `X-Control-Session`。
- `GET /api/replay/status`：全局回放任务状态。
- `POST /api/replay/stop`：停止；停止不依赖当前页面所选片段。

## 构建、部署与验证

本轮使用隔离目录 `hal/build/replay-validation` 成对构建带 SDK/Fast-DDS 的 `HalServer.next.exe` 和 `JodellGripperWorker.next.exe`。未启动候选程序、未驱动设备。构建脚本副本省略了正式程序覆盖步骤。

启动前必须配套部署二者并重启后端。标准 `Start-App.cmd` 调用的 `start-hal.ps1` 会在启动时提升 `hal/build/` 下的 `.next.exe` 候选并备份旧程序；不要只替换其中一个。回退时也需使用匹配的旧程序对及源码。

已验证 Python 回放/Policy/DDS/契约回归、React 交互与控制门闩、类型检查及前端构建；C++ 离线 MotionExecutorTests、ForceCoreTests 及带 SDK 的配套编译。离线浏览器接口全部拦截，检查面板显示与数据校验流程，无页面脚本错误。

未验证：实际 DDS 往返时延、设备轨迹跟随、物理夹爪到位精度、接触状态下的力保护，以及真实数据集的运动验收。不得将上述离线通过表述为真机验收完成。
