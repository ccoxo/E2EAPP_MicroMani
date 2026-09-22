#include "MotionExecutor.h"

#include <algorithm>
#include <stdexcept>

namespace appstation::hal {

Side MotionExecutor::targetSide(const TeleopHardwareTarget& target) {
  if (target.side != 0 && target.side != 1) throw std::invalid_argument("invalid motion target side");
  return target.side == 0 ? Side::Left : Side::Right;
}

void MotionExecutor::refresh(std::uint64_t epoch) {
  if (!motion_.commandEpochAllowed(epoch)) throw std::runtime_error("motion ownership cancelled by emergency stop");
  if (epoch_ != epoch) {
    owners_ = {};
    nativeActive_.store(false);
    epoch_ = epoch;
  }
}

void MotionExecutor::requireAvailable(Side side, MotionOwner requested) {
  const auto owner = owners_[index(side)];
  if (owner == requested && requested == MotionOwner::External) return;
  if (owner == MotionOwner::NativeTeleop || owner == MotionOwner::External) {
    throw std::runtime_error("motion control conflict; stop the current controller before switching source");
  }
  // 手动/机械回零的驱动调用返回不表示运动完成，交接必须读取真实停止状态。
  motion_.requireSideStopped(side);
}

void MotionExecutor::beginNative(std::uint64_t sequenceFloor, std::uint64_t epoch) {
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) throw std::runtime_error("motion executor busy; request was not queued");
  refresh(epoch);
  if (nativeActive_.load()) return;
  // 被撤销的会话先完成停止；失败时保持占用，禁止其他来源接管。
  for (auto side : {Side::Left, Side::Right}) {
    if (owners_[index(side)] == MotionOwner::NativeTeleop) {
      motion_.stopTeleopSide(side);
      owners_[index(side)] = MotionOwner::Idle;
    }
    requireAvailable(side, MotionOwner::NativeTeleop);
  }
  owners_.fill(MotionOwner::NativeTeleop);
  lastSequence_.fill(sequenceFloor);
  nativeActive_.store(true);
}

void MotionExecutor::endNative() {
  revokeNative();
  std::scoped_lock lock(mutex_);
  for (auto side : {Side::Left, Side::Right}) {
    if (owners_[index(side)] != MotionOwner::NativeTeleop) continue;
    motion_.stopTeleopSide(side);
    owners_[index(side)] = MotionOwner::Idle;
  }
}

void MotionExecutor::stopNativeSide(Side side, std::uint64_t sequenceFloor) {
  std::scoped_lock lock(mutex_);
  if (owners_[index(side)] != MotionOwner::NativeTeleop) return;
  lastSequence_[index(side)] = (std::max)(lastSequence_[index(side)], sequenceFloor);
  motion_.stopTeleopSide(side);
}

std::optional<TeleopTargetUpdateResult> MotionExecutor::applyNative(
    const TeleopHardwareTarget& target, const std::array<double, 6>& deltas) {
  const auto side = targetSide(target);
  std::scoped_lock lock(mutex_);
  // 迟到的 DDS 帧只丢弃，不能使新控制源故障或重新获取控制权。
  if (!nativeActive_.load() || !motion_.commandEpochAllowed(epoch_)
      || owners_[index(side)] != MotionOwner::NativeTeleop
      || target.sequence <= lastSequence_[index(side)]
      || target.stampUnixMs <= motion_.lastEmergencyStopUnixMs()) return std::nullopt;
  lastSequence_[index(side)] = target.sequence;
  return apply(target, deltas, epoch_);
}

TeleopTargetUpdateResult MotionExecutor::applyExternal(const TeleopHardwareTarget& target, std::uint64_t epoch, bool absoluteTarget) {
  const auto side = targetSide(target);
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) throw std::runtime_error("motion executor busy; request was not queued");
  refresh(epoch);
  requireAvailable(side, MotionOwner::External);
  owners_[index(side)] = MotionOwner::External;
  return apply(target, target.deltas, epoch, absoluteTarget);
}

TeleopTargetUpdateResult MotionExecutor::apply(const TeleopHardwareTarget& target,
    const std::array<double, 6>& deltas, std::uint64_t epoch, bool absoluteTarget) {
  std::array<AxisLimit, 6> limits{};
  for (std::size_t i = 0; i < limits.size(); ++i) limits[i] = {target.softLimitMin[i], target.softLimitMax[i]};
  return motion_.updateTeleopTargetUi(targetSide(target), deltas,
      target.translationStepLimitPulse, target.rotationStepLimitPulse,
      target.translationPulseDeadband, target.rotationPulseDeadband,
      target.enabledAxes, target.syncZeroDeltaTarget, limits,
      target.translationVelocityUiPerSec, target.rotationVelocityUiPerSec,
      target.translationStartVelocityUiPerSec, target.rotationStartVelocityUiPerSec,
      target.accTimeSec, target.decTimeSec, epoch, absoluteTarget);
}

std::string MotionExecutor::enableSide(Side side, bool enabled, const std::array<bool, 6>& axes,
    std::uint64_t epoch) {
  std::scoped_lock lock(mutex_);
  if (enabled) {
    refresh(epoch);
    requireAvailable(side, MotionOwner::Manual);
  }
  const auto result = motion_.enableSide(side, enabled, axes, epoch);
  if (!enabled && owners_[index(side)] != MotionOwner::NativeTeleop) owners_[index(side)] = MotionOwner::Idle;
  return result;
}

void MotionExecutor::stopSide(Side side) {
  std::scoped_lock lock(mutex_);
  motion_.stopTeleopSide(side);
  // 原生模式须由 native.stop 退出，单侧停止不能暗中交出整个会话。
  if (owners_[index(side)] != MotionOwner::NativeTeleop) owners_[index(side)] = MotionOwner::Idle;
}

std::array<bool, 6> MotionExecutor::homeSide(Side side, const std::array<bool, 6>& axes, std::uint64_t epoch) {
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) throw std::runtime_error("motion executor busy; request was not queued");
  refresh(epoch);
  requireAvailable(side, MotionOwner::Homing);
  owners_[index(side)] = MotionOwner::Homing;
  return motion_.homeSide(side, axes, epoch);
}

void MotionExecutor::homeAll(const std::array<double, 12>& origin,
    const std::array<std::array<bool, 6>, 2>& axes, std::uint64_t epoch) {
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) throw std::runtime_error("motion executor busy; request was not queued");
  refresh(epoch);
  requireAvailable(Side::Left, MotionOwner::Homing);
  requireAvailable(Side::Right, MotionOwner::Homing);
  owners_.fill(MotionOwner::Homing);
  motion_.homeAll(origin, axes, epoch);
}

void MotionExecutor::homeOriginSide(Side side, const std::array<double, 6>& origin,
    const std::array<bool, 6>& axes, std::uint64_t epoch, bool hardwareReferenceReturn) {
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) throw std::runtime_error("motion executor busy; request was not queued");
  refresh(epoch);
  requireAvailable(side, MotionOwner::Homing);
  owners_[index(side)] = MotionOwner::Homing;
  motion_.homeOriginSide(side, origin, axes, epoch, hardwareReferenceReturn);
}

void MotionExecutor::moveRelativeUi(Side side, SemanticAxis axis, double delta, double velocity,
    double startVelocity, double acc, double dec, std::uint64_t epoch) {
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) throw std::runtime_error("motion executor busy; request was not queued");
  refresh(epoch);
  requireAvailable(side, MotionOwner::Manual);
  owners_[index(side)] = MotionOwner::Manual;
  motion_.moveRelativeUi(side, axis, delta, velocity, startVelocity, acc, dec, epoch);
}

std::array<MotionOwner, 2> MotionExecutor::owners() {
  std::scoped_lock lock(mutex_);
  if (!motion_.commandEpochAllowed(epoch_)) return {};
  return owners_;
}

}  // namespace appstation::hal
