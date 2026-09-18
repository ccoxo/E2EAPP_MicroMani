#pragma once

#include <cstdio>
#include <exception>
#include <functional>
#include <thread>
#include <utility>

namespace appstation::hal {

// 创建依赖只在线程启动时使用；默认启动真实线程，离线验证可注入创建失败。
using WorkerLauncher = std::function<std::thread(std::function<void()>)>;

inline std::thread launchWorker(const WorkerLauncher& launcher, std::function<void()> entry) {
  return launcher ? launcher(std::move(entry)) : std::thread(std::move(entry));
}

// 每个控制组件提供自己的锁存/诊断动作；这里仅保证异常不越过线程或 DDS 回调边界。
template <typename Work, typename Failure>
void runWorkerBoundary(Work&& work, Failure&& failure) noexcept {
  const auto fail = [&](const char* message) noexcept {
    try { failure(message); }
    catch (...) { std::fputs("HAL worker failure handler threw an exception\n", stderr); }
  };
  try { work(); }
  catch (const std::exception& error) { fail(error.what()); }
  catch (...) { fail("unknown C++ exception in HAL worker"); }
}

}  // namespace appstation::hal
