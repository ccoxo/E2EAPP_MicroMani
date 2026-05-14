#pragma once

#include <array>
#include <mutex>
#include <string>

#include "HalTypes.h"

namespace appstation::hal {

class LTDMCDriver {
 public:
  bool initialize();
  HalHealth health(double uptimeS) const;
  MotionState readState();
  void emergencyStop();
  std::string enableSide(Side side, bool enabled = true);
  void homeSide(Side side);
  void homeAll(const std::array<double, 12>& workOriginPulse);
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
  void updateTeleopTargetUi(
      Side side,
      const std::array<double, 6>& deltaUi,
      double translationStepPulse,
      double rotationStepPulse,
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

  mutable std::mutex mutex_;
  bool initialized_{false};
  bool estopActive_{false};
  std::string lastError_;
  std::array<double, 12> pulse_{};
  std::array<bool, 12> enabled_{};
  std::array<double, 12> teleopTargetPulse_{};
  std::array<bool, 12> teleopTargetActive_{};
};

}  // namespace appstation::hal
