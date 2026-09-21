# 采集、模型与数据回放的位置语义

核对日期：2026-09-21。依据当前仓库源码描述现有实现；不代表已部署二进制或实机验收结果。

## 1. 核心结论

数据集 `observation.state` 与 `action` 的运动分量均使用**以工作原点为零点的位置坐标**。`action` 是这个坐标系里的目标位置，不是需要逐帧累加的位移。

“相对工作原点的位置”和“相对当前位置的运动增量”是两种不同概念。代码注释里的“action 记录绝对目标”，指前者坐标系内的定点目标，不是控制卡原始绝对脉冲。

| 环节/字段 | 位置语义 | 参考点 |
| --- | --- | --- |
| HAL 原始 `pulses` | 控制卡位置计数 | 控制卡当前坐标零点 |
| 数据集 `observation.pulses` | 原始脉冲，只交换左右顺序 | 不减工作原点 |
| 数据集 `observation.state` | 实测位置减工作原点后换算 | 配置中的工作原点 |
| 数据集 `action` | state 加所选遥操作增量得到的目标位置 | 与 state 相同的工作原点 |
| Policy API 输入/输出 | state 与目标 action 共用数据集坐标系 | 当前工作原点 |
| Policy 向 HAL 发出的运动量 | action 减当前 state，再限幅 | 增量；HAL 可基于已有目标续推 |
| 回放向 HAL 发出的运动量 | 数据集目标加回工作原点 | 控制卡坐标系中的绝对目标 |
| 手动单轴步进 | 指定一次相对位移 | 当前控制卡位置 |

这里的“绝对”仅指相应坐标系，不代表世界坐标、相机坐标或外部测量得到的末端位姿。

## 2. 顺序与单位

契约版本：`appstation.dual_arm.operator_sides.v2`。

- 14 维 state/action：`[操作者左 X,Y,Z,Roll,Pitch,Yaw,夹爪, 操作者右 X,Y,Z,Roll,Pitch,Yaw,夹爪]`。
- 操作者左对应硬件右，操作者右对应硬件左。HAL 原始遥测保持硬件左、硬件右顺序，数据边界负责交换。
- state/action：平移 μm，旋转 mdeg（0.001 degree），夹爪开口 mm。
- UI/HAL 运动接口：平移 μm，旋转 degree。旋转从数据集发往 HAL 时除以 1000。
- 脉冲转换使用带符号的标定系数；平移系数为 pulse/mm，旋转系数为 pulse/degree，不能忽略方向符号。
- 夹爪 state 是实际开口，action 是目标开口，不减运动工作原点。

来源：[数据契约](../backend/core/data_contract.py)、[单位转换](../backend/core/units.py)。

## 3. 数据采集

设某轴原始脉冲为 `P`，录制工作原点为 `O`，带符号标定系数为 `K`。运动分量的记录公式为：

```text
observation.pulses = P                 # 存储时转换为操作者侧顺序
observation.state = (P - O) / K × 1000 # 平移得到 μm，旋转得到 mdeg
action = observation.state + Δteleop  # Δteleop 已转换为相同单位和顺序
```

`_recording_motion_positions()` 先减原点，再由 `_compose_observation_state()` 交换侧顺序、转换旋转单位并插入夹爪反馈。原始 pulses 保留，便于追溯与重新转换。

`_latest_action_vector()` 以当前帧 state 为基底，加上 `_latest_action_delta_vector()` 返回的遥操作增量。时间对齐时选择目标时间之前、仍在新鲜度窗口内的动作：优先每侧最近一条，否则全局最近一条；不是把 episode 开始以来所有增量累计。没有合格增量时，运动 action 等于当前 state。夹爪目标优先取 native teleop 目标；非真实 native 路径可取配置目标。

因此，记录的 action 是按上述公式构造的监督目标，不能直接称作下一帧实际位置，也不能保证等于 HAL 最终经过死区、限幅、软限位和目标窗口处理后的目标。

Episode 保存 `motionOrigin`、`motionCalibration`，数据集元信息保存 `sessionOrigin`。原点来自配置，不是录制时自动把第一帧置零。转换函数对无效原点的侧会保留传入位置，因此解释旧数据时必须检查原点元信息，不能仅凭字段名认定已相对化。

参与采集配置会将未参与的 state/action 分量置零，并保存相应 mask；这些零不能解释为未参与设备真实处于原点。原始 pulses 仍用于追溯。

来源：[dataset_recorder.py](../backend/services/dataset_recorder.py)：帧组装、`_recording_motion_positions`、`_compose_observation_state`、`_latest_action_vector`、`_teleop_actions_for_target`。

## 4. 模型训练与推理接口

### 4.1 本仓库可确认的训练数据契约

录制器向 LeRobot 数据集写入 14 维 `observation.state` 和 14 维 `action`。如果训练直接使用这些字段作为输入和监督标签，学到的 action 语义应是**工作原点坐标系内的目标位置**，不是 `action - state`，也不是控制卡绝对脉冲。

本仓库的部署脚本调用外部工程的 `act_deploy.py`；本次没有核实外部训练源码、checkpoint 配置和预处理器。因此不能断言实际训练是否另做 delta-action 转换、统计标准化、动作分块或其他变换。若外部训练做过这些变换，推理输出必须先还原为本 API 要求的物理单位和目标位置语义。

统计归一化（例如减均值、除标准差）与减工作原点是不同操作。不能把标准化后的数值直接当作 μm/mdeg/mm 发给 API。

来源：[部署入口](../scripts/run-act-deploy.ps1)、[ACT/JEPA 部署入口](../scripts/run-act-jepa-deploy.ps1)。

### 4.2 策略在线执行

`GET /api/policy/observation` 在真实硬件路径读取位置和脉冲，用工作原点生成相对位置，再通过 `lerobot_state_from_ui()` 生成上述 14 维 state。

`POST /api/policy/action` 接收同一坐标系下的目标 action，校验数据契约，通过 `build_policy_action_plan()` 计算：

```text
平移增量（μm） = clamp(action - current_state)
旋转增量（degree） = clamp((action - current_state) / 1000)
夹爪目标（mm） = 当前开口 + 限幅后的目标差，再限制到行程内
```

其中 current_state 来自当时的 telemetry；action 路由不是用数据集某一帧 state 计算差值。只有实际发送路径才将运动增量通过 `motion.teleop_target_update` 发往 HAL；默认 `dryRun=true` 仅返回计划。

```text
/api/policy/action
  → build_policy_action_plan：目标位置变为限幅增量
  → motion.teleop_target_update
  → MotionExecutor::applyExternal(..., absoluteTarget=false)
  → LTDMCDriver::updateTeleopTargetUi(..., absoluteTarget=false)
```

HAL 增量模式在已有活动目标、且未反向时可基于缓存目标续推，否则基于实际位置。因此策略执行不是绕过跟随逻辑、直接把模型目标作为控制卡绝对目标写入；它也不调用 `moveRelativeUi()`。

来源：[Policy API](../backend/app.py)、[policy_bridge.py](../backend/services/policy_bridge.py)、[MotionExecutor.cpp](../hal/src/MotionExecutor.cpp)。另有 `PolicyService` 手动动作队列，仅支持 `manual_axis_move`，不要与 14 维 Policy action 接口混淆。

## 5. 数据回放

`DatasetReplayService` 会检查参与侧的当前工作原点与录制原点一致，并检查标定一致；当前实现不允许通过任意更换工作原点直接平移回放轨迹。

`_targets()` 先将数据集侧顺序还原成硬件侧，再执行：

```text
HAL 绝对目标 UI = 数据集目标转换为 UI 单位 + 工作原点脉冲转换为 UI 单位
等价的目标脉冲 = O + 数据集目标 / 1000 × K
```

第二行适用于运动分量，旋转目标使用 mdeg，平移目标使用 μm。夹爪保持目标开口 mm，不加原点。目标还会进行软限位及夹爪范围校验。

执行先对齐第一帧 `observation.state`，再逐帧发送 `action`；末尾等待机械臂最后目标到位；夹爪不到位仅提示，连续回放不以夹爪位置差停机或等待。跟踪反馈会减回工作原点并转为数据集顺序，以便与记录目标比较。实际回放包含反馈检查和等待，不应理解为无条件按原始时间戳发送后立即完成。

```text
DatasetReplayService._send()
  → motion.replay_absolute_target
  → HalCommandDispatcher
  → MotionExecutor::applyExternal(..., absoluteTarget=true)
  → LTDMCDriver::updateTeleopTargetUi(..., absoluteTarget=true)
  → updateTeleopTargetBestEffort()
  → dmc_update_target_position(card, axisNo, targetPulse, 1)
```

HAL 的绝对模式先计算 `目标脉冲 - 实际脉冲`，再经过死区、步长限制、软限位、目标领先窗口及取整。该模式以实际位置为基底，不把重复发送的同一个绝对目标继续累加到旧目标上。一次调用的实际目标仍可能因上述限制尚未达到请求目标。

回放不经过 `moveRelativeUi()`。后者属于 `motion.manual_axis_move` 手动步进路径，最终调用 `dmc_pmove(card, axisNo, deltaPulse, 0)`。

来源：[dataset_replay.py](../backend/services/dataset_replay.py)、[命令分发](../hal/src/HalCommandDispatcher.cpp)、[LTDMCDriver.cpp](../hal/src/LTDMCDriver.cpp)、[回放说明](dataset-replay.md)。

## 6. 一个数值例子

假设某平移轴 `K = 1000 pulse/mm`、工作原点 `O = 10000 pulse`、当前 `P = 10200 pulse`，所选遥操作增量是 `+50 μm`：

| 项目 | 数值 |
| --- | --- |
| 记录的原始 pulses | 10200 pulse |
| 记录的 state | 200 μm |
| 记录的 action | 250 μm |
| 策略执行时当前 state 仍为 200 μm，且限幅不生效 | 向 HAL 发 +50 μm 增量 |
| 回放该 action | 请求控制卡坐标 10250 μm，即 10250 pulse |

重复回放 `action=250` 的请求目标仍是 10250 pulse，不是每次再前进 250 μm。旋转同理，但 `action=500` 表示 0.5 degree，不是 500 degree。

## 7. 原点迁移与边界

`scripts/normalize_origin.py` 是显式离线迁移工具，不是每次训练或回放都会自动执行的步骤。它用原始 pulses 和所选新原点重建 state，并同步平移 action：

```text
new_action = old_action + new_state - old_state
```

这样保持 `action - state` 不变，原始 pulses 和夹爪分量不因运动原点变更而改写。脚本默认 dry-run，需显式 `--apply` 才写入。此处“normalize origin”是坐标原点转换，不是模型统计标准化。

来源：[normalize_origin.py](../scripts/normalize_origin.py)。

本文仅做源码核对与文档检查，没有运行训练、启动设备或执行实机回放；外部模型的预处理/反归一化是否遵守本契约仍需在具体模型工程中确认。
