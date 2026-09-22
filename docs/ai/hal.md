# MicroMani HAL、DDS 与硬件控制任务流程

用于 `hal/`、`backend/hal_client/`、`backend/native/` 及影响设备执行的启动配置。继承根目录项目规则。

## 先定位实际执行链

- C++ 先读对应 `hal/include/*.h`，再读 `hal/src/*.cpp`；从 `HalCommandDispatcher`、`NativeTeleopController` 或 `ForceControlRuntime` 追踪到实际驱动。
- DDS 变更对照 `hal/dds/appstation_hal.idl`、`hal/include/TeleopDdsTypes.h`、`backend/hal_client/dds_types.py` 和 `backend/native/appstation_fastdds_transport.cpp` 的相关定义。
- 检查命令 ID、应答关联、超时、旧数据、重复请求和连接恢复；非幂等运动不能因超时自动重放，先核对现有请求策略。
- 确认真实执行走 DDS follower 还是进程内路径；不要把 Python 服务管理职责误判为原生实时循环。

## 修改时保持的约束

- 单位、轴顺序、坐标方向和操作者/硬件左右转换需要可追溯的转换点；不散布补偿常数来修正表面症状。
- 保留执行侧急停、限位、力安全锁存和恢复条件。异常、断连、陈旧数据与恢复路径同样需要验证。
- 力安全检测与柔顺控制可能使用不同处理阶段的数据；以 `ForceSafetyLatch`、`ForceComplianceController` 和配置为准，不把滤波后响应延迟无意带入停机判断。
- 实时或驱动线程避免新增阻塞 I/O、无限等待、频繁分配和大批日志；跨线程状态遵循现有同步与所有权约定。
- 修改退出或异常处理时检查设备句柄、线程停止、DLL 生命周期及夹爪 worker 的隔离边界。
- 实机控制、启动带设备连接的服务、模型部署和参数写入按本次明确授权执行；离线开发不自动包含这些操作。

## 分层验证与真实限制

先从根目录运行相关离线契约测试，例如：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_hal_protocol.py backend/tests/test_hal_dds_client.py -q
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_hal_source_contracts.py backend/tests/test_units.py -q
```

- 上述源码/协议测试不等同于 C++ 编译或设备验收。
- `hal/build_hal.cmd` 包含 `ForceCoreTests` 编译运行和后续构建步骤。执行前读取整个脚本，确认 MSVC、SDK、Fast-DDS 路径及二进制输出；不要覆盖正在使用的服务二进制。
- `hal/CMakeLists.txt` 提供 `APPSTATION_ENABLE_VENDOR_SDKS` 和 `APPSTATION_ENABLE_DDS` 开关。需要无 SDK 骨架验证时显式关闭相应开关并使用独立构建目录；不要将关闭依赖的编译成功表述成真实 DDS/设备验证。
- 当前 CMake 文件未注册 `ForceCoreTests` 的 CTest 目标；不得声称 `ctest` 已运行它。若构建系统变化，重新检查实际目标。
- 有实机授权时再按操作范围完成连接、状态及必要动作验收；没有条件时准确列出尚未验证的路径，不修改保护逻辑迁就环境。

交付分别报告 Python 契约、C++ 编译/原生测试、DDS 运行及实机操作的实际覆盖，附失败或缺依赖的证据。
