/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明HalCommandDispatcher 的接口与状态结构；统一解析并分派 HAL 命令，调用运动、主手、遥操作和力运行时。
 * 先看：HalCommandDispatcher。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

#include <chrono>
#include <atomic>
#include <string>

#include "ForceControlRuntime.h"
#include "LTDMCDriver.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"

namespace appstation::hal {

// 统一分发 HAL 命令名，供 HTTP 和 DDS 两种传输层复用同一套语义。
// 这里只做参数解析与驱动调用编排，不拥有硬件对象生命周期。
class HalCommandDispatcher {
 public:
  HalCommandDispatcher(
      LTDMCDriver& motion,
      Omega7Driver& omega,
      NativeTeleopController& nativeTeleop,
      ForceControlRuntime& forceRuntime,
      const std::chrono::steady_clock::time_point& started);

  // bodyText 是上层传入的 JSON 字符串；返回值保持 JSON 字符串，便于传输层原样转发。
  std::string handle(const std::string& name, const std::string& bodyText,
      std::optional<std::uint64_t> expectedEpoch = std::nullopt);
  std::string handleEmergencyStop();

 private:
  void requireForceMutationSafe(const char* operation);

  LTDMCDriver& motion_;
  Omega7Driver& omega_;
  NativeTeleopController& nativeTeleop_;
  ForceControlRuntime& forceRuntime_;
  const std::chrono::steady_clock::time_point& started_;
  std::atomic_uint32_t emergencyStopsInProgress_{0};
};

}  // namespace appstation::hal
