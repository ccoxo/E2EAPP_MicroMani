# GitHub 上传与后续提交契约

本文档是 `E2EAPP_MicroMani` 的仓库级贡献约束。每个提交和 Pull Request 都必须同时满足本文档、根目录 `AGENTS.md` 和 `docs/data-contract-and-deployment.md`；若描述冲突，以代码中的数据契约常量和安全保护为准。

## 1. 不可变的数据边界

当前数值契约为 `appstation.dual_arm.operator_sides.v2`，顺序为 `operator_left_then_operator_right`。

| 边界 | 数组含义 | 侧别定义 |
| --- | --- | --- |
| HAL、原始遥测、前端内部状态 | 12 维运动、硬件侧顺序 | 硬件左 6 轴 + 硬件右 6 轴 |
| 原点、kinematics、HAL force sides、物理端口/卡号 | 标定和设备元信息 | 始终按真实硬件侧命名 |
| LeRobot `observation.state` / `action` | 14 维状态/目标 | 操作者左（硬件右）+ 操作者右（硬件左），夹爪槽位同样按操作者侧 |
| LeRobot `observation.pulses` | 12 维脉冲 | 操作者左（硬件右）+ 操作者右（硬件左） |
| LeRobot `observation.force_left/right` | 6 维力值 | 字段名是数据集/操作者侧，`force_left` 来自硬件右传感器 |
| 相机字段 | 图像角色 | `global`、`wrist_left`、`wrist_right` 独立于数值侧别，不得交换 |

单位必须保持：平移 μm、旋转 mdeg（degree × 1000）、夹爪 mm、力值 SI。脉冲转 UI 时使用对应真实硬件侧的 signed kinematics，不能因为数组顺序改变而机械交换 origin 或 calibration 对象。

转换规则只有一处：backend 的 policy boundary。`/api/policy/observation` 输出数据集顺序；`/api/policy/action` 只接受带匹配 `dataContract` 的数据集顺序 action，并在发 HAL 前转换为硬件侧。`controlledSides` 是操作者/数据集侧，HAL payload 的 `side` 是硬件侧。任何新消费者必须复用 `backend/core/data_contract.py`，不得自行切片交换。

## 2. 数据集兼容

- 新建 native 数据集必须在 `meta/appstation_info.json` 和 `meta/info.json` 写入完整 `dataContract`，包含 version、side order、state/action/pulses/force order、硬件侧映射和单位。
- 读取、预览、续录、策略消费和原点归一化都必须校验契约。
- 没有顺序标记、标记不完整或未知版本的数据不得猜测为旧版或新版；必须拒绝并给出人工确认/迁移路径。
- 迁移必须默认保留原目录，同时处理 `state`、`action`、`pulses`、`force_left/right` 和相关侧别元信息；相机角色保持不变。
- `scripts/normalize_origin.py` 默认 dry-run。只有明确使用 `--apply` 才能改写，并且不得在测试或验证中指向用户真实数据集。

## 3. 硬件默认与安全边界

- 新配置、缺少 `force.source` 的配置、前端初始值和 HAL 默认统一为 `hkvl_serial`。
- 显式保存的 `nidaq`、NI 通道和 calibration 参数必须保留。
- HKVL 缺失、不健康或 stale 时只报告不可用状态，不启动 NI-DAQ，不伪装成正常反馈。
- 不得削弱急停、限位、力锁存、新鲜度、动作幅度、夹爪启用或来源健康保护来适配旧消费者。

## 4. 原生程序和外部程序

涉及 HAL 源码、头文件、协议或 capability 时，必须配套重新构建并部署：

1. `HalServer.exe`；
2. 与同一份 core 配套的 `JodellGripperWorker.exe`；
3. 若 backend native Fast-DDS binding 发生变化，`appstation_fastdds_transport.dll`。

当前 HAL capability 为 `force_calibration_state_v1`，版本为 `hal-real/0.2`。启动脚本会检查源码时间和候选二进制；不要把自动编译或放宽前端 calibration 自检作为兼容方案。构建必须在隔离目录完成，候选文件使用 `.next.exe`，不得覆盖正在运行的二进制。

仓库不包含外部 `act_deploy.py`。上传前必须在 PR 中写明外部仓库 URL、固定 tag/commit、依赖锁定和参数协议；当前仓库无法验证外部程序是否已经实现 `controlled_sides`、`hardware_sides` 和 `dataContract`。外部程序必须发送数据集/操作者顺序，不能再做左右交换或相机交换。

## 5. GitHub 上传前核对

提交者必须：

1. 先确认当前 branch、HEAD 和工作区状态，保留不属于本次工作的已有改动；
2. 检查未跟踪源码、import、依赖锁文件、构建清单和测试 fixture；
3. 运行受影响 backend 测试、frontend typecheck/Vitest 和隔离 HAL 离线编译；
4. 运行 `git diff --check`，检查没有凭据、runtime 配置、SDK/DLL、模型、二进制或临时产物；
5. 在 PR 中填写实际命令和结果，并单独列出未验证的实机、外部程序和设备范围；
6. 将提交按“源码修复 / 数据兼容 / 部署配套”分组，提交信息说明 what/why，不提交个人工作区目录。

“测试通过”只代表所列测试和离线编译通过，不代表真实 HAL、串口、相机、运动设备或外部 policy 程序已经验证。
