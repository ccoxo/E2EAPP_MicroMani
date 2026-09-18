#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>

#include "EmergencyStopState.h"

namespace appstation::hal {

// 只操作软件状态；续租与到期不能依赖设备 SDK 或运动锁。
class ControlLeaseState {
 public:
  static constexpr std::int64_t kTimeoutMs = 2500;
  static std::int64_t nowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
  }

  void require(EmergencyStopState& stop) {
    std::scoped_lock lock(mutex_);
    if (required_.exchange(true)) return;
    tripLocked(stop);
  }

  void renew(EmergencyStopState& stop, const std::string& session,
      std::uint64_t sequence, std::int64_t now) {
    if (session.empty() || session.size() > 64 || sequence == 0)
      throw std::runtime_error("invalid control lease identity");
    std::scoped_lock lock(mutex_);
    if (!required_.load() || failed_.load()) throw std::runtime_error("control lease guardian is not available");
    const bool sameSession = session == session_;
    if (!sameSession && !session_.empty() && now < deadline_.load())
      throw std::runtime_error("control lease already has a live owner");
    if (sameSession && sequence <= sequence_)
      throw std::runtime_error("control lease replay rejected");
    // 到期后先锁存，再建立新期限；迟到心跳不能跨过这次急停。
    pollLocked(stop, now);
    if (!sameSession && !session_.empty()) tripLocked(stop);
    session_ = session;
    sequence_ = sequence;
    expiryReported_ = false;
    deadline_.store(now + kTimeoutMs, std::memory_order_release);
  }

  bool fresh(std::int64_t now = nowMs()) const {
    return !failed_.load(std::memory_order_acquire) && (!required_.load(std::memory_order_acquire)
        || now < deadline_.load(std::memory_order_acquire));
  }

  void fail(EmergencyStopState& stop) noexcept {
    failed_.store(true, std::memory_order_release);
    stop.trip();
  }

  std::uint64_t poll(EmergencyStopState& stop, std::int64_t now = nowMs()) {
    std::scoped_lock lock(mutex_);
    pollLocked(stop, now);
    const auto pending = stopSequence_.load();
    return pending > completedStopSequence_.load() ? pending : 0;
  }

  void completeStop(std::uint64_t sequence) {
    auto completed = completedStopSequence_.load();
    while (completed < sequence
        && !completedStopSequence_.compare_exchange_weak(completed, sequence)) {}
  }

  void acknowledge(EmergencyStopState& stop, std::uint64_t epoch) {
    std::scoped_lock lock(mutex_);
    pollLocked(stop, nowMs());
    if (!fresh() || stopSequence_.load() > completedStopSequence_.load())
      throw std::runtime_error("control lease unavailable or fail-safe stop is still pending");
    if (!stop.acknowledge(epoch))
      throw std::runtime_error("a newer emergency stop requires a new acknowledgement");
    // 确认期间时限到达也必须重新锁存，不能靠续租自动恢复。
    pollLocked(stop, nowMs());
    if (!fresh()) throw std::runtime_error("control lease expired during acknowledgement");
  }

 private:
  void tripLocked(EmergencyStopState& stop) {
    stop.trip();
    stopSequence_.fetch_add(1);
  }
  void pollLocked(EmergencyStopState& stop, std::int64_t now) {
    if (required_.load() && deadline_.load() > 0 && now >= deadline_.load() && !expiryReported_) {
      expiryReported_ = true;
      tripLocked(stop);
    }
  }
  mutable std::mutex mutex_;
  std::atomic_bool required_{false};
  std::atomic_bool failed_{false};
  std::atomic_int64_t deadline_{0};
  std::atomic_uint64_t stopSequence_{0};
  std::atomic_uint64_t completedStopSequence_{0};
  std::string session_;
  std::uint64_t sequence_{0};
  bool expiryReported_{false};
};

}  // namespace appstation::hal
