cmake_minimum_required(VERSION 3.24)
project(AppStationHal LANGUAGES CXX)

option(APPSTATION_ENABLE_VENDOR_SDKS "Compile LTDMC and Omega.7 vendor SDK calls" ON)

add_library(appstation_hal_core
  src/LTDMCDriver.cpp
  src/MotionControlThread.cpp
  src/Omega7Driver.cpp
)
target_include_directories(appstation_hal_core PUBLIC include)
target_compile_features(appstation_hal_core PUBLIC cxx_std_20)
if(MSVC)
  target_compile_options(appstation_hal_core PRIVATE /utf-8)
endif()

if(APPSTATION_ENABLE_VENDOR_SDKS)
  target_compile_definitions(appstation_hal_core PRIVATE APPSTATION_ENABLE_VENDOR_SDKS=1)
endif()

add_executable(HalServer src/HalServer.cpp)
target_link_libraries(HalServer PRIVATE appstation_hal_core)
if(MSVC)
  target_compile_options(HalServer PRIVATE /utf-8)
endif()
if(WIN32)
  target_link_libraries(HalServer PRIVATE ws2_32)
endif()
