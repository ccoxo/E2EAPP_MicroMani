cmake_minimum_required(VERSION 3.24)
project(AppStationHal LANGUAGES CXX)

# Vendor SDK 默认打开，面向现场工作站；没有硬件 SDK 的机器可在配置时显式关闭。
option(APPSTATION_ENABLE_VENDOR_SDKS "Compile LTDMC and Omega.7 vendor SDK calls" ON)

# 核心库只包含硬件访问、单位换算、teleop 映射和夹爪控制，不包含 HTTP 传输层。
add_library(appstation_hal_core
  src/JodellGripperDriver.cpp
  src/LTDMCDriver.cpp
  src/MotionControlThread.cpp
  src/NativeTeleopController.cpp
  src/Omega7Driver.cpp
)
target_include_directories(appstation_hal_core PUBLIC include)
target_compile_features(appstation_hal_core PUBLIC cxx_std_20)
if(MSVC)
  # 源码和诊断字符串包含中文，MSVC 必须显式指定 UTF-8。
  target_compile_options(appstation_hal_core PRIVATE /utf-8)
endif()

if(APPSTATION_ENABLE_VENDOR_SDKS)
  # 真实硬件调用都用这个宏包住，便于在无 SDK 环境下保留可编译骨架。
  target_compile_definitions(appstation_hal_core PRIVATE APPSTATION_ENABLE_VENDOR_SDKS=1)
endif()

# HalServer 是本地 HTTP 边界，给 Python backend 提供 /health、motion、teleop 等接口。
add_executable(HalServer src/HalServer.cpp)
target_link_libraries(HalServer PRIVATE appstation_hal_core)
if(MSVC)
  target_compile_options(HalServer PRIVATE /utf-8)
endif()
if(WIN32)
  # HalServer.cpp 直接使用 Winsock API。
  target_link_libraries(HalServer PRIVATE ws2_32)
endif()

# 夹爪 worker 是隔离进程，用于绕开 vendor DLL 串口状态互相污染的问题。
add_executable(JodellGripperWorker src/JodellGripperWorker.cpp)
target_link_libraries(JodellGripperWorker PRIVATE appstation_hal_core)
if(MSVC)
  target_compile_options(JodellGripperWorker PRIVATE /utf-8)
endif()
