#include "ForceComplianceController.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace appstation::hal {
namespace {

double applyDeadband(double value, double deadband) {
  const double magnitude = std::max(0.0, std::abs(value) - std::max(0.0, deadband));
  return std::copysign(magnitude, value);
}

}  // namespace

void ForceComplianceController::configure(
    const ForceComplianceConfig& config) {
  config_ = config;
  reset();
}

ForceComplianceResult ForceComplianceController::correction(
    int side,
    const std::array<double, 6>& force,
    bool sampleFresh,
    bool safetyLatched,
    std::uint64_t targetMonotonicMs) {
  if (side < 0 || side >= 2) {
    throw std::out_of_range("force compliance side must be 0 or 1");
  }
  ForceComplianceResult result;
  const auto& sideConfig = config_.sides[side];
  if (!config_.enabled || !sideConfig.mappingConfirmed || !sampleFresh || safetyLatched) {
    lastTargetValid_[side] = false;
    return result;
  }

  result.active = true;
  if (lastTargetValid_[side] && targetMonotonicMs >= lastTargetMs_[side]) {
    result.dtSec = std::min(
        static_cast<double>(targetMonotonicMs - lastTargetMs_[side]) / 1000.0,
        0.020);
  }
  lastTargetMs_[side] = targetMonotonicMs;
  lastTargetValid_[side] = true;

  const std::array<double, 2> input{{force[0], force[2]}};
  result.mappedN = {{
      sideConfig.matrix[0] * input[0] + sideConfig.matrix[1] * input[1],
      sideConfig.matrix[2] * input[0] + sideConfig.matrix[3] * input[1],
  }};

  for (std::size_t axis = 0; axis < 2; ++axis) {
    result.deadbandedN[axis] =
        applyDeadband(result.mappedN[axis], sideConfig.deadbandN[axis]);
    result.requestedUm[axis] =
        sideConfig.gainUmPerNs[axis] * result.deadbandedN[axis] * result.dtSec;

    const double stepLimit = std::max(0.0, sideConfig.maxStepUm[axis]);
    double correction = std::clamp(result.requestedUm[axis], -stepLimit, stepLimit);
    if (correction != result.requestedUm[axis]) {
      result.clipReason[axis] = "max_step";
    }

    const double offsetLimit = std::max(0.0, sideConfig.maxOffsetUm[axis]);
    const double minimum = -offsetLimit - cumulativeOffsetUm_[side][axis];
    const double maximum = offsetLimit - cumulativeOffsetUm_[side][axis];
    const double offsetBounded = std::clamp(correction, minimum, maximum);
    if (offsetBounded != correction) {
      result.clipReason[axis] = "session_offset";
    }
    result.correctionUm[axis] = offsetBounded;
  }
  return result;
}

void ForceComplianceController::commit(
    int side,
    const std::array<double, 2>& requestedUm,
    const std::array<double, 2>& actualUm) {
  if (side < 0 || side >= 2) {
    throw std::out_of_range("force compliance side must be 0 or 1");
  }
  for (std::size_t axis = 0; axis < 2; ++axis) {
    const double lower = std::min(0.0, requestedUm[axis]);
    const double upper = std::max(0.0, requestedUm[axis]);
    const double applied = std::clamp(actualUm[axis], lower, upper);
    const double limit = std::max(0.0, config_.sides[side].maxOffsetUm[axis]);
    cumulativeOffsetUm_[side][axis] =
        std::clamp(cumulativeOffsetUm_[side][axis] + applied, -limit, limit);
  }
}

void ForceComplianceController::reset() {
  cumulativeOffsetUm_ = {};
  lastTargetMs_ = {};
  lastTargetValid_ = {false, false};
}

void ForceComplianceController::resetSide(int side) {
  if (side < 0 || side >= 2) {
    return;
  }
  cumulativeOffsetUm_[side] = {};
  lastTargetMs_[side] = 0;
  lastTargetValid_[side] = false;
}

std::array<double, 2> ForceComplianceController::cumulativeOffset(
    int side) const {
  if (side < 0 || side >= 2) {
    throw std::out_of_range("force compliance side must be 0 or 1");
  }
  return cumulativeOffsetUm_[side];
}

const ForceComplianceConfig& ForceComplianceController::config() const {
  return config_;
}

}  // namespace appstation::hal
