#pragma once

#include <atomic>
#include <chrono>
#include <thread>

#include "LTDMCDriver.h"

namespace appstation::hal {

// MotionControlThread 是轻量级轮询线程：周期性读取 LTDMCDriver 状态并缓存最新快照。
// 当前不做轨迹规划，真正的运动命令仍由 LTDMCDriver 和 teleop 控制器发起。
class MotionControlThread {
 public:
  explicit MotionControlThread(LTDMCDriver& driver);
  ~MotionControlThread();

  // 以 hz 指定轮询频率启动后台线程；重复 start 是幂等操作。
  void start(int hz);
  // 停止线程并 join，析构时也会调用。
  void stop();
 private:
  void loop();

  // driver_ 生命周期由 HalServer 持有，线程只保存引用。
  LTDMCDriver& driver_;
  std::atomic<bool> running_{false};
  std::chrono::microseconds period_{1000};
  std::thread worker_;
};

}  // namespace appstation::hal
