#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>

#include "HalTypes.h"

namespace appstation::hal {

// LTDMCDriver 封装雷赛 LTDMC 控制卡访问。外部只使用语义轴和 UI 单位，
// 具体控制卡号、物理轴号、脉冲方向和 vendor SDK 调用都被限制在本类内部。
class LTDMCDriver {
 public:
  // 加载 LTDMC.dll、绑定必要导出并初始化控制卡；失败时 lastError_ 会保存原因。
  bool initialize();
  HalHealth health(double uptimeS) const;
  // 读取 12 个语义轴的运动快照。读锁竞争时返回最近缓存，避免周期线程阻塞。
  MotionState readState();
  // 输出每个轴的 IO、伺服、限位和停止原因诊断 JSON，供硬件联调使用。
  std::string axisDiagnosticsJson();
  // 立即急停所有轴并尽力关闭伺服，随后需要由回零/使能流程清除急停状态。
  void emergencyStop();
  // 回工作原点前的安全检查，急停未清除时直接拒绝运动。
  void ensureMotionReturnAllowed() const;
  // 按侧打开/关闭伺服；enabledAxes 允许只作用于部分语义轴。
  std::string enableSide(Side side, bool enabled = true);
  std::string enableSide(Side side, bool enabled, const std::array<bool, 6>& enabledAxes);
  // 使用控制卡原点回零模式回单侧机械原点。
  void homeSide(Side side);
  void homeSide(Side side, const std::array<bool, 6>& enabledAxes);
  // 两侧回工作原点。workOriginPulse 是 12 轴目标脉冲，顺序与 MotionState::axes 一致。
  void homeAll(const std::array<double, 12>& workOriginPulse);
  void homeAll(
      const std::array<double, 12>& workOriginPulse,
      const std::array<std::array<bool, 6>, 2>& enabledAxes);
  // 单侧回工作原点。入参只包含该侧 6 个语义轴的目标脉冲。
  void homeOriginSide(Side side, const std::array<double, 6>& workOriginPulse);
  void homeOriginSide(
      Side side,
      const std::array<double, 6>& workOriginPulse,
      const std::array<bool, 6>& enabledAxes);
  // 直接设置 12 轴 UI 目标位置；当前主要用于 skeleton/仿真路径和限位验证。
  void moveAllUi(const std::array<double, 12>& targetUi, const std::array<AxisLimit, 12>& limits);
  // maxVelocityUiPerSec/startVelocityUiPerSec 使用语义 UI 单位：
  // 平移轴是 um/s，旋转轴是 deg/s；传入 <=0 时使用内置保守默认值。
  void moveRelativeUi(
      Side side,
      SemanticAxis axis,
      double deltaUi,
      double maxVelocityUiPerSec,
      double startVelocityUiPerSec = 0.0,
      double accTimeSec = 0.0,
      double decTimeSec = 0.0);
  // 原生 teleop 的高频目标更新入口。函数会做死区、单步限幅、软限位裁剪、
  // 目标窗口刷新/重发，并返回完整诊断数据给上层记录。
  TeleopTargetUpdateResult updateTeleopTargetUi(
      Side side,
      const std::array<double, 6>& deltaUi,
      double translationStepPulse,
      double rotationStepPulse,
      double translationPulseDeadband,
      double rotationPulseDeadband,
      const std::array<bool, 6>& enabledAxes,
      bool syncZeroDeltaTarget,
      const std::array<AxisLimit, 6>& limits,
      double translationVelocityUiPerSec,
      double rotationVelocityUiPerSec,
      double translationStartVelocityUiPerSec = 0.0,
      double rotationStartVelocityUiPerSec = 0.0,
      double accTimeSec = 0.0,
      double decTimeSec = 0.0);
  // 停止某一侧 teleop 相关运动，并清空该侧目标缓存。
  void stopTeleopSide(Side side);

 private:
  void ensureInitialized() const;
  // 配置某侧控制卡轴的脉冲模式、限位模式等基础参数。
  void configureStageAxes(Side side);
  void checkLimits(const std::array<double, 12>& targetUi, const std::array<AxisLimit, 12>& limits) const;
  // 根据 commandedEnabled_ 与 enabledAxes 判断某个语义轴是否允许运动。
  bool axisMotionEnabled(Side side, SemanticAxis axis) const;
  // snapshotMutex_ 保护的缓存读写，用于高频读取失败时退回上一帧。
  MotionState cachedStateSnapshot() const;
  HalHealth cachedHealth(double uptimeS) const;
  void publishStateSnapshotLocked();
  void publishStateSnapshotLocked(const MotionState& state);
  // best-effort 方法用于急停路径，不能抛异常，也不能依赖完整初始化状态。
  void stopAllAxesBestEffort() noexcept;
  void disableAllAxesBestEffort() noexcept;
  void clearEstopIfUnchanged(std::uint64_t sequenceAtStart);

  // mutex_ 保护 vendor SDK 调用和内部状态；snapshotMutex_ 只保护对外快照缓存。
  mutable std::mutex mutex_;
  mutable std::mutex snapshotMutex_;
  bool initialized_{false};
  // estopSequence_ 用来区分不同急停事件，避免旧操作在结束时误清新急停。
  std::atomic_bool estopActive_{false};
  std::atomic_uint64_t estopSequence_{0};
  std::string lastError_;
  // pulse_/enabled_ 是 12 轴内部状态，索引由 stateIndex(side, axis) 计算。
  std::array<double, 12> pulse_{};
  std::array<bool, 12> enabled_{};
  // commandedEnabled_ 记录软件侧最近一次期望伺服状态，弥补部分轴无可读反馈的问题。
  std::array<bool, 12> commandedEnabled_{};
  // teleopTargetPulse_ 保存高频遥操作连续刷新时的当前目标，避免每帧只基于实际位置累加。
  std::array<double, 12> teleopTargetPulse_{};
  // true 表示该轴已经建立连续 teleop 目标，下一帧应基于目标而不是实际位置续推。
  std::array<bool, 12> teleopTargetActive_{};
  // cachedState_ 是对外快照缓存，读硬件失败或抢锁失败时用于保持服务响应。
  MotionState cachedState_{};
  bool cachedInitialized_{false};
  std::string cachedLastError_;
};

}  // namespace appstation::hal
