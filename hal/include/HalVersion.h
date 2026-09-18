#pragma once

#include <array>

namespace appstation::hal {

inline constexpr const char* kHalVersion = "hal-real/0.2";
// 只声明实际实现的能力；当前未接入双阶段校准状态机，不能声明 force_calibration_state_v1。
inline constexpr std::array<const char*, 0> kHalCapabilities{};

}  // namespace appstation::hal
