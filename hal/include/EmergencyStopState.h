#pragma once

#include <atomic>
#include <cstdint>

namespace appstation::hal {

// 状态和代际放在同一个原子值中；确认只允许清除它实际观察到的那次急停。
class EmergencyStopState {
 public:
  std::uint64_t epoch() const { return state_.load(std::memory_order_acquire); }
  bool active() const { return (epoch() & 1U) != 0; }
  bool permits(std::uint64_t commandEpoch) const {
    return (commandEpoch & 1U) == 0 && epoch() == commandEpoch;
  }
  void trip() {
    auto observed = epoch();
    while (!state_.compare_exchange_weak(
        observed, (observed + 2U) | 1U, std::memory_order_acq_rel)) {}
  }
  bool acknowledge(std::uint64_t observed) {
    const auto cleared = (observed & 1U) != 0 ? observed + 1U : observed;
    return state_.compare_exchange_strong(observed, cleared, std::memory_order_acq_rel);
  }

 private:
  std::atomic_uint64_t state_{0};
};

}  // namespace appstation::hal
