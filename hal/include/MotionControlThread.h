#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <thread>

#include "HalTypes.h"
#include "LTDMCDriver.h"

namespace appstation::hal {

class MotionControlThread {
 public:
  explicit MotionControlThread(LTDMCDriver& driver);
  ~MotionControlThread();

  void start(int hz);
  void stop();
  void setSoftLimits(std::array<AxisLimit, 12> limits);
  MotionState latestState() const;

 private:
  void loop();

  LTDMCDriver& driver_;
  std::atomic<bool> running_{false};
  std::chrono::microseconds period_{1000};
  std::thread worker_;
  std::array<AxisLimit, 12> limits_{};
  MotionState latest_{};
};

}  // namespace appstation::hal
