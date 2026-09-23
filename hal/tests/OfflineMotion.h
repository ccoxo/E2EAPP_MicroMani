#pragma once
#include "ForceControlRuntime.h"
#include "MotionExecutor.h"

namespace appstation::hal {
// 仅供关闭 vendor SDK 的测试使用；不加载设备、不启动真实运动。
struct MotionExecutorTestAccess {
  static void initialize(LTDMCDriver& motion) { motion.initialized_ = true; }
  static std::unique_lock<std::mutex> holdExecutor(MotionExecutor& executor) {
    return std::unique_lock<std::mutex>(executor.mutex_);
  }
  static std::unique_lock<std::mutex> holdDriver(LTDMCDriver& motion) {
    return std::unique_lock<std::mutex>(motion.mutex_);
  }
  static void markEnabled(LTDMCDriver& motion, Side side) {
    std::scoped_lock lock(motion.mutex_);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto index = stateIndex(side, static_cast<SemanticAxis>(axisIndex));
      motion.enabled_[index] = true;
      motion.commandedEnabled_[index] = true;
    }
  }
  static bool executorBusy(MotionExecutor& executor) {
    std::unique_lock lock(executor.mutex_, std::try_to_lock);
    return !lock.owns_lock();
  }
  static void moving(LTDMCDriver& motion, Side side, bool value) {
    std::scoped_lock lock(motion.mutex_, motion.snapshotMutex_);
    motion.cachedState_.axes[stateIndex(side, SemanticAxis::X)].moving = value;
  }
};

struct ForceControlRuntimeTestAccess {
  static void markCalibrationReady(ForceControlRuntime& runtime) {
    std::scoped_lock lock(runtime.mutex_);
    runtime.calibration_.state = "ready_for_ack";
  }
};
}
