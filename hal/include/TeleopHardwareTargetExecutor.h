/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明TeleopHardwareTargetExecutor 的接口与状态结构；接收硬件目标，叠加柔顺修正后交给运动驱动，并回写实际修正量。
 * 先看：TeleopHardwareTargetExecutor。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

#include "ForceControlRuntime.h"
#include "LTDMCDriver.h"
#include "MotionExecutor.h"
#include "TeleopDdsTypes.h"

namespace appstation::hal {

// 给 DDS 硬件目标叠加柔顺修正，再交给共享 MotionExecutor 仲裁和执行。
// 不订阅 DDS、不直接写运动驱动；保留柔顺实际应用量的回写职责。
class TeleopHardwareTargetExecutor {
 public:
  TeleopHardwareTargetExecutor(
      LTDMCDriver& motion,
      MotionExecutor& executor,
      ForceControlRuntime& forceRuntime,
      std::function<void(const char*)> failureCallback = {});

  void apply(const TeleopHardwareTarget& target);
  void reportControlFailure(const char* message) noexcept;

 private:
  LTDMCDriver& motion_;
  MotionExecutor& executor_;
  ForceControlRuntime& forceRuntime_;
  std::function<void(const char*)> failureCallback_;
};

}  // namespace appstation::hal
