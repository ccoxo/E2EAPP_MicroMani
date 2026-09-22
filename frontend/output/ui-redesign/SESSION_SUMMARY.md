# MicroMani / AppStation 已实现修改与稳定性审查总结

核对日期：2026-09-11；2026-09-14 更新开机自动回原点移除结果（第十节）及控制恢复审查整改（第十一节）；2026-09-17 补充 HKVL 主用默认与 NI-DAQ 备用切换（第十二节）。范围：前端改版、控制安全、模块耦合、线程及请求生命周期，以及力传感器数据源默认配置。依据当前工作区源码、Git 基线 `6d898c9` 和各阶段实际验证记录整理。

本文只把当前源码中保留的实现列为完成项。静态设计预览、已回滚方案和未实施的后续建议已移出完成清单；过时的包体估算、失败数量和“后端控制契约未改变”等描述已更正。表中的“修改前”包含基线问题和改版过程中已复现的问题，不表示所有缺陷均由前端轻量化引入。

“已实现”表示代码已落地；“验证通过”仅指注明范围的离线检查。当前工作区含未提交改动，本轮整理没有部署软件、启动设备或重新执行整套业务测试。

## 一、界面与功能组织：已实现项的前后对比

| 项目 | 修改前 | 当前实现 | 效果与边界 |
| --- | --- | --- | --- |
| 视觉样式 | 配色、表单和卡片依赖原组件库及分散样式 | CSS token 统一背景 `#EEF1F5`、操作蓝 `#1B6CF3`、成功 `#0D9B6C`、警告 `#D4870F`、危险 `#E03A4F`；保留卡片、状态色条和数字强调 | 状态与操作更易区分；并未将静态预览中的每个设计方案都搬入真实界面 |
| 轻量 UI | 页面和弹窗使用 Ant Design | 使用 `components/ui.tsx` 的按钮、表单、标签、Tabs、进度等原语；迁移页面、壳层及弹窗 | 减少运行依赖；loading/disabled 落到原生控件，保留焦点、悬停、按压和减少动画偏好 |
| 设置分区 | 硬件配置集中在大页面，主要状态订阅也集中在上层 | 分成系统连接、安全与力觉、运动控制、遥操作、视觉五个 Tab，仅挂载活动面板；硬件卡拆入 `views/settings/` | 减少非活动卡片的挂载和订阅；便于按域维护，但共享 store、API 和控制保护仍然存在 |
| 设置页跳转 | hash 与分区存在错配；PICO 链接可能跳到主手分区 | hash 选中对应 Tab；区分 PICO 的 `#teleop` 与主手的 `#teleop-left/right` | 原链接能挂载目标卡，避免跳转后找不到设置项 |
| 顶部 Record / Auto / Manual | `selectedMode` 与真实 URL 脱节，点击只改状态 | 由 `/record`、`/auto`、`/settings#manual` 推导并执行导航；其他页面不选中模式 | 消除导航与模式显示的双重状态；导航本身不启动设备 |
| 力觉型号与单位 | 部分标题、设备标签与当前力觉来源不一致 | `forceSensorModelLabel`、`forceSensorUnitLabel` 随 `config.force.source` 派生标题和单位 | 显示与所选配置一致；改变界面配置不等于硬件已成功应用 |
| 录制状态展示 | REC/STANDBY 直接依赖遥测 `frame.recording`，与录制会话阶段可能冲突 | `recordDisplay.ts` 从 `recordSession.phase` 派生显示；遥测仍用于后端同步 | 录制中、准备、结束等状态含义一致；重连时允许短暂同步差异 |
| PreCheck 说明 | 连接或回原点不满足条件时，缺少具体原因 | 列出 HAL、WS、相机、主手、夹爪等失败原因；按侧说明未知/部分使能，提供“去设置使能” | 帮助定位原因；2026-09-14 起，人工勾选回工作原点还须已有所需侧的到位确认且当前无回原点请求；录制启动在后端再次核实。Tare/力值验证仍为可选项，实际动作仍过控制门槛 |

五个 Tab 的实际内容：

| Tab | 内容 |
| --- | --- |
| 系统连接 | HAL、数据存储 |
| 安全与力觉 | 安全链路、左右六维力 |
| 运动控制 | 手动控制、左右运动控制卡 |
| 遥操作 | Omega.7 主手、夹爪 |
| 视觉 | PICO、三路相机 |

源码入口：[全局样式](/D:/E2EAPP_MicroMani/frontend/src/index.css)、[轻量 UI](/D:/E2EAPP_MicroMani/frontend/src/components/ui.tsx)、[设置壳层](/D:/E2EAPP_MicroMani/frontend/src/views/SettingsView.tsx)、[导航](/D:/E2EAPP_MicroMani/frontend/src/components/AppLayout.tsx)、[录制派生](/D:/E2EAPP_MicroMani/frontend/src/recordDisplay.ts)、[PreCheck](/D:/E2EAPP_MicroMani/frontend/src/components/record/PreCheckModal.tsx)。

## 二、轻量化与订阅优化：实际改善在哪里

| 项目 | 修改前 | 当前实现 | 可确认的改善 |
| --- | --- | --- | --- |
| 路由加载 | 业务页面集中进入入口依赖 | 七个业务页使用 `React.lazy`、`Suspense`，图表等共享代码单独分块 | 按页面加载业务代码；入口仍需 React 等 vendor 依赖，不能把入口文件大小当成全部首屏下载量 |
| 图表 | ECharts 及 React 封装承担曲线绘制 | Canvas `LiveLineChart`；移除 `echarts`、`echarts-for-react` | 去掉通用图表库运行依赖；已有曲线展示保留，不声称具备 ECharts 的全部能力 |
| UI 依赖 | `antd`、`@ant-design/icons` 为直接依赖 | 两项从 package/lock 和业务 import 中移除 | 重型 UI 依赖不再进入当前产物；旧 CSS 类名或构建配置中的兼容分支残留不代表运行时仍加载该库 |
| 遥测订阅 | 多个组件订阅整帧，无关字段变化也触发更新 | `useFrameField`、切片和浅比较；Dashboard、Settings、Auto、PreCheck、Dataset、硬件状态卡收窄订阅；历史订阅和时钟下放活动卡片 | 减少无关字段驱动的 React 更新；未进行 CPU、内存或实机控制延迟的前后基准测试 |
| 隐藏页面 | 可见/隐藏页面使用相同显示提交节奏 | 可见显示提交间隔 66 ms，隐藏时 400 ms，可见后立即冲刷待提交帧；紧急帧可立即提交 | 减少隐藏页面显示工作；这是 UI 提交策略，不是 HAL 控制或安全挑战的降频，也不是浏览器调度时限保证 |
| 切片完整性 | `camerasEqual` 漏比较时钟偏差，优化后可能不更新告警 | 补齐 `timestampSkewMs`、标签、backend 等页面实际使用字段 | 修复“减少重渲染却冻结告警”的回归；相机偏差变化能更新预览提示 |

2026-09-11 已保存的生产构建统计来自 [frontend-build.log](/D:/E2EAPP_MicroMani/output/resilience-20260911/frontend-build.log)，单位为构建工具报告的十进制 kB；此表保留该阶段快照，不代表后续每次构建的大小：

| 产物 | 未压缩 | gzip |
| --- | ---: | ---: |
| 全部 14 个 JS 分块合计 | 494.97 kB | 156.61 kB |
| 入口 index JS | 113.04 kB | 34.07 kB |
| vendor JS | 199.57 kB | 66.38 kB |
| SettingsView JS | 101.52 kB | 27.52 kB |
| Charts JS | 3.93 kB | 1.86 kB |
| 主 CSS | 85.30 kB | 15.42 kB |

原文的约 63 KB 壳层、约 370 KB JS 总量不是最终版本数值，已替换。早期约 2.4 MB、Ant Design 约 1.05 MB、ECharts 约 1.13 MB 缺少本次可追溯的同条件原始构建产物，不再据此计算降幅。源码可以确认依赖移除和按路由分包，但不能从包体直接推导 CPU、内存或真机响应提升。

源码入口：[App 路由](/D:/E2EAPP_MicroMani/frontend/src/App.tsx)、[Canvas 图表](/D:/E2EAPP_MicroMani/frontend/src/components/Charts.tsx)、[切片订阅](/D:/E2EAPP_MicroMani/frontend/src/stores/frameSelectors.ts)、[遥测状态](/D:/E2EAPP_MicroMani/frontend/src/stores/telemetry.ts)、[依赖清单](/D:/E2EAPP_MicroMani/frontend/package.json)。

## 三、布局与缩放修复

| 位置 | 修改前 | 当前实现与效果 |
| --- | --- | --- |
| 录制页滚动 | 嵌套高度和滚动约束造成卡片堆叠、内容难以滚动 | 页面主体由 `main-content` 承担滚动；双栏顶部对齐，窄屏按断点重排。局部列表/日志仍可有自己的滚动，不是全应用只剩一个滚动区 |
| 录制相机 | 预览高度容易挤占页面内容 | 录制相机槽使用 `16/10`、最大高度 300px；三路预览在大屏并排，窄屏按断点重排。此尺寸只描述录制相机槽，不能套用于设置页所有相机 |
| 曝光/增益输入框 | 输入宽 140px，所在数值列 92px，溢出 48px | 保留“标签 + 可收缩滑块 + 数值”布局；输入宽度跟随数值列，滑块清理外边距，容器允许收缩；窄屏改为单列 |
| 同类表单 | 范围输入、手动控制、软限位等固定宽度或最小内容宽度可能撑开卡片 | 修正相关宽度、`min-width` 和 `minmax(0, …)` 约束，避免把容器扩大来容纳控件 |
| 数据集回放 | 中等宽度的三路视频布局受最小列宽影响，出现溢出 | 调整视频列宽和断点布局；有真实结构的离线数据夹具覆盖 760、800、900、1024px |

布局阶段的 [修复前记录](/D:/E2EAPP_MicroMani/output/layout-20260911/before.json) 与 [修复后记录](/D:/E2EAPP_MicroMani/output/layout-20260911/final.json) 覆盖 390、800、1024、1201、1440、1920、2560px，共 77 个页面/分区组合：记录中的输入框及主体横向溢出为 0，急停入口均在可见范围。数据集有数据场景另见 [回放布局记录](/D:/E2EAPP_MicroMani/output/layout-20260911/dataset-final.json)。

这次布局记录同时暴露 Model/FineTune 的 14 个未捕获网络拒绝，不能写成当时“浏览器零错误”；这部分随后修复，见第六节的页面异步结果。

**缩放结论：组件不是随窗口尺寸统一线性缩放。** 当前采用 Grid/Flex、固定 px、弹性列与媒体查询：窗口变窄时会换列、堆叠、换行，字体和按钮不会全部按同一比例缩小。浏览器缩放会改变 CSS 像素与屏幕像素关系，也可能触发布局断点；局部按压/动画的 `scale()` 不等于全站缩放机制。

## 四、改版迁移遗漏与控制显示修复

| 项目 | 修改前问题 | 当前实现 | 效果与限制 |
| --- | --- | --- | --- |
| 力觉左右侧 | 数值按操作者侧直取，曲线/Tare 却按硬件侧转换，同一卡片可能混用两侧 | 卡片入口统一 `hardwareSideForOperatorSide` | 数值、危险色、曲线和操作侧一致；没有改变硬件接线或左右映射规则 |
| 应用配置 | 按钮误接不携带当前配置的 HAL 重连接口 | 恢复提交完整 `config`；增加等待、防重复、失败重试与编辑后清除旧成功提示 | 可以重新应用被拒绝的配置；重连成功不再冒充配置应用成功 |
| 回工作原点 | 共享 pending 被置空，重复点击和另一侧点击可连续发请求 | 恢复共享状态和同步互斥；跨 Tab 保留在途约束，成功/失败释放；后续安全代际阻止急停后续发 | 避免同一界面重复下发；不将 HTTP 返回等同于物理运动完成 |
| 存储与快照功能 | 拆分遗漏数据目录、录制 FPS 及部分快照操作 | 恢复 StorageCard、1–60 整数录制 FPS、全局快照恢复和两类快照删除 | 补回原有功能；录制 FPS 与相机采集 FPS 仍是不同配置。开机回原点功能已于 2026-09-14 按用户要求移除 |
| 运动使能显示 | MotionCard/ManualArmControl 各自保存乐观状态，API 成功即容易被理解为设备已使能 | 共用 `motionCommands` 和 `useMotionEnable`；sending → waitingConfirm → confirmed/failed/timeout；只认请求后到达、时间戳更新且六轴一致的新鲜反馈 | 两处显示一致；API 接受不等于已使能，右侧不可读反馈继续显示未知/未获得执行确认 |
| 运动请求生命周期 | 在途请求与后续相反方向动作可能相互覆盖；急停不能完整失效旧队列 | 有效同向请求合并，反向保留最新意图并等待旧 HTTP；显示超时不释放传输占用；使能在入队、续发、回包、遥测确认时均检查保护 | 不自动重试不确定的动作；全局急停独立立即发送并取消旧排队使能，常规断使能仍保留原 HTTP 顺序 |
| 夹爪状态语义 | 请求使能、API 接受、控制器运行、健康反馈混用；仅入队也显示反馈正常 | 分开显示请求启停、命令进度、反馈健康；queued 状态显示待确认，不计入健康数量 | 不将“已请求使能”或 worker 入队解释为硬件已使能；没有新增经实机验证的夹爪使能反馈契约 |
| 夹爪迟到结果 | 旧失败或延迟配置回读覆盖新命令、另一侧操作或新编辑 | 成功/失败/配置回读均核对请求和配置归属 | 旧响应不能把新状态回滚；两侧控制展示互不串写 |
| 录制进度条 | 底轨与填充同色，进度难识别；百分比语义丢失 | 恢复可辨识填充与 `progressbar` 百分比属性 | 显示与可访问读数一致 |

运动确认仍依赖“请求后收到匹配遥测”，没有新增控制卡逐命令采样确认，不能据此证明采样与该命令的严格因果关系。

源码入口：[运动命令状态机](/D:/E2EAPP_MicroMani/frontend/src/stores/motionCommands.ts)、[使能 hook](/D:/E2EAPP_MicroMani/frontend/src/hooks/useMotionEnable.ts)、[夹爪派生](/D:/E2EAPP_MicroMani/frontend/src/gripperDisplay.ts)、[存储卡](/D:/E2EAPP_MicroMani/frontend/src/views/settings/StorageCard.tsx)、[初轮修复证据](/D:/E2EAPP_MicroMani/output/fix-20260911/FIXES.md)。

## 五、急停优先与跨层控制安全

这部分已超出最初的前端展示调整：保留原有业务 API 和硬件映射，同时补充前端、后端、DDS、HAL 的保护条件与租约协议。因此原文“后端控制契约未改动”不再适用于最终版本。

| 控制链位置 | 修改前风险 | 当前行为与优化效果 |
| --- | --- | --- |
| 前端急停状态 | 立即改写危险值，请求失败仍可能显示已停；旧 ACK 覆盖新急停 | 急停意图、发送结果、错误和代际分开保存；先确保发出请求并同步锁定，失败/超时仍锁定。本标签页刷新保留意图，确认成功后仍等新的未锁存遥测 |
| UI / API 启动入口 | 未知使能或过期遥测可发点动，页面直调 API 绕过 store | 统一门槛覆盖手动、回原点、夹爪、遥操作、录制、自动策略；真实点动要求目标轴明确使能。停止、断使能、关闭力输出保留请求路径 |
| 前后端异步步骤 | 旧双臂回原点、粗调分段、录制启动或策略动作在急停后继续执行 | 原始停止代际贯穿 await、排队和实际发送；急停、策略停止及安全确认时清空待执行策略、关闭 auto，旧令牌不能被 ACK 复活；保留已录制 episode 缓冲 |
| 后端急停与 ACK | 清理等待或局部遥测错误妨碍 HAL 急停；HAL 自动力急停后旧后端任务仍有效 | 不等待策略清理完成再请求 HAL；异常逐项记录。ACK 本身建立新的停止屏障并清队列，失败保持本地锁存；不自动使能或回原点 |
| DDS 请求 | 丢回包导致非幂等动作重发；排队动作恢复后迟到执行 | 运动、启动、ACK、夹爪动作不自动重发；新后端动作带执行截止时间，HAL 消费时拒绝过期/非法时间。停止类保留明确的幂等重试 |
| HAL 执行边界 | 只在入口检查，等待或逐轴执行途中越过急停；分离读写 ACK 清掉新锁存 | 单一原子状态保存急停锁存及代际，ACK 使用 CAS；驱动、Native、夹爪、Omega、follower 在执行前后和等待中复查；旧 DDS 请求/目标被拒绝 |
| 数值与力输出 | NaN/Infinity、脉冲窄整数转换或差值溢出进入 SDK；迟到启用重新施力 | 有限值和整数范围检查先于设备访问；重力/力输出启用受代际约束，急停请求关闭双侧力输出 |
| Tare 与恢复 | 锁存后仍可去皮，最后样本先改零点再检测，可能掩盖超限 | 前端/HAL 拒绝锁存中 Tare；最后样本先用旧偏置检测，再复查代际后提交。停止、超限、配置变化取消旧窗口；显示不伪造零读数 |
| 生产安全展示 | 显示覆盖测试控件、旧组件库样式、顶部/底部状态可能掩盖锁存 | 生产关闭 dangerOverride 与模拟安全控件；急停文案区分未确认/失败/锁定；失联不显示正常，修复按钮文字与尺寸 |

没有修改既有物理阈值、软限位范围、轴序、脉冲单位或操作者/硬件左右转换。软件取消仅阻止尚未发送的后续步骤，不能承诺撤回已经进入设备 SDK 或控制器的动作。

源码入口：[前端门槛](/D:/E2EAPP_MicroMani/frontend/src/utils/controlSafety.ts)、[API 边界](/D:/E2EAPP_MicroMani/frontend/src/api/index.ts)、[后端停止代际](/D:/E2EAPP_MicroMani/backend/core/motion_safety.py)、[命令服务](/D:/E2EAPP_MicroMani/backend/services/command_service.py)、[DDS 策略](/D:/E2EAPP_MicroMani/backend/hal_client/protocol.py)、[HAL 原子急停](/D:/E2EAPP_MicroMani/hal/include/EmergencyStopState.h)、[急停与 Tare 审查](/D:/E2EAPP_MicroMani/output/safety-20260911/SAFETY_REVIEW.md)。

## 六、系统耦合与稳定性排查：故障如何被限制

系统仍使用浏览器、Python 后端和 C++ HAL 的现有进程结构。改进的目标是阻止异常意外扩散、限制阻塞资源，并在关键控制故障时主动联停；主动联停属于安全设计，不能写成“某模块出错后其他运动必须继续”或“系统已完全无耦合”。

| 边界 | 修改前问题 | 已实现的隔离/修复 | 保留的限制 |
| --- | --- | --- | --- |
| 路由与共享外壳 | 分块 404 或渲染异常可清空 React 根节点，连急停一起消失 | RouteErrorBoundary 隔离业务页；ModuleErrorBoundary 隔离日志、状态等显示模块，保留可用外壳和急停；最终版本同时撤销控制连接 | React 边界不能处理无限循环、内存耗尽或整个 renderer 退出；错误后不维持旧运动授权 |
| 共享遥测 store | 坏数组/非有限值/坏日志进入共享状态，使多个订阅者一起失败 | 接收边界校验主要结构、维度、数值及布尔状态；坏日志隔离。坏遥测撤销租约和可信状态；可识别的力锁存先进入保护，再做完整帧校验 | 不是全部配置/可选硬件字段的完整 schema 校验；不能把历史“坏帧后同连接好帧自动恢复”写成最终恢复方式 |
| WS 与配置归属 | 构造失败漏回收；旧连接、初始配置或 PICO 响应污染新连接 | 连接代际、当前 WS 身份与配置归属共同检查；失败对称清理，旧响应失效 | 后端和浏览器仍共享各自进程；断连后恢复必须重新建立当前会话 |
| Model / FineTune | 网络拒绝未捕获、旧响应覆盖、重复操作、卸载后继续刷新 | 捕获并显示错误、支持重试；请求序号与在途锁覆盖操作及后续刷新，保留 Stop/Cancel；真实模式不再用静态 ready 列表掩盖读取失败 | 页面成功提示不代表后端任务或设备动作完成 |
| Dataset | 删除/复核失败仍改显示；不同数据集同名 episode 相互污染；失败详情被永久缓存 | API 成功后才提交显示变更；以 datasetId + episodeId 标识；只缓存成功详情，提供重试，迟到错误不能污染新选择 | 使用离线数据夹具验证，不操作用户生产数据集 |
| 轻量 UI / Canvas | 排查 disabled、空 context、ResizeObserver 和清理是否引入崩溃 | 已核对原生 disabled 传递、空 context 返回和 observer 清理；未发现足以确认的新缺陷，保留现实现 | 这是检查结果，不列为额外已修复项，也不声称覆盖所有输入和性能极限 |
| 后端 WS 组帧 | 夹爪读取失败阻断后续运动/力帧和日志 | 夹爪边界局部降级为明确不可用，其余帧和日志继续发送，读取恢复后恢复反馈 | 局部显示可继续，相关运动是否允许仍由安全门槛决定 |
| HAL 健康与后端退出 | 首次健康读取失败漏清客户端，后续失败保留旧健康；一项清理异常跳过余下资源 | 健康失败立即离线、成功后恢复、finally 清理客户端；退出逐项捕获并继续独立资源清理 | 捕获异常不能解除永久阻塞；相机/配置/遥测原有线程池没有全部替换 |
| DDS 原生调用 | publish/cache 占事件循环，等待应答共用默认线程池；阻塞拖累其他模块 | 普通命令 4、急停 1、租约 1、状态 2、关闭 1 个独立有界槽；无队列，满即拒绝；超时后原生调用未返回仍占槽 | 线程限制数量，不能强杀 C 调用或隔离 DLL 地址空间破坏；关闭时不提前释放仍被使用的句柄 |
| HAL 线程/回调 | 标准或非标准 C++ 异常逃出入口；创建到一半失败、running=false 后漏 join | Native、Motion、HKVL、Force monitor、DDS 普通/紧急循环和 follower 补齐异常边界；部分启动回收、joinable 回收、HKVL 句柄作用域清理 | 关键故障先撤权再停机；不包含访问违规、原生内存破坏及系统崩溃 |
| HAL 停止与析构顺序 | 一步停止抛错跳过其余动作；发布器先于仍使用它的线程销毁 | 先原子撤销权限，再分别尝试各停止动作；DDS 接收延后到装配完成，正常退出/异常展开共用清理顺序 | 某个 SDK 永久不返回时，后续执行或 join 仍可能等待；异常隔离不等于阻塞隔离 |
| HAL HTTP 诊断 | 无界 detached 线程导致资源耗尽，创建失败漏连接回收 | 最多 32 个可回收 worker，异常回收 socket/槽位，检查收发超时 | 未进行真实 TCP 负载或吞吐量对照测试；真实运动仍走 DDS |

前端页面的最终隔离浏览器检查：Model/FineTune 两页 × 七种宽度，14 个失败/重试场景通过且 `pageErrors=[]`，见 [页面稳定性报告](/D:/E2EAPP_MicroMani/output/stability-20260911/FRONTEND_COMPONENTS.md)。这与布局阶段记录的 14 个未处理拒绝属于修复前后证据，不能混为同一次运行。

源码入口：[遥测输入验证](/D:/E2EAPP_MicroMani/frontend/src/stores/telemetryIngress.ts)、[页面异常边界](/D:/E2EAPP_MicroMani/frontend/src/components/RouteErrorBoundary.tsx)、[模块异常边界](/D:/E2EAPP_MicroMani/frontend/src/components/ModuleErrorBoundary.tsx)、[有界调用](/D:/E2EAPP_MicroMani/backend/hal_client/bounded_lane.py)、[DDS 客户端](/D:/E2EAPP_MicroMani/backend/hal_client/dds_client.py)、[线程异常边界](/D:/E2EAPP_MicroMani/hal/include/WorkerExceptionBoundary.h)、[HAL 生命周期](/D:/E2EAPP_MicroMani/hal/src/HalServer.cpp)。

## 七、现有架构内新增的失联保护

用户已确认控制卡为雷赛 DMC3000、DMC5C10，目前没有实体急停按钮，并明确不增加接线、硬件、独立服务或额外部署组件。本轮据此仅完善现有软件链，未新增浏览器 Worker 或第三方运行依赖。

| 场景 | 修改前不足 | 当前保护与实际影响 |
| --- | --- | --- |
| 浏览器主线程卡死 | TCP/进程存活不能证明操作界面仍能响应 | 后端每 500 ms 发随机挑战，只有主线程应答可续租；2 秒无有效应答即失效。计时器只撤销许可，不代替主线程回答 |
| 多窗口及迟到应答 | 健康窗口或旧应答可能替故障控制窗口持续授权 | 2026-09-14 起只接受一个控制浏览器；观察页不参与租约，其他控制连接以 WS 1008 拒绝。续租消耗当前控制会话的新 nonce；失效旧会话移出集合，旧应答和断开不影响新所有者 |
| 前端暂停后恢复 | 积压包可能赶在超时计时器前执行，恢复旧会话 | 使用单调时钟先检查旧期限；迟到确认不从接收时重新延长期限；全局 error/unhandledrejection 也撤销连接 |
| 后端失效或普通 DDS 命令阻塞 | 仅由前端/后端检查，执行侧没有同等存活依据 | HAL 启动即锁存并要求 2500 ms 租约；Guardian 在现有 HAL 内独立检查，续租复用现有 DDS 紧急 topic；执行前也检查租约 |
| 急停通道本身失败 | 正常心跳可能掩盖急停请求堵塞或无应答 | 急停堵塞、无应答、原生异常或 HAL 拒绝均使 DDS 进入故障隔离并停止续租；停止请求仍可尝试 |
| 通信恢复/ACK | 新心跳或迟到确认错误地解除保护 | 首次启动、过期、更换会话都先锁存；HAL 要求租约新鲜、待执行停止完成、代际匹配。后端 ACK 前后均检查当前会话；前端等待匹配确认和后续安全反馈 |
| 关键线程已退出 | ACK 清掉表面锁存，但关键工作线程并未恢复 | DDS、follower、服务中的 Motion 轮询或 Guardian 故障永久撤销本实例授权，需重启 HAL；Force 线程故障需重新配置重建；不靠普通 ACK 假恢复 |

运行行为已改变：关闭、卡死当前控制页面会触发撤权/停机，自动流程也受此约束；观察页退出不撤销控制者的租约。重新连接后仍须显式确认和使能，不自动恢复旧运动。500/2000/2500 ms 是软件工程参数，不是实测物理停机时间或制动距离。单控制会话、HTTP 归属与恢复条件见第十一节。

协议复用现有 WS 与 DDS 通道，增加 `safety_challenge`、`safety_heartbeat`、`control_lease` 消息及 `control.lease` 命令；本阶段未改 DDS IDL。前端、后端、HAL 必须配套更新，缺失新协议时拒绝运动，不静默绕过保护。本次没有替换现场运行二进制。

源码与设计入口：[前端租约](/D:/E2EAPP_MicroMani/frontend/src/stores/controlLease.ts)、[后端监控](/D:/E2EAPP_MicroMani/backend/services/control_watchdog.py)、[HAL 租约状态](/D:/E2EAPP_MicroMani/hal/include/ControlLeaseState.h)、[HAL 守护线程](/D:/E2EAPP_MicroMani/hal/include/ControlLeaseGuardian.h)、[完整设计与验证](/D:/E2EAPP_MicroMani/output/resilience-20260911/DESIGN_AND_VALIDATION.md)。

## 八、2026-09-11 验证记录与证据口径

本次文档整理核对了源码、既有 JSON/日志及链接，没有为了文字调整重新启动服务或重复完整测试。下表保留 2026-09-11 阶段各范围最后一次已保存验证；2026-09-14 的后续结果见第十、十一节。不同阶段与不同组合存在重叠，不相加成“全部系统通过数”。

| 验证范围 | 最后已保存结果 | 证据与限制 |
| --- | --- | --- |
| 前端全套 | **341/341，通过；0 失败、0 跳过** | [测试 JSON](/D:/E2EAPP_MicroMani/output/resilience-20260911/frontend-tests.json)；已覆盖新增租约及拒绝路径 |
| App 专项 | **82/82 通过**，并已进入最终全套 | [单独复跑](/D:/E2EAPP_MicroMani/output/resilience-20260911/app-rerun.json)；原文约 21 项遗留失败已过时，不保留为当前问题 |
| 前端类型、构建及 lint | typecheck、生产 build、相关改动文件定向 ESLint 通过 | [构建日志](/D:/E2EAPP_MicroMani/output/resilience-20260911/frontend-build.log)；未声称全仓 lint 通过，早期设置域既有规则问题见历史报告 |
| 布局 | 七种宽度、77 个组合，记录中的输入框/主体无横向溢出；数据集有数据场景另四种宽度 | [布局结果](/D:/E2EAPP_MicroMani/output/layout-20260911/final.json)、[数据集结果](/D:/E2EAPP_MicroMani/output/layout-20260911/dataset-final.json)；布局阶段网络异常随后另行修复 |
| 浏览器主线程故障 | CDP 暂停 2.8 秒、renderer 崩溃、未处理 Promise 拒绝三场景，故障后均无新增有效心跳 | [故障记录](/D:/E2EAPP_MicroMani/output/resilience-20260911/browser-main-thread.json)、[前端报告](/D:/E2EAPP_MicroMani/output/resilience-20260911/FRONTEND.md)；使用 Node 协议替身，非实际后端/HAL 停机时间 |
| 后端最终定向组合 | **65/65**；既有 WS/急停/ACK/关闭路由 **9/9** | [准确命令及结果](/D:/E2EAPP_MicroMani/output/stability-20260911/CONTROL_LEASE.md)；真实应用路由 + Socket/HAL 替身覆盖 WS→租约→ACK→断连急停，不是完整后端集合 |
| HAL 离线行为 | ForceCore 11 组、EmergencyStop 9、ThreadStability 6、WorkerResilience 14、ControlLease 9，五个程序均通过 | [HAL 报告](/D:/E2EAPP_MicroMani/output/resilience-20260911/HAL.md)；完成离线编译、链接、运行，关闭 vendor/DDS 宏；200 次 stop/ACK 竞态包含在对应程序中 |
| HAL 源码契约 | **113/113 通过** | [日志](/D:/E2EAPP_MicroMani/output/resilience-20260911/hal-source-contracts.log)；源码契约不替代 SDK 编译 |
| 真实 vendor 宏 | **11 个 C++ 源文件仅编译通过** | [编译日志](/D:/E2EAPP_MicroMani/output/resilience-20260911/hal-vendor-compile/compile-vendor.log)；最新 HalServer 另补编译，无完整服务链接或实机运行 |

历史后端 179 项、202 项、12/6 项及最终 65/9 项包含重叠用例，不累计。历史完整 `test_app.py` 曾为 157 过、4 跳过、5 项缺 DDS binding 的环境失败，后续只重跑相关组合，不能记为已全套通过。详见 [后端验证范围](/D:/E2EAPP_MicroMani/output/safety-20260911/BACKEND_VALIDATION.md)。

## 九、尚存边界与未验证内容

1. **C++ 异常边界已补齐所列入口，进程级风险仍在。** DLL 访问违规、原生内存破坏、HAL 被强杀、Windows 整体冻结或断电，不能由同一软件进程的 try/catch 保证处理。不得继续把这些与“尚未给所列线程加异常边界”混为一项。
2. **永久阻塞仍可能阻止物理停止或资源退出。** 后端限槽、撤销租约和 HAL 软件锁存限制后续动作；已经进入 SDK 的调用无法安全强杀。若负责物理停止的 SDK 自身永久堵塞，不能承诺设备已静止。夹爪待发命令取消也不等于已接收动作被撤回或物理断能。
3. **DDS 完整验证仍缺失。** 当前所需 `F:/opt/ros/jazzy` SDK 路径缺失，DDS 宏分支、完整 HalServer 链接及真实 listener 生命周期未完成验证；vendor 宏编译和源码检查不替代这一部分。
4. **未做真机与长期压力验收。** 没有真实 USB/DDS 故障注入、串口故障、物理停机时间、各轴制动距离或长期负载测试；未对当前无实体急停的硬件作安全保证。按已确认约束，不把新增硬件或接线列为本轮实现或待办。

各阶段原始记录保留用于追溯；其中阶段性的失败数量、未完成项和恢复行为若与后续实现冲突，以本总结所列当前源码及最后验证记录为准。

## 十、2026-09-14：移除开机自动回工作原点

用户要求移除开机自动回工作原点的执行逻辑和界面开关，已完成：

- 后端启动事件删除 `home_all()` 自动执行分支，仅保留残留 Native 遥操作的停止清理；无需通过浏览器租约或安全确认后补跑开机动作。
- HAL 设置卡删除“开机回工作原点”开关及模式标签；前后端默认配置、前端类型删除 `motion.homeOnStartup`，旧文件、旧客户端配置及快照恢复经配置读取/保存时清除该字段。前端归一化也丢弃旧字段。
- 删除四个启动/部署脚本中的 `SkipStartupHome` 参数、环境变量和传递逻辑，启动不再需要“跳过回原点”选项。
- 遥操作准备不再读取已移除的开机模式字段；其独立的 `teleop.homeBeforeStart` 仍保留。手动回工作原点、工作原点记录、录制复位、HAL 急停锁存和租约保护均保留。

本次实际验证：前端 App、设置回归及配置归一化 **101/101**；后端启动旧配置回归 **3/3**，遥操作/默认配置/脚本/源码契约 **231/231**；typecheck 和生产 build 通过，四个 PowerShell 脚本语法解析通过。启动回归只执行目标协程，使用替身验证旧开关为 true 且旧跳过环境变量未设置/false/true 时均不发运动命令，未启动设备生命周期。

第八节保留 2026-09-11 的整套与阶段验证记录，本次没有重新执行前端或后端全部集合；没有运行新 HAL 二进制或连接真机。后端逻辑在加载更新后的代码后生效，前端产物已重新构建。

## 十一、2026-09-14：控制恢复与审查整改

本轮依据附件审查逐项核实当前源码，已完成下列修复与调整。附件中的建议仅作为审查材料；未确认的问题、已移除的逻辑和有意保留的保护措施不计为新增修复。详细设计、源码入口及验证范围见 [控制恢复与审查整改](/D:/E2EAPP_MicroMani/docs/CONTROL_RECOVERY_2026-09-14.md)。

| 范围 | 已确认的问题 | 本轮修改与当前行为 |
| --- | --- | --- |
| 控制会话恢复 | 失效但尚未关闭的旧 WS 留在控制集合，阻止新会话恢复；旧回调可能干扰新控制者 | trip 后移除旧会话；迟到应答、发送失败和断开核对会话归属。一个后端只接受一个控制浏览器，其他控制连接以 WS 1008 拒绝，前端不自动重试抢占 |
| 观察与 HTTP 所有权 | 控制请求没有充分绑定发起页面，可能借用另一页面的有效租约 | 受保护 HTTP 请求携带 `X-Control-Session` 并在后端核对所有者；观察页使用 `?mode=observe`，跨路由及重连保持观察身份。退出释放/停栈请求也绑定最后控制会话，旧页面和观察页不能释放新控制者的资源 |
| 安全恢复 | 重连入口不足；停止请求失败后仅凭租约恢复不足以允许 ACK | 增加显式重连/页面重试入口；ACK 还要求停止任务结束且停止已确认。新租约不清除急停、不恢复使能或旧动作；全局脚本异常仍撤权 |
| 后端执行准入 | 部分服务入口与配置路径缺少统一租约检查，并发操作可竞争同一运动资源 | 共享安全门槛同时检查浏览器/HAL 租约、急停及停止代际；按侧互斥覆盖使能、手动、回原点、原点修改、策略派发、遥操作及录制启动/重置。同一任务嵌套可重入，冲突新操作直接拒绝；停止、断使能和关闭力输出仍可请求 |
| 控制配置及快照 | 控制参数可能在无控制权或运动期间变更，与在途动作使用不同配置 | 涉及运动、力、遥操作、夹爪、自动控制或 HAL 的更新要求有效租约、新鲜静止反馈和资源互斥，并在保存前复查停止代际；快照应用遵循相同规则，普通显示配置不要求租约 |
| 回工作原点与录制准入 | HAL 接受/返回容易被当作实际到位；人工勾选或历史确认不能证明当前原点状态 | 原点捕捉、恢复和清除要求新鲜静止反馈。回原点等待请求后的六轴停止反馈，距目标不超过 1 pulse，确认窗 2 秒、反馈最大年龄 500 ms。前端取消右侧 Yaw 例外及聚合使能兜底；PreCheck 勾选须已有所需侧到位确认且无在途回原点，录制启动/跳过复位在服务端再次核实 |
| 请求超时与重复动作 | 请求可能长期挂起，不同入口可重复回原点；中断结果误示为从未派发 | API 增加有界等待和 AbortController：普通命令 10 秒、回原点 80 秒；受保护命令超时撤权并标记结果未知。共享 API 防止重复回原点；策略已进入 HAL 后遇到停止，保留已派发事实及 interrupted 结果，不自动重发 |
| 数值与策略边界 | 非有限数、负步长、非法方向/速度或过期配置可能进入执行路径 | 手动及策略拒绝非法数值；位置、原点、限位须有限且限位有序。策略在实际派发前使用最新配置复查轴使能、位置边界和停止代际 |
| 录制中断与数据保留 | 安全中断可能丢失保存入口，清理异常也可能干扰急停流程 | 急停同步关闭新帧入口，异步停止来源并排空已接受帧；保留未保存 episode，不直接执行清空缓冲的 finish。清理异常不阻断硬件急停；前端新增 interrupted 状态，保留保存/丢弃/结束入口，重连或重载可同步中断会话，恢复控制不自动重启录制 |
| Test HAL 与展示 | 替身恒成功掩盖租约拒绝路径；固定状态文字与反馈不一致 | Test HAL 模拟所有者、租约期限、序号重放拒绝、急停及显式 ACK；补充运动状态模拟。顶栏频率读取遥测，移除固定“检测通过”，轴数组订阅改用浅比较；替身不等于完整 HAL 仿真 |

当前恢复顺序为：重新连接 → 完成新的主线程挑战 → HAL 确认有效租约 → 停止任务结束且停止已确认 → 操作者显式安全 ACK → 重新使能。前端超时仅结束客户端等待，不能撤回已进入 SDK 的动作；停止失败或 DDS 隔离时不能把重连成功显示为可恢复运动。

本轮核实后保留或更正的结论：

- `homeOnStartup` 已按第十节移除，本轮没有恢复开机动作或增加绕过浏览器租约的无头控制通道。
- 每侧六轴是当前硬件契约，后端保留六轴要求；不按缺失反馈推断“未安装轴”。PreCheck 原有人工勾选并未覆盖全部自动检查，本轮收紧的是回原点证据及后端录制准入。
- DDS 超时/结果未知后的故障隔离保留，原生调用未返回时继续占用有界槽；急停 lane 容量仍为 1，不强制释放、不增加无界重试线程。双侧关闭重力/力输出可走隔离后的停止路径。
- watchdog 仅对已经返回的停止失败有限重试，DDS 已隔离时不重试；关闭时对停止及发送任务有界等待。保守租约截止时间保留，不把 ACK 往返耗时补回有效期。HAL Guardian 的 2500 ms 仍不代表物理停止完成时间。

本轮实际验证如下，均为整改执行阶段的结果；本次补充文档只复核记录与链接，未重新运行业务测试。

| 验证范围 | 结果 | 范围与证据 |
| --- | --- | --- |
| 后端相关回归 | **223 项通过**，40 条现有 FastAPI 生命周期弃用警告 | 七个文件：`test_review_remediation`、`test_control_watchdog`、`test_control_stop_races`、`test_dds_blocking_isolation`、`test_dataset_recorder`、`test_teleop_mapping`、`test_hal_dds_client`；覆盖租约恢复、HTTP 所有权、资源冲突、停止竞态、数值拒绝、原点确认、录制保留与 DDS 隔离，不代表后端全套通过 |
| 前端全量回归 | **349/349 通过，0 失败、0 跳过** | `npm --prefix frontend test -- --maxWorkers=4`；[本轮测试 JSON](/D:/E2EAPP_MicroMani/frontend/output/review-remediation-all-tests.json)。此结果更新第八节历史 341 项记录，不与历史数量累加 |
| 前端静态检查 | typecheck、相关修改文件 ESLint 通过 | 不等于全仓 lint 通过；本轮没有重新执行生产 build，第十节的构建结果属于上一轮修改 |
| 测试夹具调整 | 懒加载页面等待上限 10 秒；模拟 DDS 丢应答用例发布窗口 100 ms | 避免并行懒加载/原生线程调度延迟造成误判；未放宽生产租约时限 |

本轮修改涉及前端、Python 后端及 DDS 客户端适配，未修改 HAL C++ 保护实现，未进行 C++ 重编译、真实 DDS 运行、实机运动或浏览器连接实机的视觉验收；第九节的实机及原生阻塞边界仍适用。旧 API 测试或外部调用若未建立控制会话，需按新契约迁移，不能通过关闭生产租约校验兼容。代码未提交、推送或部署；历史构建/离线验证不能视为本轮设备验收。

## 十二、2026-09-17：HKVL 主用默认与 NI-DAQ 备用

按用户要求，将 HKVL 设为主要使用路径，NI-DAQ 保留为可手动选择的备用路径；打开界面时默认显示 HKVL 配置。已同步修改默认值和本机持久化配置，保留 NI-DAQ 的通道、标定文件及其他参数。本轮没有新增故障自动切换逻辑，也不会在之后重新打开页面时强制覆盖用户明确保存的 NI-DAQ 选择。

| 范围 | 修改前 | 本轮实现 |
| --- | --- | --- |
| 前端初始配置与遥测 | `defaultConfig.force.source` 和初始遥测来源使用 `nidaq` | 默认来源改为 `hkvl_serial`，初始遥测来源跟随默认配置，避免首次显示与默认选择不一致 |
| 设置页 | 两种数据源可选，但默认进入 NI-DAQ 界面 | 数据源选择器将“HKVL-36A / HAL 串口（主用）”置于首位，将“ATI Nano-17 / NI-DAQ（备用）”保留为另一选项；所选来源决定串口或 DAQ 通道界面的显示 |
| 配置保留 | 已保存的本机配置仍选择 NI-DAQ | 本机 `backend/runtime/config.json` 的 `force.source` 已改为 `hkvl_serial`，其他配置保留；用户以后显式选择 NI-DAQ 时，配置归一化、保存和重载仍保留该选择 |
| 后端默认与采样分支 | 默认配置及多处缺少 source 时的回退判断使用 NI-DAQ | 默认配置、力配置校验、硬件状态、命令服务、遥测、稳定性监测、录制及 WS 力状态分支的默认来源统一为 HKVL；显式 NI-DAQ 分支继续保留 |
| 启动器 | `start-hal.ps1` 无来源配置时默认 NI-DAQ | 默认改为 HKVL；配置文件中的显式来源仍可覆盖脚本默认值，现有 HKVL PnP 身份解析逻辑保留 |
| HAL 原生默认 | `ForceRuntimeConfig.source` 默认 `nidaq` | 改为 `hkvl_serial`，JSON 显式指定 `nidaq` 时仍可选择备用路径；未改动端口绑定、轴方向、阈值或保护逻辑 |

源码入口：[前端默认配置](/D:/E2EAPP_MicroMani/frontend/src/data.ts)、[初始遥测与配置归一化](/D:/E2EAPP_MicroMani/frontend/src/stores/telemetry.ts)、[力传感器设置卡](/D:/E2EAPP_MicroMani/frontend/src/views/settings/ForceSensorCard.tsx)、[后端默认配置](/D:/E2EAPP_MicroMani/backend/core/defaults.py)、[力配置校验与 HAL 参数](/D:/E2EAPP_MicroMani/backend/core/force_config.py)、[启动脚本](/D:/E2EAPP_MicroMani/scripts/start-hal.ps1)、[HAL 默认值](/D:/E2EAPP_MicroMani/hal/include/ForceControlRuntime.h)、[本机当前配置](/D:/E2EAPP_MicroMani/backend/runtime/config.json)。

本轮新增或调整的回归覆盖：默认 HKVL 界面、主备双向切换、切换设置 Tab 后保留选择、操作者侧与硬件侧字段对应、显式 NI-DAQ 配置保存与重载、缺少 source 的旧配置采用 HKVL、HKVL 状态缺失时不自动启动 NI-DAQ 采样，以及 HAL JSON 对默认来源的覆盖。原有 NI-DAQ 专用测试已明确选择 `nidaq`，继续验证备用路径。

以下结果来自本轮代码修改阶段；本次补充 Markdown 没有重新运行业务测试。与前面各节的历史数量不累计，也不代表前后端全量测试通过。

| 验证范围 | 实际命令或方式 | 结果 |
| --- | --- | --- |
| 前端默认、设置与备用路径 | `npm --prefix frontend test -- src/data.test.ts src/stores/telemetry.test.ts src/views/SettingsView.regressions.test.tsx src/hardwareStatus.test.ts src/App.test.tsx` | **5 个文件、114 项通过** |
| 后端默认、力配置、NI-DAQ 驱动及遥测 | `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_force_config.py backend/tests/test_hardware_defaults.py backend/tests/test_force_axis_calibration.py backend/tests/test_force_nidaq_driver.py backend/tests/test_telemetry_hub.py backend/tests/test_stability_monitor.py -q` | **45 项通过** |
| 录制力采样与元数据 | `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_dataset_recorder.py -k 'force or hkvl' -q` | **3 项通过，73 项未选中** |
| 启动脚本契约 | `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_stack_scripts.py -q` | **32 项通过**；与上两行合计 **80 项**后端及脚本测试 |
| PowerShell 配置分支 | 语法解析，以及仅执行配置片段验证无配置、缺 source、显式 HKVL、显式 NI-DAQ 四种情形 | 通过；未执行 PnP 查询、串口或启停逻辑 |
| HAL 力控离线测试 | MSVC 在独立输出目录编译并运行 `ForceCoreTests` | 通过；见 [编译日志](/D:/E2EAPP_MicroMani/output/hkvl-default-20260917-ae185158/build.log)、[测试日志](/D:/E2EAPP_MicroMani/output/hkvl-default-20260917-ae185158/test.log) |
| 前端类型与生产产物 | `npm --prefix frontend run build`，包含 `npm run typecheck` | 类型检查与生产构建通过 |
| 本机保存配置 | 对保存的 JSON 执行力配置校验、`AppConfig` 校验及 HAL payload 检查 | HKVL 已选中；NI-DAQ 通道及标定路径仍保留 |

生效与验证边界：前端产物已重新构建，本机当前配置已保存；下次通过正常启动器启动时按 HKVL 来源配置运行。本机 `backend/runtime/config.json` 属于 Git 忽略文件，源码默认值更新不等同于自动覆盖其他机器已经保存的显式 NI-DAQ 配置。HAL 源码默认值已修改并完成相关离线测试，但未重新构建或部署正式 `HalServer.exe`；直接运行旧二进制且不提供来源配置时，不能据此断言其内置默认值已更新。

本轮没有启动 HAL 服务、连接设备、下发控制命令或进行实机采样验收。原生测试没有调用 `start()` / `initialize()`；仅在未运行的 runtime 上测试配置、样本与保护状态，并关闭 vendor/DDS 编译宏，没有覆盖现有 HAL 运行二进制。离线通过不代表已确认 HKVL 现场采样、左右绑定或通信稳定性。

## 十三、2026-09-18：按图片范围整合远端改动

用户最终限定：本轮只整合截图列出的九项。来源为 `ccoxo/E2EAPP_MicroMani` 的 `codex/hal-native-teleop-v2` 分支 `aabe6734b2ad39945641fe8a53e107c8d3409b76`，本地 HEAD 仍为 `6d898c9df4a3b13b415e3dabd75591ed2aabc2ac`。采取逐项移植，保留本机已有未提交修改；没有执行整分支覆盖、Git 提交或 push。

| 图片项目 | 本轮结果与本地适配 |
| --- | --- |
| 数据集与 Policy 顺序 | 新增共享 `backend/core/data_contract.py`。录制 state/action/pulses/force 和 Policy API 使用 `appstation.dual_arm.operator_sides.v2`；HAL、内部遥测、原点与标定保持硬件侧，backend 负责边界转换，相机角色不随数值通道交换 |
| 保持位置动作 | 录制状态与在线 observation 使用一致的侧别、14 维布局和旋转单位。原值作为 action 时差值为零；操作者侧选择在下发时映射到对应硬件侧，沿用原安全门槛和限位检查 |
| 原点归一化 | `scripts/normalize_origin.py` 校验契约，将硬件侧原点映射到数据集顺序，再使用对应硬件侧的换算系数；同原点保持值不变。默认 dry-run，未处理用户真实数据 |
| native 旧集续录 | 已识别的 native 数据集缺少、未知或不匹配契约时拒绝续录；新集向两份 meta 信息写入完整契约。兼容数据集走 resume，保留本地录制启动、原点、安全代际和中断保留逻辑 |
| HKVL 默认 | 保留第十二节已完成的 HKVL 主用、NI-DAQ 手动备用；本轮核验默认和显式 NI 配置，没有覆盖本机 runtime 配置、端口或相机身份 |
| HAL 能力上报 | 新增统一版本头 `HalVersion.h`，health 序列化和 HTTP/DDS 客户端支持 capability 字段，`/api/health` 返回能力诊断。仅报告已实现能力，详见下面的限制 |
| 腕相机识别 | 使用本地轻量 UI 接入视觉设置，支持候选预览、稳定身份绑定、保存及刷新；拒绝全局身份不明确或两腕指向同一设备。录制启动、会话活跃及写线程收尾时拒绝识别/绑定 |
| 相机曝光/FPS | Windows 自动曝光时尝试关闭暗光动态降帧，覆盖初次打开和已打开 direct capture 切回 Auto 的路径；失败记录告警，实际 FPS 仍需现场测量 |
| 上传文档 | 新增 `docs/data-contract-and-deployment.md`、`docs/github-upload-contract.md`、PR 模板，向根 `AGENTS.md` 追加架构约束；保留原协作规则，明确测试、部署、回退及未验证范围 |

整合时相机回归发现：旧配置迁移仅凭 `IMX335 / index` 标签识别旧默认，会把刚保存的新腕相机身份覆盖。本轮增加自定义 identity 优先保护，并验证旧默认仍可迁移、新绑定保存与重载不被重置。这属于图片中相机绑定落地所需的兼容修复，未导入远端作者机器的身份默认值。

HAL 本轮只迁入版本与实际能力上报，**未迁入双阶段 Tare、校准状态机或 ACK 自检门控**。本地版本为 `hal-real/0.2`，能力列表为空，不声明 `force_calibration_state_v1`；诊断将显示缺失，不能仅凭版本号判定该功能可用，也没有新增录制/运动入口的强制版本检查。原有 Tare、ACK、ForceRuntime、租约和执行代际逻辑保留。

本轮没有扩展到此前审计列出的 legacy 预览/上传契约补漏、读取错误的 API 适配、启动器时间戳或关页停栈策略，也没有修改外部 `act_deploy.py`。该外部程序需要传递完整 `dataContract` 后才能调用更新后的 Policy action API；仓库内前端使用的 `/api/auto/action` 单轴队列不受这项接口调整影响。

### 实际验证

以下为本轮离线验证，不与历史结果相加，也不代表后端全套或设备验收。

| 范围 | 结果 | 证据和边界 |
| --- | --- | --- |
| 数据契约、录制、策略、归一化、单位 | **105 passed、1 skipped** | [日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/recording-contract-tests.log)。跳过项需要未安装的 `pyarrow`；native 写入/续录使用替身，未安装真实 `lerobot` |
| Policy API、本地停止竞态、默认与力配置 | **59 passed** | [日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/api-and-preserved-safety-tests.log)。包括缺契约拒绝、真实 mock 控制租约、目标硬件侧及保持位置零增量 |
| 相机识别、配置保存、曝光、格式 | **31 passed** | [日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/camera-pytest.log)。包括显式身份保存重载、临时预览回收、录制启动/收尾拒绝；相机和系统调用均使用替身 |
| HAL health 与 DDS Python 客户端 | **24 passed** | `test_hal_health_capabilities.py`、`test_hal_dds_client.py`；覆盖旧字段兼容及能力传递，没有真实 DDS 连接 |
| 前端相机组件与视觉页整合 | **3 文件、10 passed** | [日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/camera-vitest.log)。保存只同步已落盘状态，不发送第二次整配置 PUT |
| 前端设置与 API 生命周期 | **3 文件、18 passed** | [日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/frontend-settings-lifecycle-tests.log)。保留设置切换、关闭释放及控制请求行为 |
| 前端静态检查和产物 | typecheck、生产 build、相机新增文件定向 ESLint 通过 | [构建日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/frontend-build.log)，非全仓 lint |
| HAL health 离线程序 | 编译和运行通过 | [编译](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/hal-health-build/build.log)、[测试](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/hal-health-build/test.log)；DDS/vendor 均关闭，未启动 HAL |
| Windows 相机属性助手 | C# 仅编译通过 | 未执行 COM 枚举、绑定或设备属性写入，不能证明现场驱动支持该属性 |
| HAL 两个目标隔离构建 | core 与 `JodellGripperWorker` 通过；**`HalServer` 未通过** | VS2022 Professional，DDS/vendor 均关闭。原有 DDS OFF 分支仍无条件包含缺失的 `fastcdr/Cdr.h`；相关源码与备份一致，本轮没有扩修。见 [HalServer 日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/hal-skeleton-build/build.log)、[Worker 日志](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/hal-skeleton-build/worker-build.log) |

本机没有完成正式 HAL 配套构建或部署。上述 Worker 仅为关闭 DDS/vendor 的隔离产物，未运行，不能代替现场程序；部署时仍需在具备完整 SDK 的环境从同一源码构建两个正式 EXE。没有启动服务、连接真实相机/串口、进行运动或真实数据录制。

### 备份与撤回依据

操作前已保存包含原有未提交源码、文档、本机 runtime 配置和本总结的 [备份 ZIP](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/before-integration.zip)，共 378 个文件；[原始哈希清单](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/before-manifest.json) 用于确认原值。备份不包含数据集、依赖、SDK 或运行二进制。

本轮独立差异见 [integration.patch](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/integration.patch)，逐文件前后哈希见 [integration-manifest.json](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/integration-manifest.json)。撤回时按该清单恢复本轮修改文件，并核对后处理本轮新增文件，保留整合前已有的用户修改。若之后又改过同一文件，应逐项合并撤回；不能对整个工作区执行 `git reset --hard`。前端撤回后需重新 build；代码撤回不会自动迁移或删除新契约数据。

最终差异为 35 个文件：修改 21 个、新增 14 个、删除 0 个。已核对本机 runtime 配置及 16 个重点保护文件 SHA-256 不变；本轮补丁反向 `--check` 通过（只检查，未撤回），新增行空白和新增文档链接检查通过。本地 HEAD 未变、没有暂存提交内容。见 [最终检查记录](/D:/E2EAPP_MicroMani/output/remote-integration-20260918-092932/final-checks.json)。
