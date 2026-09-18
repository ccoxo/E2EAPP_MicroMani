/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明HalDdsControlServer 的接口与状态结构；提供后端 DDS 控制面：发布状态、接收命令并按请求编号返回应答。
 * 先看：HalDdsControlServer → Impl。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
