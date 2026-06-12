#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>

namespace appstation::hal {

// 单只 Omega.7 主手的快照。pose 的顺序是 X/Y/Z/Roll/Pitch/Yaw，
// 位置单位来自 Force Dimension SDK 的米，姿态单位是 degree。
struct Omega7State {
  // connected 表示 initialize 已成功打开该设备，lastReadOk 表示最近一次轮询成功。
  bool connected{};
  bool calibrated{};
  // openId 是启动参数指定的 SDK 打开编号，deviceId 是 dhdOpenID 返回的运行期句柄。
  int openId{};
  int deviceId{-1};
  std::string serial;
  std::string systemName;
  // handednessKnown 为 false 时不要信任 leftHanded，前端应显示未知。
  bool leftHanded{};
  bool handednessKnown{};
  // pose 是主手当前位姿；NativeTeleopController 会按 mappingMode 转成从端语义轴。
  std::array<double, 6> pose{};
  // clutchPressed 用于遥操作使能门控，gripperPressed 是夹爪按钮兜底输入。
  bool clutchPressed{};
  bool gripperPressed{};
  // gripperGap 是主手开口宽度，单位为米；gripperGapAvailable 表示该值是否可信。
  double gripperGap{};
  bool gripperGapAvailable{};
  // lastReadOk/lastReadError 描述最近一次轮询，不影响 connected 的设备连接语义。
  bool lastReadOk{};
  std::int64_t readTimestampMs{};
  std::string lastReadError;
};

// Force Dimension Omega.7 主手驱动。该类只负责设备枚举、状态读取和力输出开关，
// 不在这里做主从映射；映射和安全门控由 NativeTeleopController 处理。
class Omega7Driver {
 public:
  // 根据左右 openId 打开设备；swapHands 用于现场接线/摆放与逻辑左右相反的情况。
  bool initialize(int leftOpenId, int rightOpenId, bool swapHands);
  // 惰性确保设备已初始化；未初始化时使用上次 initialize 参数重试。
  bool ensureReady();
  bool ok() const;
  std::string lastError() const;
  // 读取两只主手状态；未连接的一侧会返回 connected=false 的默认快照。
  std::array<Omega7State, 2> readState();
  std::array<bool, 2> forceOutputEnabled() const;
  // 控制 SDK 力输出/重力补偿。关闭力输出时会写零力，避免残留力反馈。
  void setGravityCompensation(bool leftEnabled, bool rightEnabled);
  void zeroForceFeedback(int openId);

 private:
  // 以下 Unlocked 方法要求调用方已经持有 mutex_。
  void applyForceOutputUnlocked(std::size_t index, bool enabled);
  int writeZeroForceUnlocked(const Omega7State& item);

  mutable std::mutex mutex_;
  bool initialized_{false};
  // 保存最近一次初始化参数，供 ensureReady 在断线或懒加载时复用。
  int leftOpenId_{0};
  int rightOpenId_{1};
  bool swapHands_{false};
  std::string lastError_;
  // state_[0] 是逻辑左主手，state_[1] 是逻辑右主手。
  std::array<Omega7State, 2> state_{};
  // 每侧是否保持 Force Dimension 力输出/重力补偿开启；关闭时会尽量写零力。
  std::array<bool, 2> forceOutputEnabled_{{true, true}};
};

}  // namespace appstation::hal
