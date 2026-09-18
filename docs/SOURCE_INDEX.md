# 完整源码索引

对应同步提交 `6d898c9`。共 193 个自有源码、测试和脚本文件，均已添加中文文件头导航。阶段定义与调用链见 [阅读指南](CODE_READING_GUIDE.md)。

“定位符号”供编辑器符号面板或全文搜索使用，不是函数执行顺序；没有声明符号的入口脚本直接从顶部读参数和流程。

## 01 入口与界面（32 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [frontend/src/App.tsx](../frontend/src/App.tsx) | 定义全局主题和页面路由；按 mockMode 启停模拟数据或后端遥测连接。 | `App` |
| [frontend/src/components/ActionCompareModal.tsx](../frontend/src/components/ActionCompareModal.tsx) | 在确认操作前对比参数或状态的前后变化，显示各项差异与风险提示。 | `ActionCompareItem`、`ActionCompareModalProps`、`toneIcon`、`toneType`、`CompareColumn` |
| [frontend/src/components/AppLayout.tsx](../frontend/src/components/AppLayout.tsx) | 组织导航、页面内容和全局状态区域，提供所有页面共用的布局。 | `TopStatus`、`AppLayout` |
| [frontend/src/components/CameraPreview.tsx](../frontend/src/components/CameraPreview.tsx) | 显示 MJPEG 相机流，处理加载、错误占位和手动刷新。 | `CameraPreviewProps`、`CameraPreview` |
| [frontend/src/components/Charts.tsx](../frontend/src/components/Charts.tsx) | 使用 ECharts 绘制历史遥测数据；输入来自状态仓库的采样历史。 | `HistoryProps`、`JointChart`、`AxisGroupChart`、`ForceChart`、`QueueChart` |
| [frontend/src/components/GlobalEmergencyStopButton.tsx](../frontend/src/components/GlobalEmergencyStopButton.tsx) | 提供全局急停交互并调用状态仓库中的急停操作。 | `GlobalEmergencyStopButton` |
| [frontend/src/components/LogPanel.tsx](../frontend/src/components/LogPanel.tsx) | 按通道、级别和搜索词筛选日志，并使用虚拟列表控制渲染开销。 | `formatLogTime`、`matchesSearch`、`LogPanel` |
| [frontend/src/components/MetricPill.tsx](../frontend/src/components/MetricPill.tsx) | 以紧凑标签展示单项指标及其颜色状态。 | `MetricPillProps`、`MetricPill` |
| [frontend/src/components/SafetyOverlay.tsx](../frontend/src/components/SafetyOverlay.tsx) | 显示安全锁定覆盖层，并提供安全确认相关交互。 | `SafetyOverlay` |
| [frontend/src/components/StatusBar.tsx](../frontend/src/components/StatusBar.tsx) | 显示连接、进程及遥测相关的全局状态摘要。 | `StatusBar` |
| [frontend/src/components/dashboard/ArmOverviewPanel.tsx](../frontend/src/components/dashboard/ArmOverviewPanel.tsx) | 汇总单侧机械臂的相机、力、夹爪和诊断状态。 | `diagnosticState`、`cameraByKey`、`forceMagnitude`、`formatGripperPosition`、`ArmOverviewPanel` |
| [frontend/src/components/dashboard/ModuleStatusGrid.tsx](../frontend/src/components/dashboard/ModuleStatusGrid.tsx) | 将各模块运行状态组织为概览网格。 | `ModuleStatus`、`ModuleStatusGrid` |
| [frontend/src/components/dashboard/PlatformOverview.tsx](../frontend/src/components/dashboard/PlatformOverview.tsx) | 汇总平台进程、相机及相关模块状态。 | `processState`、`cameraByKey`、`PlatformOverview` |
| [frontend/src/components/dashboard/ReadinessSummary.tsx](../frontend/src/components/dashboard/ReadinessSummary.tsx) | 把诊断结果汇总为准备程度与状态提示。 | `scoreStatus`、`ReadinessSummary` |
| [frontend/src/components/record/CameraPanel.tsx](../frontend/src/components/record/CameraPanel.tsx) | 组织录制页面的全局与双腕相机区域，复用相机预览组件。 | `CameraSlotProps`、`CameraSlot`、`CameraPanel` |
| [frontend/src/components/record/EpisodeControlPanel.tsx](../frontend/src/components/record/EpisodeControlPanel.tsx) | 提供录制会话、episode 保存/丢弃及复位流程的主要操作入口。 | `EpisodeControlPanelProps`、`EpisodeControlPanel` |
| [frontend/src/components/record/EpisodeHistoryCard.tsx](../frontend/src/components/record/EpisodeHistoryCard.tsx) | 合并后端历史与本次会话记录，去重后按时间展示近期 episode。 | `RecentEpisode`、`episodeIndex`、`backendStatus`、`EpisodeHistoryCard` |
| [frontend/src/components/record/HardwareStatusCard.tsx](../frontend/src/components/record/HardwareStatusCard.tsx) | 展示由 hardwareStatus 推导的硬件连接行及遥测新鲜度。 | `dotStyle`、`HardwareStatusCard` |
| [frontend/src/components/record/KalmanFilterCard.tsx](../frontend/src/components/record/KalmanFilterCard.tsx) | 编辑遥操作 Kalman 与映射相关参数，并同步到配置状态。 | `TeleopConfig`、`NumericTeleopKey`、`KalmanParam`、`formatValue`、`KalmanFilterCard` |
| [frontend/src/components/record/PreCheckModal.tsx](../frontend/src/components/record/PreCheckModal.tsx) | 执行录制前的逻辑连接、回原点和相机检查，区分阻断条件与提示。 | `StepDef`、`diagnosticReady`、`teleopHandsReady`、`requiredResetSides`、`requiredMotionReturnReady` |
| [frontend/src/components/record/QualityReportModal.tsx](../frontend/src/components/record/QualityReportModal.tsx) | 展示录制 episode 的质量结果与相关统计。 | `QualityReportModalProps`、`QualityReportModal` |
| [frontend/src/components/record/RecordTelemetryPanel.tsx](../frontend/src/components/record/RecordTelemetryPanel.tsx) | 显示录制期间各轴位姿和双侧力的数值与比例条。 | `formatAxisValue`、`axisRatio`、`forceRatio`、`forceTone`、`poseStyle` |
| [frontend/src/components/record/SafetyMonitorCard.tsx](../frontend/src/components/record/SafetyMonitorCard.tsx) | 展示双侧力与危险度，辅助观察录制时的安全状态。 | `DangerBarProps`、`DangerBar`、`ForceValueSummaryProps`、`ForceValueSummary`、`forceDanger` |
| [frontend/src/index.css](../frontend/src/index.css) | 应用全局样式与响应式布局；按页面和组件选择器定位，后部规则可能覆盖前部同名规则。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/main.tsx](../frontend/src/main.tsx) | 挂载 React 应用并安装页面退出时的运行资源释放监听。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/views/AutoView.tsx](../frontend/src/views/AutoView.tsx) | 展示自动执行状态，连接模型选择和自动运行控制。 | `AutoView` |
| [frontend/src/views/DashboardView.tsx](../frontend/src/views/DashboardView.tsx) | 组合平台、双臂和硬件状态概览，提供设备与设置导航。 | `diagnosticState`、`cameraByKey`、`forceMagnitude`、`stateText`、`stateTone` |
| [frontend/src/views/DatasetView.tsx](../frontend/src/views/DatasetView.tsx) | 管理数据集、episode 审阅和图像回放；列表与详情分别请求，避免一次加载全部样本。 | `CameraKey`、`EpisodeStatus`、`EpisodeSample`、`ReviewCamera`、`ReviewEpisode` |
| [frontend/src/views/FineTuneView.tsx](../frontend/src/views/FineTuneView.tsx) | 提供微调任务参数输入、任务列表和取消操作。 | `FineTuneView` |
| [frontend/src/views/ModelView.tsx](../frontend/src/views/ModelView.tsx) | 展示、导入和启停策略模型，调用后端模型接口。 | `ModelView` |
| [frontend/src/views/RecordPage.tsx](../frontend/src/views/RecordPage.tsx) | 装配录制页面：相机、控制、遥测、预检查、质量报告和历史记录。 | `RecordPage` |
| [frontend/src/views/SettingsView.tsx](../frontend/src/views/SettingsView.tsx) | 按硬件模块组织配置与手动操作；包含原点、主手、力标定、相机和 PICO 设置。 | `CameraKey`、`GripperPortHint`、`InlineStatusTone`、`PendingComparison`、`tabForHardwareHash` |

## 02 前端契约与状态（10 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [frontend/src/api/index.ts](../frontend/src/api/index.ts) | 封装后端 HTTP 请求、错误信息、相机 URL 与页面生命周期命令。 | `sendRuntimeLifecycleCommand`、`installRuntimeLifecycleOnClose`、`installRuntimeReleaseOnClose`、`installAutoShutdownOnClose`、`ApiErrorPayload` |
| [frontend/src/data.ts](../frontend/src/data.ts) | 提供前端默认配置、诊断初值与显示标签；包含操作者侧和硬件侧的转换函数。 | `RobotSide`、`hardwareSideForOperatorSide`、`operatorSideForHardwareSide`、`operatorSideLabel`、`hardwareChannelLabel` |
| [frontend/src/hardwareStatus.ts](../frontend/src/hardwareStatus.ts) | 从当前配置和遥测推导硬件状态行；遥测过期后撤销实时成功状态。 | `HardwareStatusTone`、`HardwareStatusRow`、`telemetryLinkIsLive`、`telemetryLinkLabel`、`configuredHalPort` |
| [frontend/src/hooks/useLiveCameraSnapshot.ts](../frontend/src/hooks/useLiveCameraSnapshot.ts) | 管理相机预览流 URL 及刷新事件；名称保留 Snapshot，但当前预览使用流接口。 | `CameraRefreshEventDetail`、`useLiveCameraSnapshot`、`refreshCameraStream` |
| [frontend/src/manualMotionLimits.ts](../frontend/src/manualMotionLimits.ts) | 把脉冲标定与 HAL 单步上限组合为界面手动步长限制。 | `manualAxisStepLimitFromPulse`、`manualAxisStepLimitPulse` |
| [frontend/src/manualSpeed.ts](../frontend/src/manualSpeed.ts) | 定义手动粗、中、细速度倍率，并将实际速度限制在配置上限内。 | `manualSpeedScale`、`manualMaxVelocity` |
| [frontend/src/motionReturnReady.ts](../frontend/src/motionReturnReady.ts) | 根据所需轴的使能反馈判断某侧是否具备返回工作原点条件。 | `MotionSide`、`requiredAxisIndexes`、`motionSideReturnOriginReady` |
| [frontend/src/stores/telemetry.ts](../frontend/src/stores/telemetry.ts) | Zustand 全局状态中枢；处理配置保存队列、WebSocket、遥测节流、手动控制与录制状态。 | `useTelemetryStore`、`queueConfigSave` |
| [frontend/src/teleopStatus.ts](../frontend/src/teleopStatus.ts) | 综合诊断与逻辑连接状态，生成单手或双手遥操作的显示状态。 | `omegaDiagnosticState`、`handForSide`、`logicalDisconnected`、`teleopHandState`、`teleopHandValue` |
| [frontend/src/types.ts](../frontend/src/types.ts) | 集中声明配置、遥测、录制、诊断与页面状态类型；与后端 schemas 对照阅读。 | `ConnectionState`、`TelemetryLinkState`、`TelemetryLinkStatus`、`LogLevel`、`LogChannel` |

## 03 后端契约与配置（13 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [backend/__init__.py](../backend/__init__.py) | 后端 Python 包入口；服务实例在 app_factory 中创建。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/app.py](../backend/app.py) | FastAPI 应用工厂、HTTP 路由与 WebSocket 入口；连接业务服务并管理资源生命周期。 | `create_app`、`make_hal_client`、`websocket_endpoint` |
| [backend/app_factory.py](../backend/app_factory.py) | 按依赖顺序创建配置、硬件、遥测、HAL、录制和策略服务，并集中返回 AppServices。 | `AppServices`、`create_services` |
| [backend/core/__init__.py](../backend/core/__init__.py) | 配置、数据模型、日志、单位和安全限制等基础模块的包标识。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/core/config.py](../backend/core/config.py) | 读取、迁移、校验与原子保存运行配置；管理参数快照和工作原点迁移。 | `SettingsService`、`reanchor_motion_soft_limits_to_current_origin` |
| [backend/core/defaults.py](../backend/core/defaults.py) | 集中定义硬件标定、轴映射、相机和遥操作默认值，并生成独立的默认配置副本。 | `anchored_mechanical_soft_limits`、`rotation_work_limits_from_soft_limits`、`stable_mechanical_soft_limits`、`default_config` |
| [backend/core/force_config.py](../backend/core/force_config.py) | 校验 HKVL 串口、六轴方向、安全阈值与柔顺参数，并转换为 HAL 的扁平配置。 | `validate_force_config`、`hal_force_config_payload` |
| [backend/core/gripper_protection.py](../backend/core/gripper_protection.py) | 限制夹爪目标开口范围，保留配置要求的最小夹缝。 | `gripper_stroke_mm`、`icf_target_protection_enabled`、`icf_target_min_gap_mm`、`protected_gripper_target_mm`、`protected_gripper_target_mm_from_values` |
| [backend/core/logging.py](../backend/core/logging.py) | 生成结构化事件、操作编号与配置哈希；维护内存日志和会话日志文件。 | `now_ms`、`monotonic_ms`、`default_session_id`、`stable_config_hash`、`LogService` |
| [backend/core/motion_limits.py](../backend/core/motion_limits.py) | 组合机械软限位、工作原点和旋转工作范围，计算各侧实际可用运动区间。 | `AxisLimit`、`WorkOriginMissing`、`side_offset`、`config_limit_to_ui`、`ui_limit_to_config` |
| [backend/core/operator_view.py](../backend/core/operator_view.py) | 显式转换操作者侧与硬件侧；夹爪主手来源也经此映射，避免直接按名称配对。 | `hardware_side_for_operator_side`、`operator_side_for_hardware_side`、`operator_gripper_source_for_side`、`gripper_source_for_hardware_side` |
| [backend/core/schemas.py](../backend/core/schemas.py) | 定义 API 请求、响应、配置及遥测的 Pydantic 模型，是前后端字段契约的后端入口。 | `ApiEnvelope`、`ErrorEnvelope`、`LogEntry`、`ProcessStatus`、`CameraTelemetry` |
| [backend/core/units.py](../backend/core/units.py) | 在运动脉冲、界面单位和 LeRobot 状态单位之间换算；旋转角度与毫度须区分。 | `pulse_to_lerobot`、`pulse_to_ui`、`ui_to_lerobot_state`、`lerobot_to_ui_state`、`motion_pulse_per_unit` |

## 04 后端业务与采集（19 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [backend/drivers/__init__.py](../backend/drivers/__init__.py) | Python 外设适配器包标识；各设备的探测、采集与资源释放在具体驱动中实现。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/drivers/camera_opencv.py](../backend/drivers/camera_opencv.py) | 管理相机身份绑定、OpenCV 采集、编码与最新帧缓存；可使用子进程隔离阻塞驱动。 | `CameraProbeResult`、`CameraFrameSnapshot`、`OpenCVCameraDriver` |
| [backend/drivers/force_nidaq.py](../backend/drivers/force_nidaq.py) | 管理 NI-DAQ 任务、六维力标定、去皮、滤波与采样窗口；与 HKVL 的 HAL 路径分开。 | `ForceProbeResult`、`NidaqForceDriver` |
| [backend/drivers/gripper_rs485.py](../backend/drivers/gripper_rs485.py) | 封装 Jodell DLL 的端口选择、读写和开口换算；保留给驱动测试及独立探测脚本。 | `GripperResult`、`Rs485GripperDriver` |
| [backend/drivers/pico_adb.py](../backend/drivers/pico_adb.py) | 通过 ADB 或参考脚本检查、连接 PICO，并管理视觉串流命令。 | `PicoResult`、`PicoAdbDriver` |
| [backend/services/__init__.py](../backend/services/__init__.py) | 业务服务包标识；服务实例及相互依赖统一由 app_factory 装配。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/services/command_service.py](../backend/services/command_service.py) | 执行手动运动、原点、使能与安全命令；在调用 HAL 前检查配置与录制状态。 | `axis_enabled_feedback_unreadable`、`normalize_motion_axis_enabled`、`MotionOriginDriftConfirmationRequired`、`CommandService` |
| [backend/services/dataset_recorder.py](../backend/services/dataset_recorder.py) | 管理录制会话和 episode；按时间戳组帧、排队写入 LeRobot，并提供数据集管理接口。 | `DatasetRecorderService`、`FrameAssembler`、`TimedRingBuffer`、`LeRobotWriterThread` |
| [backend/services/gripper_backend.py](../backend/services/gripper_backend.py) | 定义夹爪后端协议及原生适配器；转换配置、HAL 状态与夹爪命令载荷。 | `GripperBackend`、`native_teleop_enabled`、`gripper_serial_ports`、`native_gripper_payload`、`hal_response_message` |
| [backend/services/gripper_router.py](../backend/services/gripper_router.py) | 把夹爪操作统一路由到 HAL-native 适配器；当前 select 始终返回原生后端。 | `GripperRouter` |
| [backend/services/hardware_service.py](../backend/services/hardware_service.py) | 聚合相机、NI-DAQ、夹爪与 PICO 的 Python 驱动实例，并提供硬件状态查询。 | `HardwareService` |
| [backend/services/pico_network.py](../backend/services/pico_network.py) | 解析 Windows 网卡与路由信息，为 PICO 选择合适的物理网络与网关。 | `PicoNetworkDetectionError`、`IPv4Adapter`、`IPv4Route`、`select_pico_network`、`detect_pico_network` |
| [backend/services/policy_bridge.py](../backend/services/policy_bridge.py) | 在界面状态和 14 维 LeRobot 状态/动作之间转换，生成限幅后的分侧执行计划。 | `lerobot_state_from_ui`、`build_policy_action_plan` |
| [backend/services/policy_service.py](../backend/services/policy_service.py) | 管理模型、自动执行状态和微调任务；具体能力与返回值需结合方法实现阅读。 | `PolicyService` |
| [backend/services/stability_monitor.py](../backend/services/stability_monitor.py) | 运行稳定性观察任务，采集 HAL、相机和力状态并记录诊断结果。 | `StabilityMonitorService` |
| [backend/services/telemetry_hub.py](../backend/services/telemetry_hub.py) | 聚合运动、主手、相机、夹爪和力状态，生成供 WebSocket 与界面使用的遥测帧。 | `TelemetryHub` |
| [backend/services/teleop_mapping.py](../backend/services/teleop_mapping.py) | 管理 HAL-native 遥操作启停、来源共享、回原点安全门控和状态镜像；实时映射在 HAL。 | `TeleopMappingService` |
| [backend/workers/__init__.py](../backend/workers/__init__.py) | 采集子进程包标识；相机 worker 与主进程通过帧数据和状态消息通信。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/workers/camera_capture_worker.py](../backend/workers/camera_capture_worker.py) | 在独立进程内打开并读取相机，通过队列或标准输入输出协议回传图像与状态。 | `run_camera_capture_worker`、`run_camera_capture_worker_stdio` |

## 05 DDS 传输（8 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [backend/hal_client/__init__.py](../backend/hal_client/__init__.py) | HAL 客户端适配器包标识；先读 client 的接口，再读 DDS 实现。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/hal_client/client.py](../backend/hal_client/client.py) | 定义 HAL 抽象接口及测试实现，并保留 HTTP 客户端类；实际 real 模式由 app 选择 DDS。 | `HalHealth`、`HalClient`、`TestHalClient`、`RealHalClient` |
| [backend/hal_client/dds_client.py](../backend/hal_client/dds_client.py) | 把 HAL 状态主题缓存与带 request_id 的命令应答转换为异步 HalClient 接口。 | `DdsRuntimeUnavailableError`、`DdsHalTransport`、`create_default_dds_transport`、`DdsHalClient` |
| [backend/hal_client/dds_runtime.py](../backend/hal_client/dds_runtime.py) | 通过 ctypes 加载原生 Fast-DDS DLL，管理传输句柄、缓存读取、应答等待与关闭。 | `FastDdsBindingUnavailableError`、`FastDdsHalTransport` |
| [backend/hal_client/dds_types.py](../backend/hal_client/dds_types.py) | 定义 DDS 主题名、域默认值以及 JSON 信封和命令请求/应答的数据结构。 | `JsonEnvelope`、`HalCommandRequest`、`HalCommandReply`、`now_unix_ms`、`make_json_envelope` |
| [backend/hal_client/protocol.py](../backend/hal_client/protocol.py) | 集中维护 HAL 命令路径、载荷转换、超时与重试策略；回原点命令采用独立策略。 | `HalCommandSpec`、`command_spec`、`command_request_policy`、`hal_command_payload` |
| [backend/native/appstation_fastdds_transport.cpp](../backend/native/appstation_fastdds_transport.cpp) | 向 Python 暴露 Fast-DDS 的 C ABI；维护主题缓存、命令发布与应答同步。 | `JsonEnvelopeSample`、`HalCommandRequestSample`、`HalCommandReplySample`、`AppStationTopicDataType`、`AppStationFastDdsTransport` |
| [hal/dds/appstation_hal.idl](../hal/dds/appstation_hal.idl) | 定义控制面 DDS 信封与命令请求/应答的线缆数据契约。 | 从文件顶部的参数、配置或入口流程开始 |

## 06 HAL 硬件与安全（40 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [hal/include/ForceComplianceController.h](../hal/include/ForceComplianceController.h) | 声明ForceComplianceController 的接口与状态结构；根据力反馈计算柔顺位移，限制单步与累计修正，并接受实际执行量反馈。 | `ForceComplianceSideConfig`、`ForceComplianceConfig`、`ForceComplianceResult`、`ForceComplianceController` |
| [hal/include/ForceControlRuntime.h](../hal/include/ForceControlRuntime.h) | 声明ForceControlRuntime 的接口与状态结构；连接 HKVL 采样、安全锁存和柔顺控制，管理监控线程与急停/确认回调。 | `ForceRuntimeConfig`、`ForceControlRuntime` |
| [hal/include/ForceSafetyLatch.h](../hal/include/ForceSafetyLatch.h) | 声明ForceSafetyLatch 的接口与状态结构；根据超限次数、严重超限和数据超时锁存故障；恢复需双侧健康并满足稳定窗口。 | `ForceSafetyConfig`、`ForceSafetyTrip`、`ForceSafetyLatch` |
| [hal/include/HalCommandDispatcher.h](../hal/include/HalCommandDispatcher.h) | 声明HalCommandDispatcher 的接口与状态结构；统一解析并分派 HAL 命令，调用运动、主手、遥操作和力运行时。 | `HalCommandDispatcher` |
| [hal/include/HalDdsControlServer.h](../hal/include/HalDdsControlServer.h) | 声明HalDdsControlServer 的接口与状态结构；提供后端 DDS 控制面：发布状态、接收命令并按请求编号返回应答。 | `HalDdsControlServer`、`Impl` |
| [hal/include/HalHttpServer.h](../hal/include/HalHttpServer.h) | 声明HalHttpServer 的接口与状态结构；提供本机 HTTP 健康探测入口；当前业务控制通过 DDS。 | 从文件顶部的参数、配置或入口流程开始 |
| [hal/include/HalJson.h](../hal/include/HalJson.h) | 声明HalJson 的接口与状态结构；集中完成 HAL 状态序列化和配置/命令 JSON 字段解析。 | 从文件顶部的参数、配置或入口流程开始 |
| [hal/include/HalTypes.h](../hal/include/HalTypes.h) | 定义 HAL 共用侧别、轴、状态、限位和运动结果结构。 | `Side`、`SemanticAxis`、`AxisLimit`、`MotionProfile`、`AxisState` |
| [hal/include/HkvlForceDriver.h](../hal/include/HkvlForceDriver.h) | 声明HkvlForceDriver 的接口与状态结构；管理双侧 HKVL 串口读取、协议解析、去皮、滤波与采样回调。 | `HkvlSerialConfig`、`HkvlDriverSample`、`HkvlSideSnapshot`、`HkvlDriverSnapshot`、`HkvlForceDriver` |
| [hal/include/HkvlForceProtocol.h](../hal/include/HkvlForceProtocol.h) | 声明HkvlForceProtocol 的接口与状态结构；解析 HKVL 字节帧与 CRC，维护帧同步和错误统计。 | `HkvlForceFrame`、`HkvlForceParserStats`、`HkvlForceParser` |
| [hal/include/JodellGripperDriver.h](../hal/include/JodellGripperDriver.h) | 声明JodellGripperDriver 的接口与状态结构；封装 Jodell 夹爪配置、开口换算与隔离 worker 通信。 | `JodellGripperConfig`、`JodellGripperDriver`、`ProcessWorkerHandle` |
| [hal/include/LTDMCDriver.h](../hal/include/LTDMCDriver.h) | 声明LTDMCDriver 的接口与状态结构；封装运动卡访问、脉冲换算、回原点、目标续推、软限位及急停状态。 | `LTDMCDriver` |
| [hal/include/MotionControlThread.h](../hal/include/MotionControlThread.h) | 声明MotionControlThread 的接口与状态结构；管理运动状态后台轮询线程的启动、循环和停止。 | `MotionControlThread` |
| [hal/include/NativeTeleopController.h](../hal/include/NativeTeleopController.h) | 声明NativeTeleopController 的接口与状态结构；管理主手采样与遥操作状态机，计算映射、滤波、门控和夹爪跟随。 | `NativeTeleopConfig`、`NativeTeleopAction`、`NativeTeleopController`、`PendingGripperCommand`、`KalmanAxisState` |
| [hal/include/Omega7Driver.h](../hal/include/Omega7Driver.h) | 声明Omega7Driver 的接口与状态结构；枚举和读取 Omega.7 主手，处理左右设备绑定、重力补偿及力输出。 | `Omega7State`、`Omega7Driver` |
| [hal/include/TeleopDdsTypes.h](../hal/include/TeleopDdsTypes.h) | 定义固定大小的 TeleopHardwareTarget，明确映射端与执行端的数据边界。 | `TeleopHardwareTarget` |
| [hal/include/TeleopFollowerTargetSubscriber.h](../hal/include/TeleopFollowerTargetSubscriber.h) | 声明TeleopFollowerTargetSubscriber 的接口与状态结构；订阅 DDS 硬件目标并交给最终执行器，隔离传输与设备访问。 | `TeleopFollowerTargetSubscriber`、`Impl` |
| [hal/include/TeleopHardwareTargetExecutor.h](../hal/include/TeleopHardwareTargetExecutor.h) | 声明TeleopHardwareTargetExecutor 的接口与状态结构；接收硬件目标，叠加柔顺修正后交给运动驱动，并回写实际修正量。 | `TeleopHardwareTargetExecutor` |
| [hal/include/TeleopLeaderPublisher.h](../hal/include/TeleopLeaderPublisher.h) | 声明TeleopLeaderPublisher 的接口与状态结构；把主手采样状态发布到 DDS LeaderState 主题。 | `TeleopLeaderPublisher`、`Impl` |
| [hal/include/TeleopMappingNode.h](../hal/include/TeleopMappingNode.h) | 声明TeleopMappingNode 的接口与状态结构；订阅主手状态，调用原生映射算法，再发布 HardwareTarget。 | `TeleopMappingNode`、`Impl` |
| [hal/src/ForceComplianceController.cpp](../hal/src/ForceComplianceController.cpp) | 根据力反馈计算柔顺位移，限制单步与累计修正，并接受实际执行量反馈。 | `ForceComplianceController::configure`、`ForceComplianceController::correction`、`ForceComplianceController::commit`、`ForceComplianceController::reset`、`ForceComplianceController::resetSide` |
| [hal/src/ForceControlRuntime.cpp](../hal/src/ForceControlRuntime.cpp) | 连接 HKVL 采样、安全锁存和柔顺控制，管理监控线程与急停/确认回调。 | `ForceControlRuntime::configure`、`ForceControlRuntime::config`、`ForceControlRuntime::start`、`ForceControlRuntime::stop`、`ForceControlRuntime::running` |
| [hal/src/ForceSafetyLatch.cpp](../hal/src/ForceSafetyLatch.cpp) | 根据超限次数、严重超限和数据超时锁存故障；恢复需双侧健康并满足稳定窗口。 | `ForceSafetyLatch::configure`、`ForceSafetyLatch::onSample`、`ForceSafetyLatch::checkWatchdog`、`ForceSafetyLatch::latchExternal`、`ForceSafetyLatch::markDisconnected` |
| [hal/src/HalCommandDispatcher.cpp](../hal/src/HalCommandDispatcher.cpp) | 统一解析并分派 HAL 命令，调用运动、主手、遥操作和力运行时。 | `HalCommandDispatcher::handleEmergencyStop`、`HalCommandDispatcher::handle` |
| [hal/src/HalDdsControlServer.cpp](../hal/src/HalDdsControlServer.cpp) | 提供后端 DDS 控制面：发布状态、接收命令并按请求编号返回应答。 | `JsonEnvelopeSample`、`HalCommandRequestSample`、`HalCommandReplySample`、`HalTopicDataType`、`HalDdsControlServer` |
| [hal/src/HalHttpServer.cpp](../hal/src/HalHttpServer.cpp) | 提供本机 HTTP 健康探测入口；当前业务控制通过 DDS。 | 从文件顶部的参数、配置或入口流程开始 |
| [hal/src/HalJson.cpp](../hal/src/HalJson.cpp) | 集中完成 HAL 状态序列化和配置/命令 JSON 字段解析。 | 从文件顶部的参数、配置或入口流程开始 |
| [hal/src/HalServer.cpp](../hal/src/HalServer.cpp) | HAL 进程 main 入口；装配驱动、力安全、DDS 控制面与遥操作数据链路并管理关闭顺序。 | `main` |
| [hal/src/HkvlForceDriver.cpp](../hal/src/HkvlForceDriver.cpp) | 管理双侧 HKVL 串口读取、协议解析、去皮、滤波与采样回调。 | `HkvlForceDriver`、`SideState`、`HkvlForceDriver::start`、`HkvlForceDriver::stop`、`HkvlForceDriver::running` |
| [hal/src/HkvlForceProtocol.cpp](../hal/src/HkvlForceProtocol.cpp) | 解析 HKVL 字节帧与 CRC，维护帧同步和错误统计。 | `HkvlForceParser::feed`、`HkvlForceParser::reset`、`HkvlForceParser::stats` |
| [hal/src/JodellGripperDriver.cpp](../hal/src/JodellGripperDriver.cpp) | 封装 Jodell 夹爪配置、开口换算与隔离 worker 通信。 | `JodellGripperDriver::configure`、`JodellGripperDriver::commandTarget`、`JodellGripperDriver::readPositionMm`、`JodellGripperDriver::targetMm`、`JodellGripperDriver::positionMm` |
| [hal/src/JodellGripperWorker.cpp](../hal/src/JodellGripperWorker.cpp) | 独立夹爪工作进程入口；执行父进程发来的 DLL 操作并返回结果。 | `main` |
| [hal/src/LTDMCDriver.cpp](../hal/src/LTDMCDriver.cpp) | 封装运动卡访问、脉冲换算、回原点、目标续推、软限位及急停状态。 | `AxisHoldResult`、`LTDMCDriver::initialize`、`LTDMCDriver::health`、`LTDMCDriver::ensureMotionReturnAllowed`、`LTDMCDriver::estopActive` |
| [hal/src/MotionControlThread.cpp](../hal/src/MotionControlThread.cpp) | 管理运动状态后台轮询线程的启动、循环和停止。 | `MotionControlThread::start`、`MotionControlThread::stop`、`MotionControlThread::loop` |
| [hal/src/NativeTeleopController.cpp](../hal/src/NativeTeleopController.cpp) | 管理主手采样与遥操作状态机，计算映射、滤波、门控和夹爪跟随。 | `NativeTeleopController::configure`、`NativeTeleopController::configureGripper`、`NativeTeleopController::configureGripperProtection`、`NativeTeleopController::start`、`NativeTeleopController::stop` |
| [hal/src/Omega7Driver.cpp](../hal/src/Omega7Driver.cpp) | 枚举和读取 Omega.7 主手，处理左右设备绑定、重力补偿及力输出。 | `Omega7Driver::initialize`、`Omega7Driver::ensureReady`、`Omega7Driver::ok`、`Omega7Driver::lastError`、`Omega7Driver::readState` |
| [hal/src/TeleopFollowerTargetSubscriber.cpp](../hal/src/TeleopFollowerTargetSubscriber.cpp) | 订阅 DDS 硬件目标并交给最终执行器，隔离传输与设备访问。 | `TeleopHardwareTargetSample`、`HardwareTargetTopicDataType`、`TeleopFollowerTargetSubscriber`、`TargetListener`、`TeleopFollowerTargetSubscriber::enabled` |
| [hal/src/TeleopHardwareTargetExecutor.cpp](../hal/src/TeleopHardwareTargetExecutor.cpp) | 接收硬件目标，叠加柔顺修正后交给运动驱动，并回写实际修正量。 | `TeleopHardwareTargetExecutor::apply` |
| [hal/src/TeleopLeaderPublisher.cpp](../hal/src/TeleopLeaderPublisher.cpp) | 把主手采样状态发布到 DDS LeaderState 主题。 | `JsonEnvelopeSample`、`JsonEnvelopeTopicDataType`、`TeleopLeaderPublisher`、`TeleopLeaderPublisher::enabled`、`TeleopLeaderPublisher::publishJson` |
| [hal/src/TeleopMappingNode.cpp](../hal/src/TeleopMappingNode.cpp) | 订阅主手状态，调用原生映射算法，再发布 HardwareTarget。 | `JsonEnvelopeSample`、`TeleopHardwareTargetSample`、`TeleopTopicDataType`、`TeleopMappingNode`、`LeaderListener` |

## 07 测试与验证（43 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [backend/tests/__init__.py](../backend/tests/__init__.py) | 后端测试包标识；测试用例从基础换算逐步覆盖服务、API 和源码契约。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/tests/test_app.py](../backend/tests/test_app.py) | 回归验证：应用工厂、API 路由、配置、硬件状态、录制和安全行为的集成契约。 | `create_mock_record_client`、`test_backend_app_import_does_not_create_runtime_services`、`test_create_app_exposes_gripper_router`、`test_create_app_exposes_app_services_and_legacy_state_attrs`、`test_recording_api_contract_examples_cover_required_routes` |
| [backend/tests/test_camera_format.py](../backend/tests/test_camera_format.py) | 回归验证：相机驱动与 worker 设置 YUYV 格式及采集尺寸的顺序。 | `test_camera_driver_requests_yuyv_fourcc_before_capture_dimensions`、`test_camera_worker_requests_yuyv_fourcc_before_capture_dimensions` |
| [backend/tests/test_command_diagnostic_logging.py](../backend/tests/test_command_diagnostic_logging.py) | 回归验证：手动命令、工作原点操作与诊断日志，以及原点切换时的遥操作停止。 | `FakeSettings`、`FakeTelemetry`、`FakeHal`、`RecordingHal`、`DisabledMotionHal` |
| [backend/tests/test_dataset_recorder.py](../backend/tests/test_dataset_recorder.py) | 回归验证：按时间戳组帧、脉冲缓存、原点快照、episode 写入及录制回滚。 | `hal_motion_fixture`、`omega_state_fixture`、`source_sample_fixture`、`force_source_fixture`、`camera_source_fixture` |
| [backend/tests/test_diagnostic_logging.py](../backend/tests/test_diagnostic_logging.py) | 回归验证：结构化事件、限频、会话日志保存、清理和配置变更记录。 | `test_event_log_formats_stable_key_value_message`、`test_event_log_uses_generated_operation_id`、`test_rate_limited_event_keeps_first_and_suppresses_repeat`、`test_log_service_persists_each_entry_to_session_file`、`test_log_service_prunes_old_session_files` |
| [backend/tests/test_force_axis_calibration.py](../backend/tests/test_force_axis_calibration.py) | 回归验证：力轴方向迁移后要求重新确认柔顺映射。 | `test_axis_sign_migration_requires_a_new_compliance_confirmation` |
| [backend/tests/test_force_config.py](../backend/tests/test_force_config.py) | 回归验证：HKVL 端口绑定、方向校准、阈值和柔顺参数校验。 | `hkvl_config`、`test_hkvl_force_config_payload_matches_hal_flat_contract`、`test_hkvl_force_config_payload_uses_pnp_bound_runtime_ports`、`test_hkvl_force_config_payload_rejects_duplicate_pnp_bound_ports`、`test_invalid_hkvl_force_configuration_is_rejected` |
| [backend/tests/test_force_nidaq_driver.py](../backend/tests/test_force_nidaq_driver.py) | 回归验证：NI-DAQ 标定矩阵、去皮、窗口采样和缓存回退。 | `test_force_driver_applies_reference_calibration_fallback`、`test_force_driver_tare_bias_is_subtracted_before_calibration`、`test_force_driver_loads_ati_xml_calibration_file`、`test_force_driver_latest_window_repeats_last_scalar_without_blocking`、`test_force_driver_sample_window_failure_uses_latest_scalar_fallback` |
| [backend/tests/test_gripper_driver.py](../backend/tests/test_gripper_driver.py) | 回归验证：Jodell 开口换算、最小夹缝保护、端口释放和使能条件。 | `test_jodell_gripper_target_position_mapping`、`test_gripper_close_respects_icf_min_gap_protection`、`test_jodell_gripper_default_config_matches_reference_project`、`test_gripper_selects_and_closes_configured_port_per_command`、`test_gripper_probe_releases_each_configured_port` |
| [backend/tests/test_gripper_router.py](../backend/tests/test_gripper_router.py) | 回归验证：所有夹爪操作选择 HAL-native 并生成正确载荷。 | `FakeHal`、`FakeTeleopMapper`、`build_router`、`test_router_always_selects_hal_native`、`test_router_ignores_legacy_python_mapper_engine` |
| [backend/tests/test_hal_dds_client.py](../backend/tests/test_hal_dds_client.py) | 回归验证：DDS 状态缓存、命令应答、急停发布、超时和重试。 | `FakeDdsTransport`、`test_dds_hal_client_reads_health_from_topic_cache`、`test_dds_hal_client_reads_motion_state_from_topic_cache`、`test_dds_hal_client_reads_force_state_from_topic_cache`、`test_dds_hal_client_reads_native_teleop_status_from_topic_cache` |
| [backend/tests/test_hal_native_acceptance_report.py](../backend/tests/test_hal_native_acceptance_report.py) | 回归验证：验收报告字段完整性与失败诊断分类。 | `test_hal_native_acceptance_report_verifier_accepts_full_pass`、`test_hal_native_acceptance_report_verifier_rejects_missing_axes`、`test_hal_native_acceptance_report_verifier_explains_missing_observation`、`test_hal_native_acceptance_report_verifier_rejects_missing_axis_diagnostics`、`test_hal_native_acceptance_report_verifier_rejects_zero_output_axis_diagnostics` |
| [backend/tests/test_hal_protocol.py](../backend/tests/test_hal_protocol.py) | 回归验证：共享命令契约、载荷展开以及回原点命令超时策略。 | `test_shared_hal_command_protocol_covers_existing_real_hal_commands`、`test_hal_command_payload_flattens_teleop_target_deltas`、`test_hal_command_request_policy_keeps_home_commands_long_running`、`test_unknown_hal_command_has_clear_error` |
| [backend/tests/test_hal_source_contracts.py](../backend/tests/test_hal_source_contracts.py) | 回归验证：HAL C++ 源码中的安全与接口约束；文本契约不等于实机验收。 | `test_hal_motion_state_json_exposes_axis_moving_flags`、`test_hal_removes_orphaned_axis_diagnostics_entrypoint`、`test_hal_command_dispatcher_does_not_store_unused_gripper_reference`、`test_hal_motion_control_thread_keeps_only_polling_lifecycle`、`test_ltdmc_driver_removes_unused_motion_simulation_entrypoints` |
| [backend/tests/test_hal_transport_selection.py](../backend/tests/test_hal_transport_selection.py) | 回归验证：real 模式选择 DDS、test 模式保留替身并拒绝旧 HTTP 配置。 | `FakeDdsHalClient`、`test_make_hal_client_defaults_to_dds_real_hal`、`test_make_hal_client_keeps_test_hal_even_when_dds_transport_is_set`、`test_make_hal_client_uses_dds_transport_for_real_hal`、`test_make_hal_client_rejects_http_transport_for_real_hal` |
| [backend/tests/test_hardware_defaults.py](../backend/tests/test_hardware_defaults.py) | 回归验证：硬件默认值、标定、轴映射、相机与运动参数的版本约定。 | `test_hal_defaults_use_backend_hal_boundary`、`test_motion_translation_profile_uses_um_units`、`test_omega7_teleop_defaults_match_icf_strategy`、`test_motion_kinematics_defaults_match_icf_mapping`、`test_work_origin_defaults_match_icf_reference_position` |
| [backend/tests/test_import_icf_teleop_config.py](../backend/tests/test_import_icf_teleop_config.py) | 回归验证：ICF 配置导入的操作者映射、Yaw 权限及限位迁移。 | `test_import_icf_gripper_sources_are_normalized_to_operator_view`、`test_import_icf_config_restores_card0_yaw_permission`、`test_import_icf_config_decouples_legacy_rotation_window_from_mechanical_limits` |
| [backend/tests/test_normalize_origin.py](../backend/tests/test_normalize_origin.py) | 回归验证：数据集原点换算及预演/实际改写行为。 | `load_normalize_origin_module`、`test_normalize_frame_to_origin_updates_motion_state_and_action_only`、`test_normalize_origin_dataset_dry_run_and_apply_rewrite_parquet` |
| [backend/tests/test_operator_view.py](../backend/tests/test_operator_view.py) | 回归验证：操作者左右侧、硬件通道与夹爪主手来源之间的交叉映射。 | `test_operator_view_maps_left_to_existing_right_hardware`、`test_operator_view_gripper_sources_follow_same_named_operator_hand`、`test_operator_view_gripper_sources_for_hardware_targets_follow_cross_mapping` |
| [backend/tests/test_pico_adb_driver.py](../backend/tests/test_pico_adb_driver.py) | 回归验证：ADB 离线设备不能被报告为连接成功。 | `test_pico_status_reports_offline_device_as_not_ok` |
| [backend/tests/test_pico_network.py](../backend/tests/test_pico_network.py) | 回归验证：物理网卡选择及 PICO 自动网络配置持久化。 | `test_select_pico_network_prefers_related_physical_lan_over_virtual_default_route`、`test_pico_network_endpoint_preserves_operator_ip_and_persists_detected_pc_fields` |
| [backend/tests/test_policy_bridge.py](../backend/tests/test_policy_bridge.py) | 回归验证：LeRobot 14 维状态、动作限幅、预演和控制侧选择。 | `test_lerobot_state_from_ui_inserts_grippers_and_converts_rotation_to_mdeg`、`test_build_policy_action_plan_clamps_motion_and_gripper_steps`、`test_policy_observation_endpoint_returns_lerobot_state`、`test_policy_action_endpoint_is_dry_run_by_default`、`test_policy_action_endpoint_can_send_through_test_hal` |
| [backend/tests/test_probe_jodell_dual_com.py](../backend/tests/test_probe_jodell_dual_com.py) | 回归验证：双串口探测脚本的参数解析、统计与运动检查开关。 | `load_probe_module`、`test_probe_script_parses_com_ports_and_latency_percentiles`、`test_probe_stats_report_success_rate_and_raw_range`、`test_probe_command_check_requires_explicit_unsafe_flag` |
| [backend/tests/test_stability_monitor.py](../backend/tests/test_stability_monitor.py) | 回归验证：HKVL 监控路径读取 HAL 力状态而不依赖 NI-DAQ。 | `test_stability_monitor_reads_hkvl_state_without_nidaq` |
| [backend/tests/test_stack_scripts.py](../backend/tests/test_stack_scripts.py) | 回归验证：启动脚本的进程顺序、DLL 部署、DDS 和 HKVL 配置注入。 | `test_start_stack_cleans_backend_process_tree_even_without_listening_port`、`test_start_stack_stops_backend_before_restarting_hal`、`test_start_hal_passes_configured_port_to_hal_process_and_health_check`、`test_start_hal_injects_force_runtime_config_from_backend_config`、`test_start_hal_binds_hkvl_sides_to_pnp_instance_ids` |
| [backend/tests/test_telemetry_hub.py](../backend/tests/test_telemetry_hub.py) | 回归验证：力与夹爪遥测反馈、故障状态和来源选择。 | `FakeSettings`、`FakeForce`、`FakeCameras`、`FakeHardware`、`test_real_hal_ok_is_not_reported_faulted_when_force_probe_is_unavailable` |
| [backend/tests/test_teleop_mapping.py](../backend/tests/test_teleop_mapping.py) | 回归验证：原生遥操作启停、来源共享、原点门控、并发切换及失败回滚。 | `FakeHal`、`FakeSettings`、`GuardedSettings`、`FailingNativeStopHal`、`StatusTimeoutHal` |
| [backend/tests/test_units.py](../backend/tests/test_units.py) | 回归验证：脉冲到微米/角度的换算，以及运行标定值的使用。 | `test_rotation_ui_uses_degrees_not_millidegrees`、`test_translation_ui_uses_micrometers`、`test_motion_pulse_per_unit_uses_runtime_kinematics_config` |
| [frontend/src/App.test.tsx](../frontend/src/App.test.tsx) | 集成验证页面、遥测合并、硬件配置、手动控制和录制交互，使用后端替身。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/DatasetView.lazy.test.tsx](../frontend/src/DatasetView.lazy.test.tsx) | 验证数据集列表先取元数据，选择 episode 后再加载详情样本。 | `ok` |
| [frontend/src/api.lifecycle.test.ts](../frontend/src/api.lifecycle.test.ts) | 验证页面隐藏时只发送一次运行资源释放请求。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/components/CameraPreview.test.tsx](../frontend/src/components/CameraPreview.test.tsx) | 验证 MJPEG 流选择、重载、待定状态与错误后的手动刷新。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/components/record/EpisodeHistoryCard.test.tsx](../frontend/src/components/record/EpisodeHistoryCard.test.tsx) | 验证后端历史倒序、实时记录去重优先级与请求失败回退。 | `episode`、`dataset` |
| [frontend/src/components/record/PreCheckModal.test.tsx](../frontend/src/components/record/PreCheckModal.test.tsx) | 验证录制前主手连接、必需原点侧和相机警告的判定。 | `makeReadyForRecordPrecheck` |
| [frontend/src/data.test.ts](../frontend/src/data.test.ts) | 验证前端运动标定、主手重力及 HKVL 默认值与项目约定一致。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/hardwareStatus.test.ts](../frontend/src/hardwareStatus.test.ts) | 验证硬件状态投影、过期遥测降级与夹爪真实反馈条件。 | `liveLink`、`healthyFrame` |
| [frontend/src/main.test.tsx](../frontend/src/main.test.tsx) | 验证 React 启动时安装页面退出的资源释放监听。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/manualMotionLimits.test.ts](../frontend/src/manualMotionLimits.test.ts) | 验证手动平移/旋转步长与脉冲约束及 HAL 上限一致。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/manualSpeed.test.ts](../frontend/src/manualSpeed.test.ts) | 验证粗、中、细速度倍率与最大速度限幅。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/stores/telemetry.test.ts](../frontend/src/stores/telemetry.test.ts) | 验证配置默认值迁移、相机绑定和候选 HAL 二进制诊断提示。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/src/test/setup.ts](../frontend/src/test/setup.ts) | 安装 DOM 测试断言和浏览器 API 替身，并统一测试环境清理。 | `ResizeObserverMock` |
| [hal/tests/ForceCoreTests.cpp](../hal/tests/ForceCoreTests.cpp) | 验证 HKVL 帧解析、力安全锁存、柔顺修正及力运行时的边界行为。 | `main` |

## 08 启动、部署与工具（28 个文件）

| 文件 | 职责 / 阅读重点 | 定位符号 |
| --- | --- | --- |
| [Start-App.cmd](../Start-App.cmd) | 双击启动入口；切换到项目目录后调用 scripts/launch-app.ps1。 | 从文件顶部的参数、配置或入口流程开始 |
| [Stop-App.cmd](../Stop-App.cmd) | 双击停止入口；调用 scripts/stop-stack.ps1 结束本项目服务。 | 从文件顶部的参数、配置或入口流程开始 |
| [backend/native/build_fastdds_transport.cmd](../backend/native/build_fastdds_transport.cmd) | 调用本机 C++ 工具链构建 Python 使用的 Fast-DDS 传输 DLL。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/eslint.config.js](../frontend/eslint.config.js) | 配置 TypeScript、React Hooks 与热更新相关的静态检查规则。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/index.html](../frontend/index.html) | 浏览器 HTML 入口；提供 React 挂载节点并加载 src/main.tsx。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/output/camera-debug/inspect-record.mjs](../frontend/output/camera-debug/inspect-record.mjs) | 保留的录制页面浏览器诊断脚本；用于调查相机预览，非应用启动入口。 | 从文件顶部的参数、配置或入口流程开始 |
| [frontend/scripts/serve-dist.mjs](../frontend/scripts/serve-dist.mjs) | 用本地 HTTP 服务提供构建后的 dist 文件，并处理前端路由回退。 | `safePath`、`sendFile` |
| [frontend/scripts/visual-check.mjs](../frontend/scripts/visual-check.mjs) | 启动预览并用 Playwright 检查页面与截图；用于人工界面核对。 | `wait`、`waitForServer` |
| [frontend/vite.config.ts](../frontend/vite.config.ts) | 配置 Vite 构建分包、开发端口和 Vitest 的 jsdom 测试环境。 | 从文件顶部的参数、配置或入口流程开始 |
| [hal/CMakeLists.txt](../hal/CMakeLists.txt) | 声明 HAL 核心库、服务和夹爪 worker 构建目标，以及 SDK、Fast-DDS 链接配置。 | 从文件顶部的参数、配置或入口流程开始 |
| [hal/build_hal.cmd](../hal/build_hal.cmd) | 使用 Windows C++ 工具链构建 HAL 和夹爪 worker，并处理候选二进制产物。 | 从文件顶部的参数、配置或入口流程开始 |
| [scripts/accept-hal-native-teleop.ps1](../scripts/accept-hal-native-teleop.ps1) | 已停用的旧 HTTP 遥操作验收入口；执行即提示改用后端/UI 的 DDS 路径。 | 从文件顶部的参数、配置或入口流程开始 |
| [scripts/benchmark_jodell_gripper.py](../scripts/benchmark_jodell_gripper.py) | 测量 Jodell 端口读取方式的耗时和成功率；直接使用夹爪驱动。 | `BenchResult`、`percentile`、`mean`、`parse_port`、`timed` |
| [scripts/capture-hkvl-force.ps1](../scripts/capture-hkvl-force.ps1) | 只读采集双侧 HKVL 串口原始数据，并验证候选帧格式与 CRC。 | `New-ReadOnlySerialPort`、`Get-ModbusCrc16`、`Test-HkvlCandidateFrames` |
| [scripts/check-hal.ps1](../scripts/check-hal.ps1) | 请求 HAL /health 并报告端口所属进程和驱动健康状态。 | 从文件顶部的参数、配置或入口流程开始 |
| [scripts/diagnose-teleop-latency.ps1](../scripts/diagnose-teleop-latency.ps1) | 已停用的旧 HTTP 延迟诊断入口；当前执行会抛出迁移说明。 | 从文件顶部的参数、配置或入口流程开始 |
| [scripts/import_icf_teleop_config.py](../scripts/import_icf_teleop_config.py) | 将参考 ICF INI 配置转换为当前运行配置，归一化侧别、轴权限和限位。 | `main`、`load_runtime_config`、`apply_icf_config`、`apply_gripper_config`、`apply_motion_profiles_and_limits` |
| [scripts/launch-app.ps1](../scripts/launch-app.ps1) | 启动或复用本地应用服务，检测健康状态，打开浏览器并收集启动失败诊断。 | `Find-Browser`、`Wait-HttpOk`、`Test-HttpOk`、`Get-RecordStatus`、`Start-AppStack` |
| [scripts/normalize_origin.py](../scripts/normalize_origin.py) | 根据目标工作原点重算数据集运动 state/action；默认预演，显式 apply 才改写文件。 | `parse_args`、`normalize_frame_to_origin`、`normalize_dataset`、`main` |
| [scripts/probe_jodell_dual_com.py](../scripts/probe_jodell_dual_com.py) | 直接探测双 COM 夹爪读数及延迟，区分只读检查与显式启用的运动路由检查。 | `parse_port`、`percentile`、`mean`、`ReadStats`、`bind_symbol` |
| [scripts/push_dataset_to_hub.py](../scripts/push_dataset_to_hub.py) | 加载本地 LeRobot 数据集并上传至指定 Hugging Face 仓库。 | `parse_args`、`push_dataset`、`main` |
| [scripts/run-act-deploy.ps1](../scripts/run-act-deploy.ps1) | 装配 ACT 模型部署参数并调用外部策略工程；Send 控制是否发送真实动作。 | 从文件顶部的参数、配置或入口流程开始 |
| [scripts/run-act-jepa-deploy.ps1](../scripts/run-act-jepa-deploy.ps1) | 装配 ACT-JEPA 部署路径、相机、控制侧和动作平滑参数，并启动外部策略程序。 | `Convert-ToBooleanParameter` |
| [scripts/start-hal.ps1](../scripts/start-hal.ps1) | 部署候选 HAL 二进制和依赖 DLL，绑定 HKVL 端口、注入力配置并启动健康检查。 | `Stop-ProcessTree`、`Stop-HalRuntimeProcessTrees`、`Promote-HalCandidate`、`Copy-RuntimeDllIfNewer`、`Resolve-HkvlBoundPort` |
| [scripts/start-stack-dds.ps1](../scripts/start-stack-dds.ps1) | 设置 DDS 域和发现范围，重启本项目 HAL 与后端服务。 | `Stop-RepoProcessByPattern` |
| [scripts/start-stack.ps1](../scripts/start-stack.ps1) | 按顺序启动 HAL、后端和前端，并管理同项目旧进程及日志。 | `Stop-ProcessTree`、`Stop-BackendProcessTrees` |
| [scripts/stop-stack.ps1](../scripts/stop-stack.ps1) | 查找并停止本项目后端、HAL、夹爪 worker 与前端相关进程树。 | `Stop-ProcessTree`、`Stop-BackendProcessTrees`、`Stop-HalRuntimeProcessTrees` |
| [scripts/verify-hal-native-teleop-report.ps1](../scripts/verify-hal-native-teleop-report.ps1) | 离线校验已有遥操作验收 JSON，检查轴诊断、零停、主手和夹爪等证据。 | `Add-Failure`、`As-Array`、`Has-Value`、`Number-Value`、`Classify-AxisDiagnostic` |

## 配置、数据与非源码文件

下列文件按原格式保留，JSON、锁文件和数据样本不插入代码注释。

| 路径 | 用途与阅读方式 |
| --- | --- |
| [backend/pyproject.toml](../backend/pyproject.toml) | Python 版本、运行/可选依赖和 pytest/ruff/mypy 配置。 |
| [frontend/package.json](../frontend/package.json) | npm 脚本和前端依赖声明。 |
| [frontend/package-lock.json](../frontend/package-lock.json) | 精确依赖锁，使用 npm ci 安装。 |
| [frontend/tsconfig.json](../frontend/tsconfig.json)、[tsconfig.app.json](../frontend/tsconfig.app.json)、[tsconfig.node.json](../frontend/tsconfig.node.json) | TypeScript 项目引用与浏览器/构建配置。 |
| [mypy.ini](../mypy.ini) | 仓库级 Python 类型检查配置。 |
| [HKVL 帧样本](../hal/tests/fixtures/hkvl_active_v1_frames.hex) | 力协议测试用十六进制数据，不是可执行代码。 |
| [frontend/public](../frontend/public)、[frontend/src/assets](../frontend/src/assets) | 图标与图片资源。 |
| [frontend/output](../frontend/output) | 历史截图与调试产物；其中 inspect-record.mjs 已在上表单独说明。 |
| [app-main-DKFfP-X-.js](../app-main-DKFfP-X-.js) | 压缩构建产物，阅读对应 frontend/src 源码。 |
| [.specify](../.specify) | 规格工作流、模板和扩展脚本，保留工具自身说明。 |
| [.gitignore](../.gitignore)、[frontend/.gitignore](../frontend/.gitignore) | 本地环境、日志、二进制和生成产物的 Git 忽略规则。 |
| [AGENTS.md](../AGENTS.md)、[claude.md](../claude.md) | 项目协作约定；本次保留原有本地语言要求。 |
| [backend/vendor/jodell/README.md](../backend/vendor/jodell/README.md) | Jodell SDK 安装位置说明。 |
| [docs/superpowers](superpowers) | 历史设计和实施计划，可辅助理解原因；不能代替当前实现。 |
| [CONTROL_TELEOP_REFERENCE_VERIFIED.md](../CONTROL_TELEOP_REFERENCE_VERIFIED.md) | 控制/遥操作参考核对记录。 |
| [docs/dds-bridge.md](dds-bridge.md)、[hal/README.md](../hal/README.md) | DDS 与硬件边界的现有说明，结合源码读取。 |
| 本地未跟踪目录、虚拟环境、node_modules、vendor 二进制和其他工作树 | 不属于本次自有源码注释范围，原内容保留。 |
