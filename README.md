# MicroMani / AppStation 代码阅读入口

本项目由 React 操作界面、Python FastAPI 后端和 Windows C++ HAL 组成，支持微操作硬件控制、Omega.7 遥操作、力监测与 LeRobot 数据录制。

第一次阅读请打开 [中文代码阅读指南](docs/CODE_READING_GUIDE.md)，再用 [完整源码索引](docs/SOURCE_INDEX.md) 定位文件职责与关键符号。每个自有源码、测试和脚本文件的开头也有“阅读导航”。

| 阅读顺序 | 内容 | 入口 |
| --- | --- | --- |
| 1 | 界面启动和页面路由 | [main.tsx](frontend/src/main.tsx)、[App.tsx](frontend/src/App.tsx) |
| 2 | 前端类型、数据连接和操作 | [types.ts](frontend/src/types.ts)、[telemetry.ts](frontend/src/stores/telemetry.ts)、[API](frontend/src/api/index.ts) |
| 3 | 后端入口、服务装配与配置 | [app.py](backend/app.py)、[app_factory.py](backend/app_factory.py)、[config.py](backend/core/config.py) |
| 4 | 业务控制与数据录制 | [command_service.py](backend/services/command_service.py)、[dataset_recorder.py](backend/services/dataset_recorder.py) |
| 5 | Python 与 HAL 的 DDS 通信 | [dds_client.py](backend/hal_client/dds_client.py)、[DDS 说明](docs/dds-bridge.md) |
| 6 | 原生遥操作与力安全 | [HalServer.cpp](hal/src/HalServer.cpp)、[NativeTeleopController.cpp](hal/src/NativeTeleopController.cpp)、[ForceSafetyLatch.cpp](hal/src/ForceSafetyLatch.cpp) |
| 7 | 回归测试 | [后端测试](backend/tests)、[前端测试示例](frontend/src/App.test.tsx)、[力核心测试](hal/tests/ForceCoreTests.cpp) |
| 8 | 构建和运行脚本 | [Start-App.cmd](Start-App.cmd)、[start-stack.ps1](scripts/start-stack.ps1)、[HAL 说明](hal/README.md) |

阅读不需要启动硬件。启动、停止、模型部署和设备探测脚本的作用见阅读指南；脚本中的本机 SDK、模型及 Conda 路径需要与实际安装对应。

本次同步与检查结果见 [本地更新记录](docs/LOCAL_UPDATE_2026-09-09.md)。

`xie` 分支的源码范围、验证、部署限制与回退说明见 [2026-09-18 上传记录](docs/xie-upload-2026-09-18.md)。
