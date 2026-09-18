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

2026-09-18 后续迁入 `ccoxo/codex/hal-native-teleop-v2` 的 HKVL 双阶段 Tare、校准状态机和 ACK 自检门控，核对的远端最新提交为 `aabe6734b2ad39945641fe8a53e107c8d3409b76`，功能来源为 `ff74427977d3c80213041bb035e14139b60f1bde`。新的 HAL 源码声明 `force_calibration_state_v1`；运行中的旧二进制仍按其实际能力报告。

设置页“安全链路 / 急停 / 软限位”中的双侧去皮自检要求明确确认两个传感器已卸载，并保持遥操作停止、所有轴停止且伺服禁用。HKVL 只接受双侧自检，NI-DAQ 的单侧去皮保持原路径。自检可在已有急停锁存下执行，控制租约、互斥操作和新急停取消仍然生效。

每侧先采集稳定窗口，再以候选偏置计算残差窗口；各窗口接受 200–1000 个样本，默认 200，每阶段最多等待 2 秒，首末接收批次跨度至少 100 ms。配置中的 `0` 表示默认 200；读取旧 HKVL 配置时只将不支持的样本数迁移为 `0` 并记录日志，保留其他设备参数，新写入则拒绝无效样本数。NI-DAQ 的样本配置保持原规则。两侧全部通过后才一起提交偏置，提交前失败保留原偏置。标准差、峰峰值、残差均值限制分别为力 `0.05 / 0.30 / 0.10 N`，力矩 `0.0025 / 0.015 / 0.005 Nm`，沿用 v2 参数，尚未经本机实机验收。安全判断在整个窗口内继续使用旧偏置，新增超限、失联、线程失败、停止或重配置均阻止旧操作提交。

串口同一读取批次使用同一主机接收时刻；窗口开始前的批次、遗留半帧不能进入新窗口，自检期间超过 50 帧的积压批次拒绝通过。此处约束的是主机接收窗口，协议没有提供传感器采集时间，不能由此证明 USB/串口没有延迟；仍需台架测量实际缓存、速率和双侧时差。Tare 使用单次 6 秒请求预算，不自动重试，HAL 在采样及提交前持续检查请求截止时间。

后端锁存期间允许仅修改 `force` 或重应用当前力配置，包含显式选择 NI-DAQ。恢复要求有效控制会话、停止已确认、12 轴新鲜停稳且断使能，以及录制会话、自动执行与遥操作均停止；混合其他配置仍不能借此绕过锁存。配置恢复与 ACK 互斥，HAL 返回后及落盘前核验停止代际；不以磁盘 IO 锁阻塞急停，也不声称配置文件替换与急停完全原子化。恢复不解除锁存，HKVL 之后仍须重新自检及 ACK。

DDS 普通命令、急停/租约、状态发布分属三个线程；耗时 Tare 不再占用遥测发布线程。后端按 DDS 信封源时间拒绝超过 500 ms 或未来超过 100 ms 的状态，健康缓存也不能延长源有效期。缺少有效运动状态时 WebSocket 的 `halOk` 为 false；缺少力状态时保留最后数值供诊断，但双侧 `healthy` 为 false，不延续旧校准就绪状态。

状态按 `waiting_sensors → checking_stability → taring → validating → ready_for_ack → ready` 转移，失败显示 `failed` 和原因。双侧统计、偏置、完成时间写入 HAL 遥测及现有录制元数据；数值沿用硬件侧与传感器坐标，界面按照既有操作者映射显示。提交后重新等待双侧新鲜样本和安全稳定窗口，再由操作者单独确认安全态；自检不自动恢复伺服。录制预检要求 HKVL 已到 `ready` 且未锁存。

每个 episode 开始时独立冻结 `forceCalibration`，保存来源、配置、硬件侧映射、实际偏置、自检结果及源时间。`meta/episodes.jsonl` 与详情接口返回对应 episode 的快照，续录更新数据集级元数据不会覆盖旧 episode 的校准；缺失状态及历史 episode 不用最新零点回填。录制会话启动、活跃或暂停待保存期间拒绝 Tare，避免同一 episode 中途更换零点。

原生离线回归入口为 `hal/tests/HkvlTareTests.cpp`、`hal/tests/ForceTareRuntimeTests.cpp`，保留 `ForceCoreTests` 和 `WorkerResilienceTests` 的保护回归。此次没有部署生产 HAL，也没有操作传感器或运动设备；运行功能需在配套正式二进制完成构建、部署后验收。

## 相机识别和曝光

视觉设置页可以扫描腕相机候选、查看预览并选择两个不同设备后保存。后端绑定接口负责持久化，前端同步已保存状态并刷新相机流，不重复提交整份配置。录制启动、活跃或写线程收尾时拒绝识别/绑定。

本轮保留本机相机默认和已保存绑定，不导入远端针对作者设备路径的自动迁移。Windows 自动曝光优先级设置用于尝试禁止暗光动态降帧，不能替代实际 FPS 测量；设备是否支持该属性需要实机验证。

2026-09-18 对照 v2 的低光补偿修复 `aabe6734b2ad39945641fe8a53e107c8d3409b76`：本地已包含按 DirectShow DevicePath 定位自动曝光腕相机、关闭低光补偿并回读确认的逻辑，同时保留本地先启用自动曝光、直接采集模式下调整曝光后重新应用该设置的处理。手动曝光和全局相机保持原路径。

同日迁入 v2 最新提交 `0ba726150b6aab93dccb7a2b6fd23fed620a3f1e` 的采集卡顿修复：三个角色均成功解析后，绑定缓存保留至配置变化或显式清理，不再每 30 秒到期后枚举设备并占用预览、录制共用的解析锁。仍有未找到设备时保留 30 秒重试，缺失身份不回退到其他相机；显式重连同时清理绑定和设备身份缓存。采集中拔插或替换相机后，应停止录制并显式重连以更新绑定。

同一提交的遥测减量一并迁入：UI 帧不再携带 `nativeStatus.actionHistory`，保留当前状态及 `lastAction`；不修改录制器读取的原始动作历史。

旧配置迁移仅在没有自定义身份时按历史标签处理。显式绑定的稳定身份优先，不能因为相机重新枚举后的 index 恰好与旧默认相同，就在保存或重载时被默认身份覆盖。

## 构建与部署

修改 HAL 源码、头文件或协议后，应从同一份源码构建配套的 `HalServer.exe` 和 `JodellGripperWorker.exe`。若修改后端 native Fast-DDS 源，还需重建对应 DLL。

先在隔离目录完成构建和测试，再按现场流程部署候选文件；不覆盖运行中的程序。没有 SDK 的开发机可以显式关闭 `APPSTATION_ENABLE_VENDOR_SDKS` 和 `APPSTATION_ENABLE_DDS` 做骨架编译，但产物不作为实机发布程序，也不能声称验证了设备或 DDS。

本机 VS2022 为 Professional；现有脚本中的 Community 路径和现场 Fast-DDS 路径需在部署时核实。本轮未迁入远端启动器时间戳判断或启停行为变化，未部署生产 HAL。

此次自检迁移使用 MSVC Professional 在 `hal/build/hkvl-tare-*` 独立目录验证。CMake 关闭 vendor SDK 和 DDS 后，核心库及夹爪 worker 编译通过，但现有 DDS 源文件仍无条件包含 `fastcdr/Cdr.h`，缺少依赖的本机无法完成 `HalServer` 链接；这些离线产物不能用于现场部署。

外部 `act_deploy.py` 不在仓库。部署前需确认其固定版本、依赖、参数协议及完整 `dataContract` 传递方式，不能假设旧外部程序自动兼容新接口。此次未修改或运行外部程序。

## 本轮记录

2026-09-18 的整合范围、实际验证、备份和未覆盖项见 `frontend/output/ui-redesign/SESSION_SUMMARY.md` 第十三节。离线通过不等于实机采样、运动或录制已验证。

同日 HKVL 自检后续迁移验证：

- Python HAL 源码契约、力配置、DDS 客户端与能力诊断共 155 项通过；HKVL 录制取样/校准元数据 2 项通过；Tare 服务及路由专项 11 项通过。
- 前端自检组件、录制预检、设置回归、安全状态和 frame selector 共 49 项通过；`npm --prefix frontend run build` 通过，包含类型检查。
- MSVC 原生 `ForceCoreTests` 通过，`WorkerResilienceTests` 14 项、`HkvlTareTests` 10 项、`ForceTareRuntimeTests` 14 项通过。测试使用注入样本与无设备控制状态，不开启传感器、设备 SDK 或 DDS。
- 扩展运行的旧后端运动测试出现 8 项失败；在独立目录提取修改前 HEAD 后复现相同失败。原因分别为 test 模式 `hal_result` 未赋值、旧运动替身缺少新鲜度/运动状态，以及旧路由测试缺少控制租约，未在本次自检迁移中修改。
- 完整 HAL 构建受上述 Fast-DDS 头文件缺失阻碍；未部署、未启动硬件、未进行浏览器视觉或实机验收。

同日按评审选定的四项继续修复：配置恢复、DDS 状态发布与源新鲜度、Tare 接收窗口、episode 校准追溯。后端整合回归 397 项通过，前端相关回归 51 项及类型检查通过。原生离线回归 `HkvlTareTests` 14 项、`ForceTareRuntimeTests` 15 项、`WorkerResilienceTests` 14 项及 `ForceCoreTests` 通过。字节批次测试共用生产解析路径，但未覆盖真实 Win32 USB/串口驱动；DDS 线程分离经源码契约检查、后端并发及 WebSocket 模拟回归验证，未完成真实 Fast-DDS 编译与运行。

同日相机修复迁入验证：相机缓存、低光补偿、设备识别与格式、遥测、录制及校准元数据相关回归共 129 项通过。新增回归模拟三相机预览和录制读取跨越 30/60/120 秒、缺失设备重试、显式重连失效与 UI 动作历史裁剪，未打开真实相机验证暗光帧率、USB 行为或长时间录制。当前虚拟环境未安装 Ruff，未执行该静态检查；不把模拟结果作为工控机实测性能。
