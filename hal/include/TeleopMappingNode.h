#pragma once

#include <memory>

#include "NativeTeleopController.h"
#include "TeleopDdsTypes.h"

namespace appstation::hal {

// DDS 映射节点：订阅 Leader 状态，调用 NativeTeleopController 计算硬件目标，再发布给 Follower。
class TeleopMappingNode {
 public:
  explicit TeleopMappingNode(NativeTeleopController& nativeTeleop);
  ~TeleopMappingNode();

  TeleopMappingNode(const TeleopMappingNode&) = delete;
  TeleopMappingNode& operator=(const TeleopMappingNode&) = delete;

  bool enabled() const;
  void start();
  void stop();
  void publishHardwareTarget(const TeleopHardwareTarget& target);

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
