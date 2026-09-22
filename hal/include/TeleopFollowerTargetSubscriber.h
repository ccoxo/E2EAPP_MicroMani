/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明TeleopFollowerTargetSubscriber 的接口与状态结构；订阅 DDS 硬件目标并交给最终执行器，隔离传输与设备访问。
 * 先看：TeleopFollowerTargetSubscriber → Impl。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
