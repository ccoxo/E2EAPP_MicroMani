#pragma once

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace appstation::hal {

// HAL 统一使用 Side 表示左右两套从端机构，数组下标通常按 Left=0、Right=1 排列。
enum class Side { Left, Right };
// SemanticAxis 是 UI、后端和 HAL 之间共享的六自由度语义轴顺序。
// 驱动层会再通过 physicalAxis() 映射到控制卡上的真实轴号。
enum class SemanticAxis { X, Y, Z, Roll, Pitch, Yaw };

// 单轴软限位。平移轴单位是 UI 侧的 um，旋转轴单位是 degree。
struct AxisLimit {
  double min{};
  double max{};
};

// 运动曲线参数，对应控制卡的起始速度、最大速度和加减速时间。
struct MotionProfile {
  double startSpeed{};
  double maxSpeed{};
  double accTimeSec{};
  double decTimeSec{};
};

// 单个语义轴的实时状态。pulse 是控制卡脉冲，uiPosition 是换算后的 UI 单位。
struct AxisState {
  double pulse{};
  double uiPosition{};
  bool moving{};
  bool enabled{};
};

// 两侧共 12 个语义轴的运动状态快照。
struct MotionState {
  std::array<AxisState, 12> axes{};
  bool estopActive{};
  std::int64_t readTimestampMs{};
};

// 遥操作目标更新的诊断结果。数组下标仍按 X/Y/Z/Roll/Pitch/Yaw 排列，
// 既保留请求值，也保留限位、死区、控制卡返回值处理后的实际下发结果。
struct TeleopTargetUpdateResult {
  // requestedDeltaUi 是上层本次要求的 UI 增量；appliedDeltaUi 是死区、限幅和软限位后的实际增量。
  std::array<double, 6> requestedDeltaUi{};
  std::array<double, 6> appliedDeltaUi{};
  // targetUi/targetPulse 表示最终希望控制卡跟随的目标位置，便于回放 teleop 诊断。
  std::array<double, 6> targetUi{};
  std::array<double, 6> requestedDeltaPulse{};
  std::array<double, 6> appliedDeltaPulse{};
  std::array<double, 6> targetPulse{};
  // currentPulse 是本帧读取到的实际位置；launchDeltaPulse 是新运动段启动时的相对脉冲。
  std::array<double, 6> currentPulse{};
  std::array<double, 6> launchDeltaPulse{};
  // updateReturn/stopReason/axisIoStatus 直接保留控制卡诊断值，调用方可以按 vendor 文档解释。
  std::array<double, 6> updateReturn{};
  std::array<double, 6> stopReason{};
  std::array<double, 6> axisIoStatus{};
  // movingBefore/moveStarted/clipped 用于区分“轴已在运动”“本帧启动运动”和“本帧被限位裁剪”。
  std::array<bool, 6> movingBefore{};
  std::array<bool, 6> moveStarted{};
  std::array<bool, 6> clipped{};
};

// HAL 健康状态返回给 /health；ltdmcOk 和 omega7Ok 分别描述从端控制卡和主手状态。
struct HalHealth {
  bool ltdmcOk{};
  bool omega7Ok{};
  std::string version{"hal-skeleton/0.1"};
  double uptimeS{};
};

// 语义轴到控制卡物理轴的映射。左侧控制卡只使用部分轴号，右侧有不同接线顺序。
constexpr std::array<int, 6> kLeftPhysicalAxis{0, 1, 3, 5, 4, 2};
constexpr std::array<int, 6> kRightPhysicalAxis{2, 0, 5, 8, 1, 7};
// 每个 UI 单位对应的脉冲数。平移轴的 UI 单位是 um，换算函数内部会先转成 mm。
constexpr std::array<double, 6> kLeftPulsePerUnit{-5000.0, 5000.0, -10000.0, 1666.666667, -2500.0, -3333.333};
constexpr std::array<double, 6> kRightPulsePerUnit{
    -5000.0,
    -10000.0,
    -5000.0,
    1666.666667,
    2500.0,
    333.3333};

// 将 Side + SemanticAxis 压成 MotionState::axes 的 0-11 下标。
inline int stateIndex(Side side, SemanticAxis axis) {
  return (side == Side::Left ? 0 : 6) + static_cast<int>(axis);
}

// 返回该语义轴在 LTDMC 控制卡上的真实轴号。
inline int physicalAxis(Side side, SemanticAxis axis) {
  const auto idx = static_cast<int>(axis);
  return side == Side::Left ? kLeftPhysicalAxis[idx] : kRightPhysicalAxis[idx];
}

// 返回该语义轴的脉冲换算系数，符号同时表达机械方向约定。
inline double pulsePerUnit(Side side, SemanticAxis axis) {
  const auto idx = static_cast<int>(axis);
  return side == Side::Left ? kLeftPulsePerUnit[idx] : kRightPulsePerUnit[idx];
}

// Roll/Pitch/Yaw 使用角度单位，其他轴使用平移单位。
inline bool isRotation(SemanticAxis axis) {
  return axis == SemanticAxis::Roll || axis == SemanticAxis::Pitch || axis == SemanticAxis::Yaw;
}

// 控制卡脉冲转 UI 单位：平移轴输出 um，旋转轴输出 degree。
inline double pulseToUi(double pulse, Side side, SemanticAxis axis) {
  const auto value = pulse / pulsePerUnit(side, axis) * 1000.0;
  return isRotation(axis) ? value / 1000.0 : value;
}

// UI 单位转控制卡脉冲：平移轴输入 um，旋转轴输入 degree。
inline double uiToPulse(double value, Side side, SemanticAxis axis) {
  const auto physical = isRotation(axis) ? value : value / 1000.0;
  return physical * pulsePerUnit(side, axis);
}

}  // namespace appstation::hal
