# E2EAPP_MicroMani 数据契约与部署说明

## 数值通道契约

当前版本为 `appstation.dual_arm.operator_sides.v2`，`sideOrder` 为
`operator_left_then_operator_right`。

- 硬件/HAL 的 12 维数组：硬件左 6 轴、硬件右 6 轴；`left/right` 永远表示实际硬件侧。
- 前端遥测和内部原始采样：保持硬件侧顺序。
- LeRobot `observation.state`、`action`、`observation.pulses`：操作者左（硬件右）在前，操作者右（硬件左）在后；14 维夹爪槽位分别是操作者左、操作者右。
- LeRobot `observation.force_left/right`：字段名是数据集/操作者侧，不是硬件侧；因此数据集 `force_left` 来自硬件右传感器。
- 平移单位为 μm，旋转单位为 LeRobot mdeg（`degree * 1000`），夹爪为 mm；脉冲换算使用对应硬件侧的 signed pulse-per-unit。
- 原点、kinematics、HAL force `sides` 和串口/卡号元数据仍以硬件侧命名。归一化时只将硬件侧原点按契约映射到数据集顺序。
- 相机角色独立于数值侧别：`observation.images.global`、`wrist_left`、`wrist_right` 不因数值转换而交换。

唯一的策略边界转换在 backend：`/api/policy/observation` 输出数据集顺序，`/api/policy/action` 按数据集顺序解释并在发 HAL 前映射到硬件侧。`controlledSides` 表示操作者/数据集侧；HAL 收到的 `side` 表示硬件侧。急停、限位、力锁存、新鲜度和动作幅度保护仍在原有安全边界执行。

## 数据集兼容策略

新建 native 数据集会在 `meta/appstation_info.json` 和 `meta/info.json` 写入完整 `dataContract`，包括版本、侧别顺序、state/action/pulses/force 通道顺序和单位。读取、续录和归一化会校验它。缺少标记、标记不完整或未知版本的数据集不会直接 resume，也不会被脚本猜测成旧/新顺序；会提示人工迁移或确认。

本次没有提供自动迁移旧数据。迁移前应复制原目录，并同时处理 `observation.state`、`action`、`observation.pulses`、`observation.force_left/right` 及侧别元信息；相机字段保持原角色。`scripts/normalize_origin.py` 默认只 dry-run，只有明确传入 `--apply` 才会重写 state/action，验证时不得对真实数据集使用 `--apply`。

## HKVL 默认与 HAL 配套

新配置、缺少 `force.source` 的配置、前端初始显示和 HAL `ForceRuntimeConfig` 默认均为 `hkvl_serial`。显式保存的 `nidaq` 选择及其 NI 参数仍会保留；不会自动切换备用硬件。HKVL 状态缺失/不健康时只报告错误或 stale，不启动 NI 采样，也不伪装成正常反馈。

本次需要重新构建并部署：

1. HAL `HalServer.exe`（包含 force calibration capability `force_calibration_state_v1`）。
2. HAL `JodellGripperWorker.exe`，与同一份 `appstation_hal_core` 配套。
3. 若 backend native Fast-DDS 源有变更，重新构建 `backend/native/appstation_fastdds_transport.dll`。

`hal/CMakeLists.txt` 和 `hal/build_hal.cmd` 已列出当前新增的 HAL 源文件。现场有 Fast-DDS SDK 时，建议在仓库外隔离目录执行构建，例如 `cmake -S hal -B <isolated-build> -DAPPSTATION_ENABLE_DDS=ON -DAPPSTATION_ENABLE_VENDOR_SDKS=OFF -DAPPSTATION_FASTDDS_ROOT=F:/opt/ros/jazzy`，再执行 `cmake --build <isolated-build> --config Release`；该过程不启动设备。完成编译/测试后，把候选程序复制为 `hal/build/HalServer.next.exe` 和 `hal/build/JodellGripperWorker.next.exe`；启动脚本只按 hash 提升候选文件，不会自动编译。`scripts/start-hal.ps1` 会在启动前检查源码是否新于已部署二进制，并提示重新构建；它不会绕过前端 calibration 自检。

启动前应确认 `/health` 报告所需 capability；缺失时先停止使用旧 HAL，重新构建并替换候选文件。本文档和测试不代表真实 HAL、串口、相机或运动设备已经验证。

## ACT 外部部署程序

仓库只包含 `scripts/run-act-deploy.ps1` / `run-act-jepa-deploy.ps1`，不包含它们调用的 `act_deploy.py`。脚本通过 `-DeployDir` 指向仓库外目录，并传递 checkpoint、camera ids、backend URL、`controlled_sides`、可选 `hardware_sides`、安全上限、平滑和 `--send` 等参数。

目前无法从本仓库验证 `act_deploy.py` 所属仓库地址、固定提交或其对 `controlled_sides/hardware_sides` 的实现；部署前必须提供并固定外部项目的仓库 URL、commit/tag、Python 依赖锁定和参数协议。不能假设外部程序已经完成左右转换。backend 是模型通道到硬件侧的唯一转换层，外部程序应发送数据集/操作者顺序，不应再交换 state/action 或相机角色。

## 离线验证记录

使用 `pytest` 执行 backend 相关测试，使用 frontend 的 Vitest/typecheck，并在仓库外临时目录用 MSVC `/c` 编译全部 `hal/src/*.cpp`（启用 Fast-DDS 头文件、关闭 vendor SDK 调用）。验证只覆盖源码、测试 fixture 和模拟/Test HAL；实机范围（真实 HAL 进程、串口、相机、运动设备、外部 `act_deploy.py`）未验证。
