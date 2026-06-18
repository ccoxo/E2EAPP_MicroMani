# HAL C++ Direct Fast-DDS Implementation Plan

**Goal:** 去掉 DDS 运行路径里的 Python sidecar，让 `HalServer.exe` 直接作为 eProsima Fast-DDS participant 发布 HAL telemetry、订阅 command request，并发布 command reply。默认 HTTP 模式保持不变。

**Architecture:** `HalServer.exe` 内置 Fast-DDS 边界；Python backend 通过 `ctypes` 绑定 `backend\native\build\appstation_fastdds_transport.dll` 作为 DDS consumer/client；`scripts/start-stack-dds.ps1` 不再启动旧 Python sidecar。DDS 路径不引入 ROS2、`rclpy`、`colcon` 或其它 DDS Python runtime。

**Tech Stack:** MSVC C++20, eProsima Fast-DDS/Fast-CDR from `F:\opt\ros\jazzy`, Python `ctypes`, pytest, PowerShell script tests.

## Success Criteria

- [x] `hal/dds/appstation_hal.idl` 记录第一阶段 JSON envelope 和 command request/reply 类型。
- [x] `HalServer.exe` 在 `APPSTATION_HAL_DDS_ENABLED=1` 时创建 Fast-DDS participant。
- [x] HAL C++ 直接发布 `Health`、`MotionState`、`OmegaState`、`NativeTeleopStatus`。
- [x] HAL C++ 直接订阅 `CommandRequest` 并发布 matching `CommandReply`。
- [x] `scripts/start-stack-dds.ps1` 设置 HAL DDS env，不启动 Python DDS sidecar。
- [x] Python DDS runtime 通过 C++ Fast-DDS binding DLL 工作。
- [x] 默认 HTTP 启动路径不改。
- [x] focused backend contract/client/script tests 通过。
- [x] HAL C++ 构建可以链接 Fast-DDS。

## Implemented Files

- `hal/dds/appstation_hal.idl`
- `hal/include/HalFastDdsBridge.h`
- `hal/src/HalFastDdsBridge.cpp`
- `hal/src/HalServer.cpp`
- `hal/build_hal.cmd`
- `hal/CMakeLists.txt`
- `scripts/start-hal.ps1`
- `scripts/start-stack-dds.ps1`
- `docs/dds-bridge.md`
- `backend/native/appstation_fastdds_transport.cpp`
- `backend/native/build_fastdds_transport.cmd`
- `backend/hal_client/dds_runtime.py`
- `backend/tests/test_hal_source_contracts.py`
- `backend/tests/test_stack_scripts.py`

## DDS Contract

Topics:

- `AppStation.Hal.Health`
- `AppStation.Hal.MotionState`
- `AppStation.Hal.OmegaState`
- `AppStation.Hal.NativeTeleopStatus`
- `AppStation.Hal.CommandRequest`
- `AppStation.Hal.CommandReply`

QoS:

- health: reliable, transient local, keep last 1
- motion/omega/native status: best effort, volatile, keep last 1
- command request/reply: reliable, volatile, keep last 32
- command reply lifespan: 5 seconds

## Verification Commands

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_hal_source_contracts.py::test_hal_dds_idl_matches_backend_dds_types backend\tests\test_hal_source_contracts.py::test_hal_server_owns_direct_dds_runtime_and_command_path backend\tests\test_hal_source_contracts.py::test_hal_build_links_fastdds_for_direct_hal_dds backend\tests\test_stack_scripts.py::test_start_dds_stack_enables_hal_direct_dds_without_python_sidecar backend\tests\test_hal_transport_selection.py backend\tests\test_hal_dds_client.py -q
backend\native\build_fastdds_transport.cmd
hal\build_hal.cmd
```

## Known Follow-Up

当前没有做完整 DDS integration smoke，因为已有 `hal\build\HalServer.exe` 进程占用 exe，构建脚本只能生成 `HalServer.next.exe`。等 HAL 进程可重启后，需要用 DDS 模式启动一次并验证 backend 收到 `source="hal-cpp"` 的 telemetry，以及 `motion.emergency_stop` command reply 能按 `request_id` 匹配。
