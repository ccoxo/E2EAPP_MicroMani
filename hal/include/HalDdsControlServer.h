#pragma once

#include <chrono>
#include <memory>

#include "ForceControlRuntime.h"
#include "HalCommandDispatcher.h"
#include "LTDMCDriver.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"

namespace appstation::hal {

// HAL 的 Fast-DDS 控制边界：定期发布健康/运动/遥操作状态，并接收命令与急停请求。
// 具体 DDS 类型放在 Impl 内，避免公共头文件把 Fast-DDS 依赖扩散到核心代码。
class HalDdsControlServer {
 public:
  HalDdsControlServer(
      HalCommandDispatcher& commandDispatcher,
      LTDMCDriver& motion,
      Omega7Driver& omega,
      NativeTeleopController& nativeTeleop,
      ForceControlRuntime& forceRuntime,
      const std::chrono::steady_clock::time_point& started);
  ~HalDdsControlServer();

  HalDdsControlServer(const HalDdsControlServer&) = delete;
  HalDdsControlServer& operator=(const HalDdsControlServer&) = delete;

  bool enabled() const;
  void start();
  void stop();

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
