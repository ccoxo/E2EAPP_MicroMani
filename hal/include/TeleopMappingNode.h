/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明TeleopMappingNode 的接口与状态结构；订阅主手状态，调用原生映射算法，再发布 HardwareTarget。
 * 先看：TeleopMappingNode → Impl。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
