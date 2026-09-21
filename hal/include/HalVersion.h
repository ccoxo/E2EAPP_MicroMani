#pragma once

#include <array>

namespace appstation::hal {

inline constexpr const char* kHalVersion = "hal-real/0.2";
// 双侧 Tare 稳定性/残差验证、校准状态和 ACK 门控由 ForceControlRuntime 实现。
// ControlLeaseState 与 DDS 紧急通道实现后端续租及执行侧失效保护。
inline constexpr std::array<const char*, 4> kHalCapabilities{{"force_calibration_state_v1", "control_lease_v1", "replay_absolute_target_v1", "record_participation_v1"}};

}  // namespace appstation::hal
