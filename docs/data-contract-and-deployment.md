# 数值通道契约与部署说明

## 数值通道

当前契约为 `appstation.dual_arm.operator_sides.v2`，`sideOrder` 为 `operator_left_then_operator_right`。定义、校验和转换集中在 `backend/core/data_contract.py`。

| 边界 | 顺序及含义 |
| --- | --- |
| HAL、原始遥测、前端内部状态 | 硬件左 6 轴在前，硬件右 6 轴在后 |
| LeRobot state/action | 操作者左（硬件右）6 轴及夹爪在前，操作者右（硬件左）6 轴及夹爪在后，共 14 维 |
| LeRobot pulses | 操作者左（硬件右）6 轴在前，操作者右（硬件左）6 轴在后，共 12 维 |
| LeRobot force_left/right | 使用数据集/操作者侧名称，force_left 来自硬件右传感器 |
| 原点、kinematics、串口/卡号、HAL force sides | 保持真实硬件侧命名，不机械交换标定对象 |
| 相机 | global、wrist_left、wrist_right 保持原角色，不随数值通道交换 |

平移为 μm，界面旋转为 degree，LeRobot 旋转为 mdeg（degree × 1000），夹爪为 mm，力值为 SI。脉冲先按对应硬件侧的标定和原点换算，再转换为数据集顺序。

`/api/policy/observation` 返回操作者侧 state、pulses、force 和完整 `dataContract`。`/api/policy/action` 要求携带匹配的完整 `dataContract`；`controlledSides` 表示操作者侧，backend 在向 HAL 发指令前转回硬件侧。缺少或不匹配契约返回 409，不发送动作。

通用 `PolicyService.manual_axis_move` 仍使用硬件侧，不能再次交换。外部模型程序应发送操作者侧数组，不能自行增加左右交换。现有控制租约、急停代际、限位和最终执行检查继续生效。

## native 数据兼容

新 native 数据集将完整契约写入 `meta/appstation_info.json` 与 `meta/info.json`。已识别的 native 旧集缺少、未知或不匹配契约时，禁止直接续录；不能仅凭 shape 相同认定左右语义兼容。

本轮没有自动迁移用户旧数据。人工迁移前需保留原目录，同时核对 state、action、pulses、force 和相关元信息，相机角色不变。`scripts/normalize_origin.py` 根据声明的契约处理脉冲和原点，默认 dry-run；只有显式 `--apply` 才写入。

本轮按用户指定范围迁入远端已有实现：native 续录、Policy action 和归一化有契约检查。legacy 数值预览、上传等入口的额外检查，以及部分读取拒绝的 API 错误提示，未作为本轮新增修复。上传契约中的要求是后续提交约束，不代表这些历史入口已经全部覆盖。

## HKVL 与能力诊断

默认来源继续为 `hkvl_serial`，保留用户显式选择的 `nidaq` 和 NI 通道/标定参数，不做自动故障切换。源码默认不覆盖其他机器的已保存配置。

HAL health 使用版本 `hal-real/0.2`，通过 HTTP/DDS 返回实际 `capabilities`；旧程序没有字段时，Python 客户端保留 `None`。backend `/api/health` 的 `hal.capabilityCheck` 展示校准状态能力诊断，不增加录制/运动入口的强制版本门控。

**本轮未迁入双阶段 Tare、校准状态机和 ACK 自检门控。** 因此本地原生能力列表为空，不声明 `force_calibration_state_v1`；诊断会显示该能力缺失。仅更新版本号或重新编译不能证明已经实现该能力。既有 Tare、ACK、控制租约和安全代际逻辑保持原实现。

## 相机识别和曝光

视觉设置页可以扫描腕相机候选、查看预览并选择两个不同设备后保存。后端绑定接口负责持久化，前端同步已保存状态并刷新相机流，不重复提交整份配置。录制启动、活跃或写线程收尾时拒绝识别/绑定。

本轮保留本机相机默认和已保存绑定，不导入远端针对作者设备路径的自动迁移。Windows 自动曝光优先级设置用于尝试禁止暗光动态降帧，不能替代实际 FPS 测量；设备是否支持该属性需要实机验证。

旧配置迁移仅在没有自定义身份时按历史标签处理。显式绑定的稳定身份优先，不能因为相机重新枚举后的 index 恰好与旧默认相同，就在保存或重载时被默认身份覆盖。

## 构建与部署

修改 HAL 源码、头文件或协议后，应从同一份源码构建配套的 `HalServer.exe` 和 `JodellGripperWorker.exe`。若修改后端 native Fast-DDS 源，还需重建对应 DLL。

先在隔离目录完成构建和测试，再按现场流程部署候选文件；不覆盖运行中的程序。没有 SDK 的开发机可以显式关闭 `APPSTATION_ENABLE_VENDOR_SDKS` 和 `APPSTATION_ENABLE_DDS` 做骨架编译，但产物不作为实机发布程序，也不能声称验证了设备或 DDS。

本机 VS2022 为 Professional；现有脚本中的 Community 路径和现场 Fast-DDS 路径需在部署时核实。本轮未迁入远端启动器时间戳判断或启停行为变化，未部署生产 HAL。

外部 `act_deploy.py` 不在仓库。部署前需确认其固定版本、依赖、参数协议及完整 `dataContract` 传递方式，不能假设旧外部程序自动兼容新接口。此次未修改或运行外部程序。

## 本轮记录

2026-09-18 的整合范围、实际验证、备份和未覆盖项见 `frontend/output/ui-redesign/SESSION_SUMMARY.md` 第十三节。离线通过不等于实机采样、运动或录制已验证。
