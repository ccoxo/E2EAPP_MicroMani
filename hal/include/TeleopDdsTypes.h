#pragma once

#include <array>
#include <cstdint>

namespace appstation::hal {

// 进程内使用的遥操作硬件目标镜像，字段顺序和含义需要与 DDS IDL 保持一致。
// Mapping 节点负责生成它，Follower 节点负责把它应用到运动控制器。
struct TeleopHardwareTarget {
  std::uint64_t sequence{0};
  std::uint64_t stampUnixMs{0};
  std::uint64_t stampMonotonicMs{0};
  int side{0};
  // 六轴增量按 X/Y/Z/Roll/Pitch/Yaw 排列，单位保持为底层运动控制使用的 pulse。
  std::array<double, 6> deltas{};
  double translationStepLimitPulse{0.0};
  double rotationStepLimitPulse{0.0};
  double translationPulseDeadband{0.0};
  double rotationPulseDeadband{0.0};
  std::array<bool, 6> enabledAxes{{true, true, true, true, true, true}};
  bool syncZeroDeltaTarget{false};
  // 每轴软限位随目标一起传递，Follower 端不再读取 Mapping 端的配置状态。
  std::array<double, 6> softLimitMin{};
  std::array<double, 6> softLimitMax{};
  double translationVelocityUiPerSec{0.0};
  double rotationVelocityUiPerSec{0.0};
  double translationStartVelocityUiPerSec{0.0};
  double rotationStartVelocityUiPerSec{0.0};
  double accTimeSec{0.0};
  double decTimeSec{0.0};
};

}  // namespace appstation::hal
