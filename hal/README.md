# AppStation HAL

This is the Windows-only hardware access boundary for LTDMC motion cards and Omega.7 master hands.

中文说明：`hal` 是 AppStation 的本地硬件访问层。Python 后端不直接加载 `LTDMC.dll`、
Force Dimension SDK 或 Jodell 夹爪 DLL，而是通过本地 HAL 服务访问真实硬件，
从而把 vendor SDK、Windows API、线程和安全门控限制在一个清晰边界内。

Current status:

- Builds without vendor SDKs as a deterministic skeleton.
- Keeps Python Backend away from `LTDMC.dll` and Force Dimension SDK calls.
- Provides the mapping, limit checks, and driver seams required before enabling real hardware.

当前状态说明：

- 无 vendor SDK 时仍可编译骨架，便于在开发机做接口验证。
- 真实运动、Omega.7 主手读取和 Jodell 夹爪控制都集中在 C++ HAL 内。
- 语义轴、软限位、急停、回零、teleop 目标续推和夹爪保护都在 HAL 边界执行。

Real hardware build path:

1. Install LTDMC and Force Dimension SDKs on the workstation.
2. Add include/library paths in `CMakeLists.txt` or a local CMake preset.
3. Configure with `-DAPPSTATION_ENABLE_VENDOR_SDKS=ON`.
4. Implement the guarded SDK calls in `LTDMCDriver.cpp` and `Omega7Driver.cpp` where marked.
5. Use the built-in Fast-DDS control plane for backend telemetry and commands; HTTP remains only as a local diagnostic surface.

真实硬件构建路径说明：

1. 工作站需要先安装雷赛 LTDMC 与 Force Dimension SDK，并确认运行时 DLL 可被加载。
2. `APPSTATION_ENABLE_VENDOR_SDKS=ON` 时会编译真实 SDK 调用；关闭时只保留可编译骨架。
3. `LTDMCDriver.cpp` 负责控制卡、轴映射、限位和运动命令。
4. `Omega7Driver.cpp` 负责 Omega.7 设备枚举、位姿读取和力输出开关。
5. `NativeTeleopController.cpp` 负责主从映射、Kalman/意图权重、夹爪 teleop 和安全门控。

The Python backend real-HAL path uses `DdsHalClient`; start it with `APPSTATION_HAL_MODE=real` and `APPSTATION_HAL_TRANSPORT=dds`.

Local workstation runtime:

- LTDMC runtime files are vendored under `hal/vendor/leishine`.
- Force Dimension runtime DLLs are vendored under `hal/vendor/force_dimension`.
- `HalServer.exe` should be started through `scripts/start-hal.ps1`; the script verifies `LTDMC.dll`, `dhd64.dll`, `drd64.dll`, and `jodellTool.dll` are present beside the executable and fails fast if `/health.ltdmc_ok` is false.
- Use `scripts/check-hal.ps1` for a non-motion health check before manual axis testing.

本地运行约定：

- `HalServer.exe` 只监听本机回环地址，作为后端到真实硬件的进程边界。
- 启动脚本会检查必要 DLL，避免运行时才在运动命令中暴露缺依赖问题。
- 手动轴测试前先跑健康检查，再逐步执行伺服使能、回原点和小步 jog。
- 急停会尽力停止所有轴并关闭伺服；回工作原点前必须确认急停状态已清除。
