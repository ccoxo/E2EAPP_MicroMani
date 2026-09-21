#pragma once

#include <atomic>
#include <mutex>
#include <optional>

#include "LTDMCDriver.h"
#include "TeleopDdsTypes.h"

namespace appstation::hal {

enum class MotionOwner { Idle, Manual, Homing, External, NativeTeleop };

// 每台运动设备共享一个执行器。锁覆盖准入与驱动调用，急停仍走驱动独立路径。
// 原生遥操作占用双侧；外部连续目标按侧占用，必须显式停止后才能换源。
class MotionExecutor {
 public:
  explicit MotionExecutor(LTDMCDriver& motion) : motion_(motion) {}

  void beginNative(std::uint64_t sequenceFloor, std::uint64_t epoch);
  void revokeNative() noexcept { nativeActive_.store(false); }
  void endNative();
  void stopNativeSide(Side side, std::uint64_t sequenceFloor);
  std::optional<TeleopTargetUpdateResult> applyNative(
      const TeleopHardwareTarget& target, const std::array<double, 6>& deltas);
  TeleopTargetUpdateResult applyExternal(const TeleopHardwareTarget& target, std::uint64_t epoch, bool absoluteTarget = false);

  std::string enableSide(Side side, bool enabled, const std::array<bool, 6>& axes,
      std::uint64_t epoch);
  void stopSide(Side side);
  void homeSide(Side side, const std::array<bool, 6>& axes, std::uint64_t epoch);
  void homeAll(const std::array<double, 12>& origin,
      const std::array<std::array<bool, 6>, 2>& axes, std::uint64_t epoch);
  void homeOriginSide(Side side, const std::array<double, 6>& origin,
      const std::array<bool, 6>& axes, std::uint64_t epoch, bool hardwareReferenceReturn = false);
  void moveRelativeUi(Side side, SemanticAxis axis, double delta, double velocity,
      double startVelocity, double acc, double dec, std::uint64_t epoch);
  std::array<MotionOwner, 2> owners();

 private:
  friend struct MotionExecutorTestAccess;
  void refresh(std::uint64_t epoch);
  void requireAvailable(Side side, MotionOwner requested);
  TeleopTargetUpdateResult apply(const TeleopHardwareTarget& target,
      const std::array<double, 6>& deltas, std::uint64_t epoch, bool absoluteTarget = false);
  static std::size_t index(Side side) { return side == Side::Left ? 0 : 1; }
  static Side targetSide(const TeleopHardwareTarget& target);

  LTDMCDriver& motion_;
  std::mutex mutex_;
  std::array<MotionOwner, 2> owners_{};
  std::array<std::uint64_t, 2> lastSequence_{};
  std::uint64_t epoch_{0};
  std::atomic_bool nativeActive_{false};
};

}  // namespace appstation::hal
