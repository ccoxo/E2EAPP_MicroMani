#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>

#include "HalTypes.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "Omega7Driver.h"

namespace appstation::hal {

struct NativeTeleopConfig {
  std::string controlMode{"incremental_position"};
  std::string mappingMode{"direct"};
  int loopHz{100};
  bool swapTeleopChannels{true};
  bool requireClutch{false};
  bool leftGravityCompensation{true};
  bool rightGravityCompensation{true};

  std::array<double, 2> translationScale{{1.0, 1.0}};
  std::array<double, 2> rotationScale{{1.0, 1.0}};
  std::array<std::array<double, 6>, 2> axisOutputScale{{
      {0.40, 0.25, 0.25, 0.40, 0.20, 0.20},
      {0.40, 0.25, 0.25, 0.40, 0.20, 0.20},
  }};
  std::array<std::array<double, 6>, 2> impulseCoeff{{
      {-5000000.0, 10000000.0, -10000000.0, 1667.0, -2500.0, -333.3333},
      {-5000000.0, -10000000.0, -10000000.0, 1667.0, 2500.0, 3333.333},
  }};
  std::array<std::array<bool, 6>, 2> enabledAxes{{
      {true, true, true, true, true, true},
      {true, true, true, true, true, true},
  }};
  std::array<std::array<AxisLimit, 6>, 2> softLimits{};
  bool rotationWorkLimitEnabled{false};
  std::array<std::array<AxisLimit, 6>, 2> rotationWorkLimits{};
  std::array<std::array<double, 6>, 2> workOriginPulse{};
  std::array<bool, 2> workOriginValid{{false, false}};

  double translationStepLimitPulse{4000.0};
  double rotationStepLimitPulse{1250.0};
  double translationPulseDeadband{2.0};
  double rotationPulseDeadband{2.0};
  double translationStartVelocityUmS{600.0};
  double translationMaxVelocityUmS{8000.0};
  double rotationStartVelocityDegS{1.0};
  double rotationMaxVelocityDegS{12.0};
  double accTimeSec{0.05};
  double decTimeSec{0.05};

  double nativeTranslationDeadzoneM{0.002};
  double nativeTranslationFullScaleM{0.040};
  double nativeRotationDeadzoneDeg{2.0};
  double nativeRotationFullScaleDeg{30.0};
  double nativeVelocitySmoothingMs{40.0};
  // 以下参数控制主从 teleop 的自适应 Kalman 滤波与意图权重 w2。
  // 是否启用主从 teleop 的自适应 Kalman 滤波；关闭时直接使用当前采样数据。
  bool kalmanFilterEnabled{false};
  // 遗忘因子 beta，用于 Q_k / R_k 的自适应更新中新观测统计量的权重。
  double kalmanBeta{0.05};
  // 协方差、过程噪声和测量噪声的数值下限，避免矩阵退化到 0 或负数。
  double kalmanMinVariance{1e-12};
  // 协方差、过程噪声和测量噪声的数值上限，避免噪声估计无限放大。
  double kalmanMaxVariance{100.0};
  // 单步采样时间 dt 的下限，用于过滤过小循环间隔造成的速度估计异常。
  double kalmanDtMinSec{0.001};
  // 单步采样时间 dt 的上限，用于过滤线程卡顿造成的一次性大跨度预测。
  double kalmanDtMaxSec{0.05};
  // 平移轴位置状态 p 的初始协方差 P00。
  double kalmanTranslationPositionVariance{1e-8};
  // 平移轴速度状态 v 的初始协方差 P11。
  double kalmanTranslationVelocityVariance{1e-4};
  // 平移轴观测噪声 R 的初始方差。
  double kalmanTranslationMeasurementVariance{1e-8};
  // 平移轴过程噪声 Q00 的初始值，对应位置分量。
  double kalmanTranslationProcessPositionVariance{1e-10};
  // 平移轴过程噪声 Q11 的初始值，对应速度分量。
  double kalmanTranslationProcessVelocityVariance{1e-8};
  // 旋转轴角度状态 p 的初始协方差 P00。
  double kalmanRotationPositionVariance{0.25};
  // 旋转轴角速度状态 v 的初始协方差 P11。
  double kalmanRotationVelocityVariance{4.0};
  // 旋转轴观测噪声 R 的初始方差。
  double kalmanRotationMeasurementVariance{0.04};
  // 旋转轴过程噪声 Q00 的初始值，对应角度分量。
  double kalmanRotationProcessPositionVariance{1e-4};
  // 旋转轴过程噪声 Q11 的初始值，对应角速度分量。
  double kalmanRotationProcessVelocityVariance{1e-3};
  // 平移轴意图速度阈值 v_th；超过该阈值时 w2 直接取 1。
  double kalmanTranslationIntentVelocityThreshold{0.0005};
  // 旋转轴意图速度阈值 v_th；超过该阈值时 w2 直接取 1。
  double kalmanRotationIntentVelocityThreshold{0.5};
  double translationDeadzoneM{0.00002};
  double rotationDeadzoneDeg{0.03};
  double incrementalTranslationMinEffectiveDeltaM{0.000025};
  double incrementalTranslationReverseDeadzoneM{0.00005};
  bool continuousIncrementMode{true};
  double translationInputEpsilonM{0.00002};
  double rotationInputEpsilonDeg{0.03};
  double translationMinActivePulse{3.0};
  double rotationMinActivePulse{3.0};
  int continuousMicroConfirmTicks{0};

  JodellGripperConfig gripper{};
  bool gripperTeleopEnabled{true};
  std::array<double, 2> gripperGapMinMm{{0.0, 0.0}};
  std::array<double, 2> gripperGapMaxMm{{25.0, 25.0}};
  std::array<bool, 2> gripperGapInvert{{false, false}};
  std::array<std::string, 2> gripperSourceHand{{"PhysicalRight", "PhysicalLeft"}};
  int gripperDeadbandCounts{1};
  double gripperMinCommandIntervalMs{20.0};
  bool gripperButtonFallback{true};
};

struct NativeTeleopAction {
  std::int64_t ts{};
  double monotonicS{};
  Side side{Side::Left};
  Side sourceSide{Side::Left};
  int axisIndex{};
  double delta{};
  std::array<double, 6> deltas{};
  std::array<double, 12> deltaVector{};
  std::array<double, 6> requestedDeltaPulse{};
  std::array<double, 6> appliedDeltaPulse{};
  std::array<double, 6> targetPulse{};
  std::array<double, 6> currentPulse{};
  std::array<double, 6> launchDeltaPulse{};
  std::array<double, 6> updateReturn{};
  std::array<bool, 6> movingBefore{};
  std::array<bool, 6> moveStarted{};
  std::array<bool, 6> clipped{};
};

class NativeTeleopController {
 public:
  NativeTeleopController(LTDMCDriver& motion, Omega7Driver& omega, JodellGripperDriver& gripper);
  ~NativeTeleopController();

  void configure(const NativeTeleopConfig& config);
  void start(bool leftConnected, bool rightConnected);
  void stop();
  std::string statusJson() const;
  bool running() const;

 private:
  struct PendingGripperCommand {
    bool pending{};
    int targetIndex{};
    Side side{Side::Left};
    double targetMm{};
    int speed{};
    int torque{};
  };

  // 单个语义轴的 Kalman 状态，严格对应 x_k=[p_k, v_k]^T、P_k、Q_k、R_k。
  struct KalmanAxisState {
    // 该轴是否已用第一帧观测完成初始化。
    bool initialized{};
    // 状态向量中的位置/角度分量 p_k。
    double position{};
    // 状态向量中的速度/角速度分量 v_k，即意图估计量的来源。
    double velocity{};
    // 状态协方差矩阵 P 的 (0,0) 元素，表示位置分量不确定度。
    double p00{};
    // 状态协方差矩阵 P 的 (0,1) 元素，表示位置-速度相关性。
    double p01{};
    // 状态协方差矩阵 P 的 (1,0) 元素，表示速度-位置相关性。
    double p10{};
    // 状态协方差矩阵 P 的 (1,1) 元素，表示速度分量不确定度。
    double p11{};
    // 过程噪声协方差 Q 的 (0,0) 元素，表示位置过程噪声。
    double q00{};
    // 过程噪声协方差 Q 的 (0,1) 元素，表示位置-速度过程噪声相关项。
    double q01{};
    // 过程噪声协方差 Q 的 (1,0) 元素，表示速度-位置过程噪声相关项。
    double q10{};
    // 过程噪声协方差 Q 的 (1,1) 元素，表示速度过程噪声。
    double q11{};
    // 测量噪声协方差 R；当前每轴是一维观测，因此 R 是标量。
    double r{};
  };

  void loop();
  void startGripperWorker();
  void stopGripperWorker();
  void gripperLoop();
  void sampleGripperPosition(Side side);
  void tick(double dtSec);
  void tickSideBestEffort(int sourceIndex, const Omega7State& hand, double dtSec);
  void tickSide(int sourceIndex, const Omega7State& hand, double dtSec);
  std::array<double, 6> kalmanPoseForSide(
      int sourceIndex,
      const std::array<double, 6>& rawSemanticPose,
      double dtSec);
  double updateKalmanAxis(int axisIndex, double measurement, double dtSec, KalmanAxisState& state);
  double kalmanIntentWeight(int axisIndex, const KalmanAxisState& state) const;
  std::array<double, 6> applyKalmanIntentWeights(int sourceIndex, std::array<double, 6> deltas) const;
  void resetKalmanSideUnlocked(int sourceIndex);
  void tickGrippers(const std::array<Omega7State, 2>& hands);
  void enqueueGripperCommand(int targetIndex, Side side, double targetMm, int speed, int torque);
  void setBlockerUnlocked(int sourceIndex, const std::string& state, const std::string& message);
  void recordActionUnlocked(Side sourceSide, Side targetSide, const TeleopTargetUpdateResult& result);
  void recordZeroStopActionUnlocked(Side sourceSide, Side targetSide);
  void syncIncrementalZeroDeltaUnlocked(
      Side sourceSide,
      Side targetSide,
      int sourceIndex,
      int targetIndex,
      const std::array<double, 6>& semanticPose,
      const std::string& message);
  std::array<double, 6> velocityDeltasUi(
      int sourceIndex,
      Side targetSide,
      const std::array<double, 6>& pose,
      double dtSec);
  std::array<double, 6> incrementalDeltasUi(int sourceIndex, Side targetSide, const std::array<double, 6>& pose);
  long applyContinuousPulseGate(int sourceIndex, int axisIndex, long requestedPulse, double requestedPulseFloat);
  double mappedDirection(int sourceIndex, Side targetSide, int axisIndex) const;
  std::array<AxisLimit, 6> effectiveSoftLimits(Side targetSide, int targetIndex) const;
  int gripperSourceIndex(int targetIndex) const;
  Side sideFromIndex(int index) const;
  int sideIndex(Side side) const;

  LTDMCDriver& motion_;
  Omega7Driver& omega_;
  JodellGripperDriver& gripper_;
  mutable std::mutex mutex_;
  NativeTeleopConfig config_{};
  std::atomic<bool> running_{false};
  std::thread worker_;
  std::array<bool, 2> logicalConnected_{{false, false}};
  std::array<bool, 2> targetActive_{{false, false}};
  std::array<std::array<double, 6>, 2> referencePose_{};
  std::array<bool, 2> referenceValid_{{false, false}};
  std::array<std::array<double, 6>, 2> velocityUiPerSec_{};
  std::array<std::array<double, 6>, 2> incrementalCarry_{};
  std::array<std::array<int, 6>, 2> incrementalDirection_{};
  std::array<bool, 2> incrementalInputActive_{{false, false}};
  std::array<std::array<double, 6>, 2> continuousPulseCarry_{};
  std::array<std::array<int, 6>, 2> continuousDirection_{};
  std::array<std::array<int, 6>, 2> continuousStreak_{};
  // 两只主手、每只 6 个语义轴各自独立维护一套 Kalman 状态。
  std::array<std::array<KalmanAxisState, 6>, 2> kalmanStates_{};
  // 两只主手、每只 6 个语义轴最近一次根据 v_hat 得到的意图权重 w2。
  std::array<std::array<double, 6>, 2> lastIntentWeight_{};
  std::array<std::array<double, 6>, 2> lastSemanticPose_{};
  std::array<std::array<double, 6>, 2> lastRawDelta_{};
  std::array<std::array<double, 6>, 2> lastFilteredDelta_{};
  std::array<std::array<double, 6>, 2> lastRequestedPulse_{};
  std::array<std::array<double, 6>, 2> lastEmittedPulse_{};
  std::array<std::array<double, 6>, 2> lastOutputDeltaUi_{};
  std::array<Side, 2> lastDiagnosticTargetSide_{{Side::Right, Side::Left}};
  std::array<std::string, 2> blockerState_{{"idle", "idle"}};
  std::array<std::string, 2> blockerMessage_{};
  std::string lastError_;
  NativeTeleopAction lastAction_{};
  bool hasLastAction_{false};
  std::deque<NativeTeleopAction> actionHistory_;
  std::array<int, 2> gripperLastRaw_{{-1, -1}};
  std::array<std::chrono::steady_clock::time_point, 2> gripperLastCommandAt_{};
  std::array<double, 2> gripperTargetsMm_{{0.0, 0.0}};
  std::array<double, 2> gripperPositionsMm_{{-1.0, -1.0}};
  std::array<bool, 2> gripperLastCommandOk_{{false, false}};
  std::array<std::string, 2> gripperLastMessage_{};
  std::array<std::int64_t, 2> gripperLastCommandTs_{{0, 0}};
  std::mutex gripperMutex_;
  std::condition_variable gripperCv_;
  std::thread gripperWorker_;
  std::atomic<bool> gripperWorkerRunning_{false};
  std::array<PendingGripperCommand, 2> pendingGripperCommands_{};
  int nextGripperSampleIndex_{0};
};

}  // namespace appstation::hal
