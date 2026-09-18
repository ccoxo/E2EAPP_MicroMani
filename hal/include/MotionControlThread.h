/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明MotionControlThread 的接口与状态结构；管理运动状态后台轮询线程的启动、循环和停止。
 * 先看：MotionControlThread。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

#include <atomic>
#include <chrono>
#include <thread>
#include <functional>
#include <mutex>
#include <string>

#include "LTDMCDriver.h"
#include "WorkerExceptionBoundary.h"

namespace appstation::hal {

// MotionControlThread 是轻量级轮询线程：周期性读取 LTDMCDriver 状态并缓存最新快照。
// 当前不做轨迹规划，真正的运动命令仍由 LTDMCDriver 和 teleop 控制器发起。
class MotionControlThread {
 public:
  explicit MotionControlThread(
      LTDMCDriver& driver,
      std::function<void(const char*)> failureCallback = {},
      std::function<void()> poll = {},
      WorkerLauncher launcher = {});
  ~MotionControlThread();

  // 以 hz 指定轮询频率启动后台线程；重复 start 是幂等操作。
  void start(int hz);
  // 停止线程并 join，析构时也会调用。
  void stop();
  bool running() const;
  std::string lastError() const;
 private:
  void loop();
  void reportFailure(const char* message) noexcept;

  // driver_ 生命周期由 HalServer 持有，线程只保存引用。
  LTDMCDriver& driver_;
  std::function<void(const char*)> failureCallback_;
  std::function<void()> poll_;
  WorkerLauncher launcher_;
  std::mutex lifecycleMutex_;
  mutable std::mutex diagnosticMutex_;
  std::string lastError_;
  std::atomic<bool> running_{false};
  std::chrono::microseconds period_{1000};
  std::thread worker_;
};

}  // namespace appstation::hal
