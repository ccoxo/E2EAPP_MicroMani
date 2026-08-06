#include "TeleopHardwareTargetExecutor.h"

#include <array>

namespace appstation::hal {

TeleopHardwareTargetExecutor::TeleopHardwareTargetExecutor(
    LTDMCDriver& motion,
    ForceControlRuntime& forceRuntime)
    : motion_(motion),
      forceRuntime_(forceRuntime) {}

void TeleopHardwareTargetExecutor::apply(const TeleopHardwareTarget& target) {
  if (motion_.estopActive()) {
    return;
  }
  // DDS 目标携带的是 min/max 数组，LTDMCDriver 需要 AxisLimit 结构数组。
  std::array<AxisLimit, 6> limits{};
  for (size_t i = 0; i < limits.size(); ++i) {
    limits[i] = AxisLimit{target.softLimitMin[i], target.softLimitMax[i]};
  }

  auto deltas = target.deltas;
  const int sideIndex = target.side == 0 ? 0 : 1;
  const auto compliance = forceRuntime_.complianceCorrection(
      sideIndex,
      target.stampMonotonicMs);
  deltas[0] += compliance.correctionUm[0];
  deltas[2] += compliance.correctionUm[1];

  // Follower 端只做最终落地，不改变 Mapping 端算好的步长、死区、速度和软限位。
  const auto result = motion_.updateTeleopTargetUi(
      target.side == 0 ? Side::Left : Side::Right,
      deltas,
      target.translationStepLimitPulse,
      target.rotationStepLimitPulse,
      target.translationPulseDeadband,
      target.rotationPulseDeadband,
      target.enabledAxes,
      target.syncZeroDeltaTarget,
      limits,
      target.translationVelocityUiPerSec,
      target.rotationVelocityUiPerSec,
      target.translationStartVelocityUiPerSec,
      target.rotationStartVelocityUiPerSec,
      target.accTimeSec,
      target.decTimeSec);
  forceRuntime_.commitCompliance(
      sideIndex,
      compliance.correctionUm,
      {{
          result.appliedDeltaUi[0] - target.deltas[0],
          result.appliedDeltaUi[2] - target.deltas[2],
      }});
}

}  // namespace appstation::hal
