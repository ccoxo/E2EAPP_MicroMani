#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

#include "HalTypes.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "Omega7Driver.h"
#include "TeleopDdsTypes.h"

namespace appstation::hal {

// 原生遥操作配置。所有长度为 2 的数组下标都是逻辑左/右主手或目标侧，
// 所有长度为 6 的数组下标都是 X/Y/Z/Roll/Pitch/Yaw 语义轴顺序。
struct NativeTeleopConfig {
  // controlMode 支持 incremental_position 与 velocity 类模式；mappingMode 控制主手姿态轴如何映射到语义轴。
  std::string controlMode{"incremental_position"};
  std::string mappingMode{"direct"};
  // loopHz 是主循环频率；swapTeleopChannels 用于现场主从左右摆放与逻辑方向相反的接线。
  int loopHz{100};
  bool swapTeleopChannels{true};
  // requireClutch=true 时，主手 clutch 按钮未按下会阻断该侧 motion 输出。
  bool requireClutch{false};
  // Force Dimension 侧的力输出/重力补偿开关，和从端 LTDMC 伺服使能无关。
  bool leftGravityCompensation{true};
  bool rightGravityCompensation{true};
  double leftGravityScale{0.45};
  double rightGravityScale{1.0};

  // 位移/旋转整体比例先按侧应用，axisOutputScale 再按单轴微调。
  std::array<double, 2> translationScale{{1.0, 1.0}};
  std::array<double, 2> rotationScale{{1.0, 1.0}};
  std::array<std::array<double, 6>, 2> axisOutputScale{{
      {0.60, 0.50, 0.375, 0.60, 0.08, 0.10},
      {0.60, 0.50, 0.375, 0.60, 0.08, 0.001},
  }};
  // impulseCoeff 用于 velocity/脉冲模式把主手位姿变化换算成 LTDMC 脉冲增量。
  std::array<std::array<double, 6>, 2> impulseCoeff{{
      {-5000000.0, -5000000.0, -10000000.0, 1667.0, 2500.0, -333.3333},
      {-5000000.0, 10000000.0, -5000000.0, 1667.0, -2500.0, 3333.333},
  }};
  // enabledAxes 是软件侧允许运动的轴掩码，false 的轴会输出 0 并停止 teleop 目标续推。
  std::array<std::array<bool, 6>, 2> enabledAxes{{
      {true, true, true, true, true, true},
      {true, true, true, true, true, false},
  }};
  // softLimits 使用 UI 单位；rotationWorkLimits 仅在 rotationWorkLimitEnabled=true 时覆盖旋转轴限位。
  std::array<std::array<AxisLimit, 6>, 2> softLimits{};
  bool rotationWorkLimitEnabled{false};
  std::array<std::array<AxisLimit, 6>, 2> rotationWorkLimits{};
  // 工作原点和 home reference 都是脉冲单位，用于增量模式中识别回零后的参考坐标。
  std::array<std::array<double, 6>, 2> workOriginPulse{};
  std::array<bool, 2> workOriginValid{{false, false}};
  std::array<std::array<double, 6>, 2> homeReferencePulse{};
  std::array<bool, 2> homeReferenceValid{{false, false}};

  // 单帧脉冲限幅和死区用于保护 100Hz teleop 环路，不让主手抖动直接变成控制卡大步进。
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

  // native* 参数用于 velocity 模式：主手位姿先过死区和满量程归一化，再转成 UI 速度。
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
  // incremental* 参数用于增量位置模式，过滤微小输入和方向反转瞬间的抖动。
  double translationDeadzoneM{0.00002};
  double rotationDeadzoneDeg{0.03};
  double incrementalTranslationMinEffectiveDeltaM{0.000025};
  double incrementalTranslationReverseDeadzoneM{0.00005};
  // continuousIncrementMode=true 时，亚脉冲残差会累积到下一帧，避免小动作永远被取整吞掉。
  bool continuousIncrementMode{true};
  double translationInputEpsilonM{0.00002};
  double rotationInputEpsilonDeg{0.03};
  double translationMinActivePulse{3.0};
  double rotationMinActivePulse{3.0};
  int continuousMicroConfirmTicks{0};

  // 夹爪 teleop 配置。主手开口单位为 mm，最终会映射到 Jodell 目标行程。
  JodellGripperConfig gripper{};
  bool gripperTeleopEnabled{false};
  std::array<double, 2> gripperGapMinMm{{0.0, 0.0}};
  std::array<double, 2> gripperGapMaxMm{{25.0, 25.0}};
  std::array<bool, 2> gripperGapInvert{{false, false}};
  std::array<std::string, 2> gripperSourceHand{{"PhysicalRight", "PhysicalLeft"}};
  int gripperDeadbandCounts{1};
  double gripperMinCommandIntervalMs{20.0};
  bool gripperIcfTargetProtectionEnabled{true};
  double gripperIcfTargetMinGapMm{1.02};
  bool gripperButtonFallback{true};
};

// 单次遥操作输出诊断。该结构会被序列化到 statusJson，便于前端和日志回放最后动作。
struct NativeTeleopAction {
  // ts 是墙钟毫秒，monotonicS 用于分析控制环间隔。
  std::int64_t ts{};
  double monotonicS{};
  // side 是目标从端侧，sourceSide 是产生该动作的主手侧。
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
  std::array<double, 6> stopReason{};
  std::array<double, 6> axisIoStatus{};
  std::array<bool, 6> movingBefore{};
  std::array<bool, 6> moveStarted{};
  std::array<bool, 6> clipped{};
};

// NativeTeleopController 连接 Omega.7 主手、LTDMC 从端运动和 Jodell 夹爪。
// 它只负责实时映射和安全门控，不拥有底层驱动生命周期。
class NativeTeleopController {
 public:
  using LeaderStatePublisher = std::function<void(const std::array<Omega7State, 2>&)>;
  using HardwareTargetPublisher = std::function<void(const TeleopHardwareTarget&)>;

  NativeTeleopController(LTDMCDriver& motion, Omega7Driver& omega, JodellGripperDriver& gripper);
  ~NativeTeleopController();

  void configure(const NativeTeleopConfig& config);
  void configureGripper(const JodellGripperConfig& config);
  // 运行时更新夹爪保护，主要给后端配置热更新使用。
  void configureGripperProtection(bool enabled, double minGapMm);
  // leftConnected/rightConnected 是逻辑主手连接状态，用于在部分连接时只启动可用通道。
  void start(bool leftConnected, bool rightConnected);
  void stop();
  void requestEmergencyStop();
  // statusJson 直接面向 HalServer 响应，包含 blocker、最后动作、夹爪和滤波诊断。
  std::string statusJson() const;
  bool running() const;
  void setLeaderStatePublisher(LeaderStatePublisher publisher);
  void setHardwareTargetPublisher(HardwareTargetPublisher publisher);
  void processLeaderState(const std::array<Omega7State, 2>& hands, double dtSec);
  // 手动夹爪命令会进入同一条 gripper worker 队列，避免和 teleop 自动命令交叉写串口。
  bool commandGripperTarget(Side side, double targetMm, int speed, int torque, std::string* message = nullptr);

 private:
  // 每个目标侧只保留最新一条夹爪命令，teleop 高频输入会被合并为最新目标。
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

  // 主循环与夹爪循环分离：运动控制需要稳定周期，夹爪串口读写可能阻塞。
  void loop();
  void startGripperWorker();
  void stopGripperWorker();
  void gripperLoop();
  void sampleGripperPosition(Side side);
  void tick(double dtSec);
  void tickSideBestEffort(int sourceIndex, const Omega7State& hand, double dtSec);
  void tickSide(int sourceIndex, const Omega7State& hand, double dtSec);
  // Kalman 只处理主手语义位姿，输出仍是同样的 6 轴数组；dtSec 被夹在配置上下限内。
  std::array<double, 6> kalmanPoseForSide(
      int sourceIndex,
      const std::array<double, 6>& rawSemanticPose,
      double dtSec);
  double updateKalmanAxis(int axisIndex, double measurement, double dtSec, KalmanAxisState& state);
  double kalmanIntentWeight(int axisIndex, const KalmanAxisState& state) const;
  std::array<double, 6> applyKalmanIntentWeights(int sourceIndex, std::array<double, 6> deltas) const;
  void resetKalmanSideUnlocked(int sourceIndex);
  // tickGrippers 根据 Omega.7 主手开口或按钮兜底生成从端夹爪目标。
  void tickGrippers(const std::array<Omega7State, 2>& hands);
  void enqueueGripperCommand(int targetIndex, Side side, double targetMm, int speed, int torque);
  void setBlockerUnlocked(int sourceIndex, const std::string& state, const std::string& message);
  void recordPublishedTargetActionUnlocked(Side sourceSide, const TeleopHardwareTarget& target, int sourceIndex);
  void recordActionUnlocked(Side sourceSide, Side targetSide, const TeleopTargetUpdateResult& result);
  void recordZeroStopActionUnlocked(Side sourceSide, Side targetSide);
  void syncIncrementalZeroDeltaUnlocked(
      Side sourceSide,
      Side targetSide,
      int sourceIndex,
      int targetIndex,
      const std::array<double, 6>& semanticPose,
      const std::string& message);
  // 回零/参考切换后，旋转轴第一帧容易出现大跳变；这里按配置识别并抑制。
  bool suppressIncrementalRotationSpikeUnlocked(
      Side sourceSide,
      Side targetSide,
      int sourceIndex,
      int targetIndex,
      const std::array<double, 6>& semanticPose);
  std::array<double, 6> velocityDeltasUi(
      int sourceIndex,
      Side targetSide,
      const std::array<double, 6>& pose,
      double dtSec);
  std::array<double, 6> incrementalDeltasUi(int sourceIndex, Side targetSide, const std::array<double, 6>& pose);
  // 连续增量模式的亚脉冲累积和方向门控，返回本帧实际允许下发的整数脉冲。
  long applyContinuousPulseGate(int sourceIndex, int axisIndex, long requestedPulse, double requestedPulseFloat);
  double mappedDirection(int sourceIndex, Side targetSide, int axisIndex) const;
  std::array<AxisLimit, 6> effectiveSoftLimits(Side targetSide, int targetIndex) const;
  int gripperSourceIndex(int targetIndex) const;
  double effectiveGripperTargetMm(double targetMm) const;
  Side sideFromIndex(int index) const;
  int sideIndex(Side side) const;

  // 底层驱动由外部构造并保证生命周期覆盖控制器。
  LTDMCDriver& motion_;
  Omega7Driver& omega_;
  JodellGripperDriver& gripper_;
  // mutex_ 保护 teleop 配置、引用位姿、诊断状态和动作历史；夹爪队列使用单独 mutex。
  mutable std::mutex mutex_;
  NativeTeleopConfig config_{};
  LeaderStatePublisher leaderStatePublisher_;
  HardwareTargetPublisher hardwareTargetPublisher_;
  std::uint64_t hardwareTargetSequence_{0};
  std::atomic<bool> running_{false};
  std::thread worker_;
  // logicalConnected_ 是启动时认定可用的主手通道，targetActive_ 表示该通道当前正在输出运动。
  std::array<bool, 2> logicalConnected_{{false, false}};
  std::array<bool, 2> targetActive_{{false, false}};
  // referencePose_ 是增量模式的零点，referenceValid_ 为 false 时下一帧会重新同步参考。
  std::array<std::array<double, 6>, 2> referencePose_{};
  std::array<bool, 2> referenceValid_{{false, false}};
  // velocityUiPerSec_ 是 velocity 模式的平滑速度缓存；incremental* 是位置增量模式的残差和方向状态。
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
  // lastRequestedPulse/lastEmittedPulse/lastOutputDeltaUi 保留最近一次映射结果，供 statusJson 排障。
  std::array<std::array<double, 6>, 2> lastRequestedPulse_{};
  std::array<std::array<double, 6>, 2> lastEmittedPulse_{};
  std::array<std::array<double, 6>, 2> lastOutputDeltaUi_{};
  std::array<Side, 2> lastDiagnosticTargetSide_{{Side::Right, Side::Left}};
  // blockerState/message 说明某通道当前为什么未输出，例如未按 clutch、主手未连接或轴未使能。
  std::array<std::string, 2> blockerState_{{"idle", "idle"}};
  std::array<std::string, 2> blockerMessage_{};
  std::string lastError_;
  // actionHistory_ 只保存最近动作窗口，避免长时间运行时内存持续增长。
  NativeTeleopAction lastAction_{};
  bool hasLastAction_{false};
  std::deque<NativeTeleopAction> actionHistory_;
  // 夹爪状态缓存由 gripperMutex_ 保护；命令线程通过 condition_variable 等待新目标。
  std::array<int, 2> gripperLastRaw_{{-1, -1}};
  std::array<std::chrono::steady_clock::time_point, 2> gripperLastCommandAt_{};
  std::array<double, 2> gripperTargetsMm_{{0.0, 0.0}};
  std::array<double, 2> gripperSourceGapMm_{{-1.0, -1.0}};
  std::array<bool, 2> gripperSourceGapAvailable_{{false, false}};
  std::array<double, 2> gripperPositionsMm_{{-1.0, -1.0}};
  std::array<bool, 2> gripperLastCommandOk_{{false, false}};
  std::array<std::string, 2> gripperLastMessage_{};
  std::array<std::int64_t, 2> gripperLastCommandTs_{{0, 0}};
  std::mutex gripperMutex_;
  std::condition_variable gripperCv_;
  std::thread gripperWorker_;
  std::atomic<bool> gripperWorkerRunning_{false};
  std::array<PendingGripperCommand, 2> pendingGripperCommands_{};
};

}  // namespace appstation::hal
