/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：接收硬件目标，叠加柔顺修正后交给运动驱动，并回写实际修正量。
 * 先看：TeleopHardwareTargetExecutor::apply。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "TeleopHardwareTargetExecutor.h"

#include <array>

namespace appstation::hal {

TeleopHardwareTargetExecutor::TeleopHardwareTargetExecutor(
    LTDMCDriver& motion,
    MotionExecutor& executor,
    ForceControlRuntime& forceRuntime,
    std::function<void(const char*)> failureCallback)
    : motion_(motion),
      executor_(executor),
      forceRuntime_(forceRuntime), failureCallback_(std::move(failureCallback)) {}

void TeleopHardwareTargetExecutor::reportControlFailure(const char* message) noexcept {
  motion_.failControlLease();
  motion_.latchEmergencyStop();
  try { if (failureCallback_) failureCallback_(message); }
  catch (...) { std::fputs("follower failure: additional stop action failed\n", stderr); }
  try { motion_.emergencyStop(); }
  catch (...) { std::fputs("follower failure: motion emergency stop failed\n", stderr); }
  try { forceRuntime_.recordExternalEmergencyStop("follower_worker_failed", forceMonotonicMilliseconds()); }
  catch (...) { std::fputs("follower failure: force latch update failed\n", stderr); }
  std::fprintf(stderr, "HAL follower stopped: %s\n", message);
}

void TeleopHardwareTargetExecutor::apply(const TeleopHardwareTarget& target) {
  if (motion_.estopActive()
      || target.stampUnixMs <= motion_.lastEmergencyStopUnixMs()) {
    return;
  }
  auto deltas = target.deltas;
  const int sideIndex = target.side == 0 ? 0 : 1;
  const auto compliance = forceRuntime_.complianceCorrection(
      sideIndex,
      target.stampMonotonicMs);
  // 当前柔顺只叠加在 X/Z 平移通道；其余分量沿用映射端的目标。
  deltas[0] += compliance.correctionUm[0];
  deltas[2] += compliance.correctionUm[1];

  // Follower 端只做最终落地，不改变 Mapping 端算好的步长、死区、速度和软限位。
  const auto applied = executor_.applyNative(target, deltas);
  if (!applied) return;
  const auto& result = *applied;
  // 按驱动实际应用的位移回写柔顺累计量，避免软限位裁剪后继续累计未执行的修正。
  forceRuntime_.commitCompliance(
      sideIndex,
      compliance.correctionUm,
      {{
          result.appliedDeltaUi[0] - target.deltas[0],
          result.appliedDeltaUi[2] - target.deltas[2],
      }});
}

}  // namespace appstation::hal
