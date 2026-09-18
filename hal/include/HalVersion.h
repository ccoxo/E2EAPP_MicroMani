#pragma once

#include <array>

namespace appstation::hal {

inline constexpr const char* kHalVersion = "hal-real/0.2";
// 双侧 Tare 稳定性/残差验证、校准状态和 ACK 门控由 ForceControlRuntime 实现。
inline constexpr std::array<const char*, 1> kHalCapabilities{{"force_calibration_state_v1"}};

}  // namespace appstation::hal
