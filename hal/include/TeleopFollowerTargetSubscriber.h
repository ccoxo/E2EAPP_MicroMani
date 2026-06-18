#pragma once

#include <memory>

#include "TeleopHardwareTargetExecutor.h"

namespace appstation::hal {

// Follower 侧 DDS 订阅器：接收 Mapping 节点发布的硬件目标并交给执行器落到 LTDMC。
class TeleopFollowerTargetSubscriber {
 public:
  explicit TeleopFollowerTargetSubscriber(TeleopHardwareTargetExecutor& executor);
  ~TeleopFollowerTargetSubscriber();

  TeleopFollowerTargetSubscriber(const TeleopFollowerTargetSubscriber&) = delete;
  TeleopFollowerTargetSubscriber& operator=(const TeleopFollowerTargetSubscriber&) = delete;

  bool enabled() const;
  void start();
  void stop();

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
