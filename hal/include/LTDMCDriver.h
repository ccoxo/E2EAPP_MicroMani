#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>

#include "HalTypes.h"

namespace appstation::hal {

class LTDMCDriver {
 public:
  bool initialize();
  HalHealth health(double uptimeS) const;
  MotionState readState();
  std::string axisDiagnosticsJson();
  void emergencyStop();
  void ensureMotionReturnAllowed() const;
  std::string enableSide(Side side, bool enabled = true);
  std::string enableSide(Side side, bool enabled, const std::array<bool, 6>& enabledAxes);
  void homeSide(Side side);
  void homeSide(Side side, const std::array<bool, 6>& enabledAxes);
  void homeAll(const std::array<double, 12>& workOriginPulse);
  void homeAll(
      const std::array<double, 12>& workOriginPulse,
      const std::array<std::array<bool, 6>, 2>& enabledAxes);
  void homeOriginSide(Side side, const std::array<double, 6>& workOriginPulse);
  void homeOriginSide(
      Side side,
      const std::array<double, 6>& workOriginPulse,
      const std::array<bool, 6>& enabledAxes);
  void moveAllUi(const std::array<double, 12>& targetUi, const std::array<AxisLimit, 12>& limits);
  // maxVelocityUiPerSec/startVelocityUiPerSec are in the semantic UI unit
  // (um/s for translation, deg/s for rotation). Pass <= 0 to fall back to the
  // built-in defaults.
  void moveRelativeUi(
      Side side,
      SemanticAxis axis,
      double deltaUi,
      double maxVelocityUiPerSec,
      double startVelocityUiPerSec = 0.0,
      double accTimeSec = 0.0,
      double decTimeSec = 0.0);
  TeleopTargetUpdateResult updateTeleopTargetUi(
      Side side,
      const std::array<double, 6>& deltaUi,
      double translationStepPulse,
      double rotationStepPulse,
      double translationPulseDeadband,
      double rotationPulseDeadband,
      const std::array<bool, 6>& enabledAxes,
      bool syncZeroDeltaTarget,
      const std::array<AxisLimit, 6>& limits,
      double translationVelocityUiPerSec,
      double rotationVelocityUiPerSec,
      double translationStartVelocityUiPerSec = 0.0,
      double rotationStartVelocityUiPerSec = 0.0,
      double accTimeSec = 0.0,
      double decTimeSec = 0.0);
  void stopTeleopSide(Side side);

 private:
  void ensureInitialized() const;
  void configureStageAxes(Side side);
  void checkLimits(const std::array<double, 12>& targetUi, const std::array<AxisLimit, 12>& limits) const;
  bool axisMotionEnabled(Side side, SemanticAxis axis) const;
  MotionState cachedStateSnapshot() const;
  HalHealth cachedHealth(double uptimeS) const;
  void publishStateSnapshotLocked();
  void publishStateSnapshotLocked(const MotionState& state);
  void stopAllAxesBestEffort() noexcept;
  void disableAllAxesBestEffort() noexcept;
  void clearEstopIfUnchanged(std::uint64_t sequenceAtStart);

  mutable std::mutex mutex_;
  mutable std::mutex snapshotMutex_;
  bool initialized_{false};
  std::atomic_bool estopActive_{false};
  std::atomic_uint64_t estopSequence_{0};
  std::string lastError_;
  std::array<double, 12> pulse_{};
  std::array<bool, 12> enabled_{};
  std::array<bool, 12> commandedEnabled_{};
  std::array<double, 12> teleopTargetPulse_{};
  std::array<bool, 12> teleopTargetActive_{};
  MotionState cachedState_{};
  bool cachedInitialized_{false};
  std::string cachedLastError_;
};

}  // namespace appstation::hal
