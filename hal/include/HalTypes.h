#pragma once

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace appstation::hal {

enum class Side { Left, Right };
enum class SemanticAxis { X, Y, Z, Roll, Pitch, Yaw };

struct AxisLimit {
  double min{};
  double max{};
};

struct MotionProfile {
  double startSpeed{};
  double maxSpeed{};
  double accTimeSec{};
  double decTimeSec{};
};

struct AxisState {
  double pulse{};
  double uiPosition{};
  bool moving{};
  bool enabled{};
};

struct MotionState {
  std::array<AxisState, 12> axes{};
  bool estopActive{};
  std::int64_t readTimestampMs{};
};

struct HalHealth {
  bool ltdmcOk{};
  bool omega7Ok{};
  std::string version{"hal-skeleton/0.1"};
  double uptimeS{};
};

constexpr std::array<int, 6> kLeftPhysicalAxis{0, 1, 3, 5, 4, 2};
constexpr std::array<int, 6> kRightPhysicalAxis{2, 0, 5, 8, 1, 7};
constexpr std::array<double, 6> kLeftPulsePerUnit{-5000.0, -10000.0, -10000.0, 1666.666667, 2500.0, 3333.333333};
constexpr std::array<double, 6> kRightPulsePerUnit{
    -5000.0,
    10000.0,
    -10000.0,
    1666.666667,
    -2500.0,
    -3333.333333};

inline int stateIndex(Side side, SemanticAxis axis) {
  return (side == Side::Left ? 0 : 6) + static_cast<int>(axis);
}

inline int physicalAxis(Side side, SemanticAxis axis) {
  const auto idx = static_cast<int>(axis);
  return side == Side::Left ? kLeftPhysicalAxis[idx] : kRightPhysicalAxis[idx];
}

inline double pulsePerUnit(Side side, SemanticAxis axis) {
  const auto idx = static_cast<int>(axis);
  return side == Side::Left ? kLeftPulsePerUnit[idx] : kRightPulsePerUnit[idx];
}

inline bool isRotation(SemanticAxis axis) {
  return axis == SemanticAxis::Roll || axis == SemanticAxis::Pitch || axis == SemanticAxis::Yaw;
}

inline double pulseToUi(double pulse, Side side, SemanticAxis axis) {
  const auto value = pulse / pulsePerUnit(side, axis) * 1000.0;
  return isRotation(axis) ? value / 1000.0 : value;
}

inline double uiToPulse(double value, Side side, SemanticAxis axis) {
  const auto physical = isRotation(axis) ? value : value / 1000.0;
  return physical * pulsePerUnit(side, axis);
}

}  // namespace appstation::hal
