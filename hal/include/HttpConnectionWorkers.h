#pragma once

#include <array>
#include <atomic>
#include <cstdio>
#include <exception>
#include <functional>
#include <thread>
#include <utility>

namespace appstation::hal {

// accept 线程独占提交；固定数量的可回收 worker 避免慢连接无限创建线程。
template <std::size_t Capacity>
class HttpConnectionWorkers {
 public:
  bool tryStart(std::function<void()> connection) {
    for (auto& slot : slots_) {
      if (slot.busy.exchange(true)) continue;
      try {
        slot.worker = std::jthread([&slot, connection = std::move(connection)]() {
          try { connection(); }
          catch (const std::exception& error) { std::fprintf(stderr, "HAL HTTP connection failed: %s\n", error.what()); }
          catch (...) { std::fputs("HAL HTTP connection failed: unknown C++ exception\n", stderr); }
          slot.busy.store(false);
        });
      } catch (...) {
        slot.busy.store(false);
        throw;
      }
      return true;
    }
    return false;
  }

  std::size_t activeCount() const {
    std::size_t count = 0;
    for (const auto& slot : slots_) if (slot.busy.load()) ++count;
    return count;
  }

 private:
  struct Slot {
    // 析构时先 join worker，再销毁它仍可能访问的 busy 状态。
    std::atomic_bool busy{false};
    std::jthread worker;
  };
  std::array<Slot, Capacity> slots_;
};

}  // namespace appstation::hal
