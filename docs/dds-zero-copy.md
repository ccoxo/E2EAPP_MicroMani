# HAL 高频 DDS 零拷贝传输

本次将 `TeleopLeaderPublisher → TeleopMappingNode → TeleopFollowerTargetSubscriber`
的两个高频 Topic 改为 Fast DDS 2.14 Data Sharing + writer/reader loan。
默认 domain 42、控制权仲裁、急停通道及其最高优先级保持不变。

## 范围与内存生命周期

- 主手使用新 Topic `AppStation.Teleop.LeaderState.V2`，固定布局 672 字节，直接填充 writer loan。
  去除每帧 JSON 编码/解析；只携带映射需要的字段。错误文本最多 255 字节，设备信息仍走 OmegaState。
- `AppStation.Teleop.HardwareTarget` 保持原有字段和线缆顺序，固定布局 264 字节。
  已计算目标复制进 writer loan，随后 DDS 共享该样本。
- 两端显式开启 Data Sharing，采用预分配池、Best Effort、Volatile；主手保留最新 1 帧，目标历史深度 8。
  无法满足 Data Sharing 条件时创建端点失败，避免默默退回未优化配置。
- 成功发布后不再访问 writer loan；填充/发布异常归还 loan。reader loan 通过 RAII 在退出和异常时归还。
- 执行前保留一次小型稳定快照，复制前后检查 `is_sample_valid`，失效样本不执行。
  Data Sharing 样本被读取后可能被发布端复用，不能让硬件调用持续读取共享数据。

这里的“零拷贝”指 DDS 共享样本传输与借用读取，不代表整个应用没有复制或内存分配。
当前所有 participant 仅启用 SHM，发现与数据都不使用 UDP/TCP，不再支持跨机器通信。
后端 CommandRequest/Reply 与 JSON 遥测未改为固定布局；它们现经 SHM transport 传输，但仍需要序列化，不能称为端到端零拷贝。
Fast DDS 的进程内投递保持默认设置，本次没有修改全局 DDS 调度策略。

实现集中在 `hal/include/TeleopDdsPlainTypes.h`。布局测试验证原生内存与 XCDRv1
字段偏移逐字节一致，不能只依赖 `is_plain=true` 的声明。
Leader V2 是不兼容的 Topic 升级，发布和映射节点必须一起更新；旧 JSON 订阅者不能消费 V2。

## 验证

从仓库根目录运行：

```powershell
cmd /c hal\tests\run_dds_zero_copy_tests.cmd
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_hal_source_contracts.py backend/tests/test_hal_protocol.py backend/tests/test_hal_dds_client.py -q
```

DDS 测试独立使用 domain 191 和合成数据，不链接设备驱动。包含布局/字段回环、
异常时 writer loan 回收、reader 借用验证、连续 300 次收发与序列化调用计数。
每种模式独立进程运行；等待两端发现匹配后再发送。前 20 次预热不计入延迟。
CMake 也注册 4 种纯共享内存模式，运行时需要 Fast DDS 及其依赖 DLL 位于 PATH；本机脚本设置 ROS/Pixi 路径。

以下为首次优化时的历史微基准，单位 μs；网络对照实现现已删除，当前测试仅运行 SHM/Data Sharing。

| 目标消息路径 | p50 | p95 | 序列化 / 反序列化次数 |
| --- | ---: | ---: | ---: |
| UDP 回环，普通 write/take | 19.9 | 21.6 | 300 / 300 |
| 原进程内方式，普通 write/take | 5.7 | 5.8 | 300 / 300 |
| Data Sharing + loan，关闭进程内投递 | 8.3 | 9.8 | 0 / 0 |
| Data Sharing + loan，默认进程内投递 | 2.5 | 3.2 | 0 / 0 |
| Leader V2，Data Sharing + loan，默认进程内投递 | 2.6 | 2.8 | 0 / 0 |

这是同进程两个 participant 的合成微基准；关闭进程内投递的模式用于单独验证 Data Sharing。
没有测量跨进程、真实主手采样、驱动/总线、线程负载或机械响应，也没有设定时延保证。
原进程内基线复现普通非 plain 类型的序列化与复制读取方式，不是完整旧 HAL 的性能回放。

Python 契约回归 181 项通过。真实 SDK/Fast DDS 的 HAL 与夹爪 worker 仅构建候选程序，
不替换现场程序、不启动硬件。此前仲裁测试记录的两项旧力自检测试失败见
[运动执行器说明](motion-executor-architecture.md)，不能把全套旧测试描述为全部通过。

协议依据：[Fast DDS 2.14 Data Sharing](https://fast-dds.docs.eprosima.com/en/2.14.x/fastdds/transport/datasharing.html)、
[Zero-Copy](https://fast-dds.docs.eprosima.com/en/2.14.x/fastdds/use_cases/zero_copy/zero_copy.html)。

## 后续优化：全本机共享内存

`hal/include/LocalDdsTransport.h` 统一配置 HAL 的 4 个 participant 和后端 DLL 的 participant：
清空继承的 user transports、禁用 builtin transports，仅注册 `SharedMemTransportDescriptor`。
每个 participant 的传输 segment 为 8 MiB，为控制面 1 MiB 样本预留及并发消息留出空间。
这不是 JSON 长度上限，也不是任意负载下的无丢包承诺；原有 reliability/history 策略保持不变。

高频 Leader V2 / HardwareTarget 继续使用 Data Sharing + loan，控制面 8 个 Topic 使用 SHM transport。
普通命令、急停/租约、遥测仍由各自线程处理；急停仍先撤销权限、锁存并停止。
后端 ctypes 缓存复制和 JSON 编解码仍存在，原来的轮询周期也未改动；本次不声称消除了全部应用延迟。
诊断 HTTP、浏览器 API、设备 SDK/串口不属于 DDS 传输改造范围。

跨进程验证命令：

```powershell
cmd /c hal\tests\run_shared_memory_tests.cmd
```

该脚本在 `hal/build/shm-tests` 构建真实后端传输 DLL 和禁用设备 SDK 的控制服务测试程序，
通过独立进程运行真实 `HalDdsControlServer` 与 Python `FastDdsHalTransport`，使用 160–189 测试域，
不使用现场 domain 42、不初始化设备、不启动主手或运动循环。检查：

- 共享内存发现、全部 5 种遥测和命令应答；256 KiB 请求名称及同等大小的错误应答完整往返。
- 普通使能命令不能借急停 Topic 提权；执行器与驱动状态锁均被占用时，急停仍收到应答并锁存。
- 急停后普通使能请求被拒绝；HAL 测试进程退出并重启后，已有后端收到新遥测并恢复应答。

本轮跨进程测试、4 种纯共享内存测试、220 项 Python 回归、11 项仲裁测试通过。
真实 SDK 版本的 HalServer/JodellGripperWorker 和后端 DLL 已在 2026-09-20 17:10 同步部署并启动。
候选 HAL/worker 位于 `hal/build/motion-architecture`，候选后端 DLL 位于 `hal/build/shm-tests`；正式程序及 `.next` 均已同步。
纯 SHM 与旧 UDP-only 节点不能互相发现，正式更新必须同步部署后端 DLL、HAL 与配套 worker，
并重启两端；同机进程应保持相同运行账户/权限。`-LanDiscovery` 已移除，旧 LAN 环境变量不再影响传输。

机制依据：[Fast DDS 2.14 SHM transport](https://fast-dds.docs.eprosima.com/en/2.14.x/fastdds/transport/shared_memory/shared_memory.html)。

## 部署验收（2026-09-20）

项目维护的 DDS 源码和测试中已删除所有 UDP/TCP transport 创建代码及 LAN 开关，仅保留 SHM。
CDR/JSON 序列化继续服务于共享内存控制消息；浏览器 HTTP/WebSocket、诊断 HTTP 和设备 SDK 通信仍是各自接口。

- 备份与 SHA256 清单：`hal/build/deployment-backup-20260920-170957/manifest.json`。
- HAL PID 29604；后端实际服务 PID 18440，加载正式路径的共享内存 DLL。
- `/api/health` 返回 `ok=true`、HAL connected、LTDMC/Omega 正常，两个 deployment 的 `restartRequired=false`。
- 核验时 HAL 与后端没有 UDP 监听端口；通过正式 DLL 发出的 DDS 急停已收到匹配请求号的成功应答。
- 运行遥测确认 `estop_active=true`，12 轴 `enabled=false`、`moving=false`；力安全仍锁存且启动自检未完成。

本次仅验收部署、通信、遥测和停止状态，未执行轴运动、自动解除急停或力自检/标定。
