#pragma once

#include <cstdio>
#include <functional>
#include <thread>

#include "LTDMCDriver.h"

namespace appstation::hal {

// 独立于 DDS 普通命令和遥测循环；普通命令阻塞也不会阻止租约到期检查。
class ControlLeaseGuardian {
 public:
  ControlLeaseGuardian(LTDMCDriver& motion, std::function<void()> applyStop)
      : motion_(motion), applyStop_(std::move(applyStop)) {
    motion_.requireControlLease();
  }
  ~ControlLeaseGuardian() { stop(); }
  void start() {
    if (worker_.joinable()) return;
    try {
      worker_ = std::jthread([this](std::stop_token stop) {
        try {
          while (!stop.stop_requested()) {
            const auto sequence = motion_.pollControlLease();
            if (sequence != 0) {
              try {
                applyStop_();
                motion_.completeControlLeaseStop(sequence);
              } catch (...) {
                motion_.latchEmergencyStop();
                std::fputs("control lease fail-safe stop failed; acknowledgement remains blocked\n", stderr);
                // 失败保持待处理状态；有限频率重试，不以成功状态掩盖停机失败。
                for (int i = 0; i < 20 && !stop.stop_requested(); ++i)
                  std::this_thread::sleep_for(std::chrono::milliseconds(10));
              }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
          }
        } catch (...) {
          motion_.failControlLease();
          std::fputs("control lease guardian failed; motion remains latched\n", stderr);
          try { applyStop_(); } catch (...) {}
        }
      });
    } catch (...) {
      motion_.failControlLease();
      try { applyStop_(); } catch (...) {}
      throw;
    }
  }
  void stop() {
    if (worker_.joinable()) {
      // SDK 停机或 join 可能阻塞，必须在等待之前永久撤销续租/确认资格。
      motion_.failControlLease();
      worker_.request_stop();
      try { applyStop_(); }
      catch (...) { std::fputs("control lease guardian shutdown stop failed\n", stderr); }
      worker_.join();
    }
  }
 private:
  LTDMCDriver& motion_;
  std::function<void()> applyStop_;
  std::jthread worker_;
};

}  // namespace appstation::hal
