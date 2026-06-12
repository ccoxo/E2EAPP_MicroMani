#include "NativeTeleopController.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <sstream>

namespace appstation::hal {

namespace {

constexpr const char* kVelocityAdmittanceMode = "velocity_admittance";
constexpr const char* kIncrementalPositionMode = "incremental_position";
constexpr int kGripperTeleopDeadbandFloorCounts = 1;
constexpr double kGripperTeleopMinCommandIntervalFloorMs = 10.0;
constexpr double kIncrementalRotationSpikeGuardDeg = 5.0;
constexpr auto kGripperPositionSampleInterval = std::chrono::microseconds(33333);

std::int64_t unixTimeMs() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

double monotonicSeconds() {
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

const char* sideName(Side side) {
  return side == Side::Left ? "left" : "right";
}

const char* axisName(int index) {
  switch (index) {
    case 0: return "X";
    case 1: return "Y";
    case 2: return "Z";
    case 3: return "Roll";
    case 4: return "Pitch";
    default: return "Yaw";
  }
}

double wrapKalmanRotationResidualDeg(double residualDeg) {
  // residualDeg：旋转轴观测残差，单位为度；归一化后避免 +180/-180 附近出现假大跳变。
  double wrapped = std::fmod(residualDeg + 180.0, 360.0);
  if (wrapped < 0.0) {
    wrapped += 360.0;
  }
  return wrapped - 180.0;
}

std::string jsonEscape(const std::string& value) {
  std::ostringstream out;
  for (char ch : value) {
    switch (ch) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default: out << ch; break;
    }
  }
  return out.str();
}

template <size_t N>
void appendArray(std::ostringstream& out, const std::array<double, N>& values) {
  out << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << values[i];
  }
  out << "]";
}

template <size_t N>
void appendBoolArray(std::ostringstream& out, const std::array<bool, N>& values) {
  out << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << (values[i] ? "true" : "false");
  }
  out << "]";
}

void appendAction(std::ostringstream& out, const NativeTeleopAction& action) {
  out << "{\"ts\":" << action.ts
      << ",\"monotonicMs\":" << static_cast<std::int64_t>(std::llround(action.monotonicS * 1000.0))
      << ",\"monotonic_s\":" << action.monotonicS
      << ",\"side\":\"" << sideName(action.side) << "\""
      << ",\"sourceSide\":\"" << sideName(action.sourceSide) << "\""
      << ",\"axis\":\"" << axisName(action.axisIndex) << "\""
      << ",\"delta\":" << action.delta
      << ",\"unit\":\"" << (action.axisIndex < 3 ? "um" : "deg") << "\""
      << ",\"deltas\":";
  appendArray(out, action.deltas);
  out << ",\"appliedDeltas\":";
  appendArray(out, action.deltas);
  out << ",\"requestedDeltaPulse\":";
  appendArray(out, action.requestedDeltaPulse);
  out << ",\"appliedDeltaPulse\":";
  appendArray(out, action.appliedDeltaPulse);
  out << ",\"targetPulse\":";
  appendArray(out, action.targetPulse);
  out << ",\"currentPulse\":";
  appendArray(out, action.currentPulse);
  out << ",\"launchDeltaPulse\":";
  appendArray(out, action.launchDeltaPulse);
  out << ",\"updateReturn\":";
  appendArray(out, action.updateReturn);
  out << ",\"stopReason\":";
  appendArray(out, action.stopReason);
  out << ",\"axisIoStatus\":";
  appendArray(out, action.axisIoStatus);
  out << ",\"movingBefore\":";
  appendBoolArray(out, action.movingBefore);
  out << ",\"moveStarted\":";
  appendBoolArray(out, action.moveStarted);
  out << ",\"clipped\":";
  appendBoolArray(out, action.clipped);
  out << ",\"deltaVector\":";
  appendArray(out, action.deltaVector);
  out << "}";
}

void appendInputDiagnostic(
    std::ostringstream& out,
    const char* sourceName,
    Side targetSide,
    bool referenceValid,
    bool inputActive,
    const std::array<double, 6>& semanticPose,
    const std::array<double, 6>& referencePose,
    const std::array<double, 6>& rawDelta,
    const std::array<double, 6>& filteredDelta,
    const std::array<double, 6>& requestedPulse,
    const std::array<double, 6>& emittedPulse,
    const std::array<double, 6>& outputDeltaUi) {
  out << "\"" << sourceName << "\":{\"targetSide\":\"" << sideName(targetSide) << "\""
      << ",\"referenceValid\":" << (referenceValid ? "true" : "false")
      << ",\"inputActive\":" << (inputActive ? "true" : "false")
      << ",\"semanticPose\":";
  appendArray(out, semanticPose);
  out << ",\"referencePose\":";
  appendArray(out, referencePose);
  out << ",\"rawDelta\":";
  appendArray(out, rawDelta);
  out << ",\"filteredDelta\":";
  appendArray(out, filteredDelta);
  out << ",\"requestedPulse\":";
  appendArray(out, requestedPulse);
  out << ",\"emittedPulse\":";
  appendArray(out, emittedPulse);
  out << ",\"outputDeltaUi\":";
  appendArray(out, outputDeltaUi);
  out << "}";
}

bool hasMotion(const std::array<double, 6>& deltas) {
  for (double value : deltas) {
    if (std::abs(value) > 1e-9) {
      return true;
    }
  }
  return false;
}

double applyDeadzone(double value, double threshold) {
  return std::abs(value) < threshold ? 0.0 : value;
}

int signOfValue(double value) {
  if (value > 1e-12) {
    return 1;
  }
  if (value < -1e-12) {
    return -1;
  }
  return 0;
}

std::array<double, 6> omegaPoseToSemantic(const std::array<double, 6>& raw, const std::string& mappingMode) {
  if (mappingMode == "legacy") {
    return {raw[1], raw[0], raw[2], raw[3], raw[5], raw[4]};
  }
  return {raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]};
}

}  // namespace

NativeTeleopController::NativeTeleopController(
    LTDMCDriver& motion,
    Omega7Driver& omega,
    JodellGripperDriver& gripper)
    : motion_(motion), omega_(omega), gripper_(gripper) {
  for (auto& sideLimits : config_.softLimits) {
    sideLimits = {
        AxisLimit{-25000.0, 25000.0},
        AxisLimit{-37500.0, 37500.0},
        AxisLimit{-37500.0, 37500.0},
        AxisLimit{-100.0, 100.0},
        AxisLimit{-100.0, 100.0},
        AxisLimit{-7.0, 7.0},
    };
  }
  for (auto& sideLimits : config_.rotationWorkLimits) {
    sideLimits = {
        AxisLimit{0.0, 0.0},
        AxisLimit{0.0, 0.0},
        AxisLimit{0.0, 0.0},
        AxisLimit{-100.0, 100.0},
        AxisLimit{-100.0, 100.0},
        AxisLimit{-7.0, 7.0},
    };
  }
}

NativeTeleopController::~NativeTeleopController() {
  stop();
}

void NativeTeleopController::configure(const NativeTeleopConfig& config) {
  NativeTeleopConfig normalized = config;
  if (normalized.controlMode == "incremental") {
    normalized.controlMode = kIncrementalPositionMode;
  }
  if (normalized.controlMode != kIncrementalPositionMode
      && normalized.controlMode != kVelocityAdmittanceMode) {
    normalized.controlMode = kIncrementalPositionMode;
  }
  if (normalized.mappingMode != "legacy" && normalized.mappingMode != "direct") {
    normalized.mappingMode = "direct";
  }
  normalized.translationDeadzoneM = std::max(0.0, normalized.translationDeadzoneM);
  normalized.rotationDeadzoneDeg = std::max(0.0, normalized.rotationDeadzoneDeg);
  normalized.translationInputEpsilonM = std::max(0.0, normalized.translationInputEpsilonM);
  normalized.rotationInputEpsilonDeg = std::max(0.0, normalized.rotationInputEpsilonDeg);
  normalized.translationMinActivePulse = std::max(0.0, normalized.translationMinActivePulse);
  normalized.rotationMinActivePulse = std::max(0.0, normalized.rotationMinActivePulse);
  normalized.continuousMicroConfirmTicks = std::max(0, normalized.continuousMicroConfirmTicks);
  // kalmanBeta：遗忘因子 beta，限制在 [0,1] 内，保证 Q/R 的凸组合更新有效。
  normalized.kalmanBeta = std::clamp(normalized.kalmanBeta, 0.0, 1.0);
  // kalmanMinVariance：所有方差/协方差的保护下限，避免数值退化。
  normalized.kalmanMinVariance = std::max(1e-15, normalized.kalmanMinVariance);
  // kalmanMaxVariance：所有方差/协方差的保护上限，且不能小于下限。
  normalized.kalmanMaxVariance = std::max(normalized.kalmanMinVariance, normalized.kalmanMaxVariance);
  // kalmanDtMinSec：dt 下限，过滤异常小的采样间隔。
  normalized.kalmanDtMinSec = std::clamp(normalized.kalmanDtMinSec, 0.0001, 1.0);
  // kalmanDtMaxSec：dt 上限，过滤线程卡顿带来的异常大预测步长。
  normalized.kalmanDtMaxSec = std::max(normalized.kalmanDtMinSec, normalized.kalmanDtMaxSec);
  // kalmanTranslationPositionVariance：平移轴 P00 初值，至少为 minVariance。
  normalized.kalmanTranslationPositionVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanTranslationPositionVariance);
  // kalmanTranslationVelocityVariance：平移轴 P11 初值，至少为 minVariance。
  normalized.kalmanTranslationVelocityVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanTranslationVelocityVariance);
  // kalmanTranslationMeasurementVariance：平移轴 R 初值，至少为 minVariance。
  normalized.kalmanTranslationMeasurementVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanTranslationMeasurementVariance);
  // kalmanTranslationProcessPositionVariance：平移轴 Q00 初值，至少为 minVariance。
  normalized.kalmanTranslationProcessPositionVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanTranslationProcessPositionVariance);
  // kalmanTranslationProcessVelocityVariance：平移轴 Q11 初值，至少为 minVariance。
  normalized.kalmanTranslationProcessVelocityVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanTranslationProcessVelocityVariance);
  // kalmanRotationPositionVariance：旋转轴 P00 初值，至少为 minVariance。
  normalized.kalmanRotationPositionVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanRotationPositionVariance);
  // kalmanRotationVelocityVariance：旋转轴 P11 初值，至少为 minVariance。
  normalized.kalmanRotationVelocityVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanRotationVelocityVariance);
  // kalmanRotationMeasurementVariance：旋转轴 R 初值，至少为 minVariance。
  normalized.kalmanRotationMeasurementVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanRotationMeasurementVariance);
  // kalmanRotationProcessPositionVariance：旋转轴 Q00 初值，至少为 minVariance。
  normalized.kalmanRotationProcessPositionVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanRotationProcessPositionVariance);
  // kalmanRotationProcessVelocityVariance：旋转轴 Q11 初值，至少为 minVariance。
  normalized.kalmanRotationProcessVelocityVariance =
      std::max(normalized.kalmanMinVariance, normalized.kalmanRotationProcessVelocityVariance);
  // kalmanTranslationIntentVelocityThreshold：平移轴 v_th，必须为正，供 w2 意图权重使用。
  normalized.kalmanTranslationIntentVelocityThreshold =
      std::max(1e-12, normalized.kalmanTranslationIntentVelocityThreshold);
  // kalmanRotationIntentVelocityThreshold：旋转轴 v_th，必须为正，供 w2 意图权重使用。
  normalized.kalmanRotationIntentVelocityThreshold =
      std::max(1e-12, normalized.kalmanRotationIntentVelocityThreshold);
  normalized.gripperDeadbandCounts = std::max(
      kGripperTeleopDeadbandFloorCounts,
      normalized.gripperDeadbandCounts);
  normalized.gripperMinCommandIntervalMs = std::max(
      kGripperTeleopMinCommandIntervalFloorMs,
      normalized.gripperMinCommandIntervalMs);
  normalized.gripperIcfTargetMinGapMm = std::clamp(
      normalized.gripperIcfTargetMinGapMm,
      0.0,
      std::max(0.001, normalized.gripper.strokeMm));
  normalized.incrementalTranslationMinEffectiveDeltaM = std::max(
      normalized.translationDeadzoneM,
      normalized.incrementalTranslationMinEffectiveDeltaM);
  normalized.incrementalTranslationReverseDeadzoneM = std::max(
      normalized.incrementalTranslationMinEffectiveDeltaM,
      normalized.incrementalTranslationReverseDeadzoneM);
  for (auto& sideLimits : normalized.softLimits) {
    for (auto& limit : sideLimits) {
      if (limit.min >= limit.max) {
        limit = AxisLimit{-1.0, 1.0};
      }
    }
  }
  for (auto& sideLimits : normalized.rotationWorkLimits) {
    for (int axisIndex = 3; axisIndex < 6; ++axisIndex) {
      auto& limit = sideLimits[axisIndex];
      if (limit.min >= limit.max) {
        limit = axisIndex == 5 ? AxisLimit{-7.0, 7.0} : AxisLimit{-100.0, 100.0};
      }
    }
  }
  {
    std::scoped_lock lock(mutex_);
    config_ = normalized;
    // kalmanStates_：配置变化后清空滤波状态，下一帧用新参数重新初始化。
    kalmanStates_ = {};
    // lastIntentWeight_：配置变化后先恢复 w2=1，避免旧权重影响新配置首帧。
    for (auto& weights : lastIntentWeight_) {
      // weights：单只主手的 6 个语义轴 w2 权重数组。
      weights.fill(1.0);
    }
  }
  gripper_.configure(normalized.gripper);
  if (!normalized.gripperTeleopEnabled) {
    stopGripperWorker();
  }
  omega_.setGravityCompensation(normalized.leftGravityCompensation, normalized.rightGravityCompensation);
}

void NativeTeleopController::configureGripper(const JodellGripperConfig& config) {
  {
    std::scoped_lock lock(mutex_);
    config_.gripper = config;
    config_.gripperIcfTargetMinGapMm = std::clamp(
        config_.gripperIcfTargetMinGapMm,
        0.0,
        std::max(0.001, config_.gripper.strokeMm));
  }
  gripper_.configure(config);
}

void NativeTeleopController::configureGripperProtection(bool enabled, double minGapMm) {
  std::scoped_lock lock(mutex_);
  config_.gripperIcfTargetProtectionEnabled = enabled;
  config_.gripperIcfTargetMinGapMm = std::clamp(
      minGapMm,
      0.0,
      std::max(0.001, config_.gripper.strokeMm));
}

void NativeTeleopController::start(bool leftConnected, bool rightConnected) {
  bool gripperTeleopEnabled = false;
  {
    std::scoped_lock lock(mutex_);
    logicalConnected_ = {leftConnected, rightConnected};
    gripperTeleopEnabled = config_.gripperTeleopEnabled;
    referenceValid_ = {false, false};
    targetActive_ = {false, false};
    velocityUiPerSec_ = {};
    incrementalCarry_ = {};
    incrementalDirection_ = {};
    incrementalInputActive_ = {false, false};
    continuousPulseCarry_ = {};
    continuousDirection_ = {};
    continuousStreak_ = {};
    // kalmanStates_：启动 teleop 时清空历史滤波状态，保证新会话从首帧重新初始化。
    kalmanStates_ = {};
    // lastIntentWeight_：启动时恢复 w2=1，避免上次会话的意图权重残留。
    for (auto& weights : lastIntentWeight_) {
      // weights：单只主手的 6 个语义轴 w2 权重数组。
      weights.fill(1.0);
    }
    lastSemanticPose_ = {};
    lastRawDelta_ = {};
    lastFilteredDelta_ = {};
    lastRequestedPulse_ = {};
    lastEmittedPulse_ = {};
    lastOutputDeltaUi_ = {};
    lastDiagnosticTargetSide_ = {Side::Right, Side::Left};
    blockerState_ = {"idle", "idle"};
    blockerMessage_ = {};
    lastError_.clear();
    hasLastAction_ = false;
    actionHistory_.clear();
    gripperLastRaw_ = {-1, -1};
    gripperLastCommandOk_ = {false, false};
    gripperLastMessage_ = {};
    gripperLastCommandTs_ = {0, 0};
  }
  if (gripperTeleopEnabled) {
    startGripperWorker();
  } else {
    stopGripperWorker();
  }
  if (running_.exchange(true)) {
    return;
  }
  worker_ = std::thread(&NativeTeleopController::loop, this);
}

void NativeTeleopController::stop() {
  if (running_.exchange(false)) {
    if (worker_.joinable()) {
      worker_.join();
    }
  }
  try {
    motion_.stopTeleopSide(Side::Left);
    motion_.stopTeleopSide(Side::Right);
  } catch (const std::exception& exc) {
    std::scoped_lock lock(mutex_);
      lastError_ = exc.what();
  }
  stopGripperWorker();
  std::scoped_lock lock(mutex_);
  logicalConnected_ = {false, false};
  referenceValid_ = {false, false};
  targetActive_ = {false, false};
  velocityUiPerSec_ = {};
    incrementalCarry_ = {};
    incrementalDirection_ = {};
    incrementalInputActive_ = {false, false};
    continuousPulseCarry_ = {};
    continuousDirection_ = {};
    continuousStreak_ = {};
    // kalmanStates_：停止 teleop 时清空滤波状态，避免下一次启动复用旧估计。
    kalmanStates_ = {};
    // lastIntentWeight_：停止时恢复 w2=1，与清空滤波状态保持一致。
    for (auto& weights : lastIntentWeight_) {
      // weights：单只主手的 6 个语义轴 w2 权重数组。
      weights.fill(1.0);
    }
    lastRawDelta_ = {};
    lastFilteredDelta_ = {};
    lastRequestedPulse_ = {};
    lastEmittedPulse_ = {};
    lastOutputDeltaUi_ = {};
}

void NativeTeleopController::startGripperWorker() {
  bool expected = false;
  if (!gripperWorkerRunning_.compare_exchange_strong(expected, true)) {
    return;
  }
  gripperWorker_ = std::thread(&NativeTeleopController::gripperLoop, this);
}

void NativeTeleopController::stopGripperWorker() {
  if (gripperWorkerRunning_.exchange(false)) {
    gripperCv_.notify_all();
  }
  if (gripperWorker_.joinable()) {
    gripperWorker_.join();
  }
  std::scoped_lock lock(gripperMutex_);
  pendingGripperCommands_ = {};
}

void NativeTeleopController::gripperLoop() {
  auto nextSampleAt = std::chrono::steady_clock::now();
  while (gripperWorkerRunning_.load()) {
    std::array<PendingGripperCommand, 2> commands{};
    bool shouldSample = false;
    {
      std::unique_lock lock(gripperMutex_);
      gripperCv_.wait_until(lock, nextSampleAt, [&] {
        return !gripperWorkerRunning_.load()
            || pendingGripperCommands_[0].pending
            || pendingGripperCommands_[1].pending;
      });
      if (!gripperWorkerRunning_.load()) {
        break;
      }
      commands = pendingGripperCommands_;
      pendingGripperCommands_ = {};
      const auto now = std::chrono::steady_clock::now();
      if (now >= nextSampleAt) {
        shouldSample = true;
        nextSampleAt = now + kGripperPositionSampleInterval;
      }
    }
    for (const auto& command : commands) {
      if (!command.pending) {
        continue;
      }
      std::string message;
      const bool ok = gripper_.commandTarget(
          command.side,
          command.targetMm,
          command.speed,
          command.torque,
          &message,
          false);
      {
        std::scoped_lock lock(mutex_);
        const auto gripperPositions = gripper_.positionMmSnapshot(gripperPositionsMm_);
        gripperPositionsMm_ = gripperPositions;
        gripperLastCommandOk_[command.targetIndex] = ok;
        gripperLastMessage_[command.targetIndex] = message;
        gripperLastCommandTs_[command.targetIndex] = unixTimeMs();
      }
    }
    if (shouldSample) {
      sampleGripperPosition(Side::Left);
      sampleGripperPosition(Side::Right);
    }
  }
}

void NativeTeleopController::sampleGripperPosition(Side side) {
  std::string message;
  const bool ok = gripper_.readPositionMm(side, &message);
  const int index = sideIndex(side);
  std::scoped_lock lock(mutex_);
  gripperPositionsMm_ = gripper_.positionMmSnapshot(gripperPositionsMm_);
  gripperLastCommandOk_[index] = ok;
  if (!message.empty()) {
    gripperLastMessage_[index] = message;
  }
}

bool NativeTeleopController::running() const {
  return running_.load();
}

bool NativeTeleopController::commandGripperTarget(
    Side side,
    double targetMm,
    int speed,
    int torque,
    std::string* message) {
  const int index = sideIndex(side);
  double bounded = targetMm;
  {
    std::scoped_lock lock(mutex_);
    bounded = effectiveGripperTargetMm(targetMm);
  }
  if (gripperWorkerRunning_.load()) {
    {
      std::scoped_lock lock(mutex_);
      gripperTargetsMm_[index] = bounded;
      gripperLastCommandOk_[index] = true;
      gripperLastMessage_[index] = "queued native gripper command";
      gripperLastCommandTs_[index] = unixTimeMs();
    }
    enqueueGripperCommand(index, side, bounded, speed, torque);
    if (message) {
      *message = "queued native gripper command";
    }
    return true;
  }

  std::string driverMessage;
  const bool ok = gripper_.commandTarget(side, bounded, speed, torque, &driverMessage);
  {
    std::scoped_lock lock(mutex_);
    gripperTargetsMm_[index] = bounded;
    gripperPositionsMm_ = gripper_.positionMmSnapshot(gripperPositionsMm_);
    gripperLastCommandOk_[index] = ok;
    gripperLastMessage_[index] = driverMessage;
    gripperLastCommandTs_[index] = unixTimeMs();
  }
  if (message) {
    *message = driverMessage;
  }
  return ok;
}

std::string NativeTeleopController::statusJson() const {
  const auto forceOutput = omega_.forceOutputEnabled();
  std::scoped_lock lock(mutex_);
  const auto gripperPositions = gripper_.positionMmSnapshot(gripperPositionsMm_);
  std::ostringstream out;
  out << "{\"running\":" << (running_.load() ? "true" : "false")
      << ",\"controlMode\":\"" << jsonEscape(config_.controlMode) << "\""
      << ",\"mappingMode\":\"" << jsonEscape(config_.mappingMode) << "\""
      << ",\"lastError\":\"" << jsonEscape(lastError_) << "\""
      << ",\"logicalConnected\":["
      << (logicalConnected_[0] ? "true" : "false") << ","
      << (logicalConnected_[1] ? "true" : "false") << "]"
      << ",\"blockers\":{";
  for (int i = 0; i < 2; ++i) {
    if (i > 0) {
      out << ",";
    }
    out << "\"" << (i == 0 ? "left" : "right") << "\":{\"state\":\"" << jsonEscape(blockerState_[i])
        << "\",\"message\":\"" << jsonEscape(blockerMessage_[i]) << "\"}";
  }
  out << "},\"lastAction\":";
  if (hasLastAction_) {
    appendAction(out, lastAction_);
  } else {
    out << "null";
  }
  out << ",\"actionHistory\":[";
  for (size_t i = 0; i < actionHistory_.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    appendAction(out, actionHistory_[i]);
  }
  out << "],\"inputs\":{";
  appendInputDiagnostic(
      out,
      "left",
      lastDiagnosticTargetSide_[0],
      referenceValid_[0],
      incrementalInputActive_[0],
      lastSemanticPose_[0],
      referencePose_[0],
      lastRawDelta_[0],
      lastFilteredDelta_[0],
      lastRequestedPulse_[0],
      lastEmittedPulse_[0],
      lastOutputDeltaUi_[0]);
  out << ",";
  appendInputDiagnostic(
      out,
      "right",
      lastDiagnosticTargetSide_[1],
      referenceValid_[1],
      incrementalInputActive_[1],
      lastSemanticPose_[1],
      referencePose_[1],
      lastRawDelta_[1],
      lastFilteredDelta_[1],
      lastRequestedPulse_[1],
      lastEmittedPulse_[1],
      lastOutputDeltaUi_[1]);
  out << "},\"gripperTargets\":";
  appendArray(out, gripperTargetsMm_);
  out << ",\"grippers\":{\"left\":{\"ok\":" << (gripperLastCommandOk_[0] ? "true" : "false")
      << ",\"targetMm\":" << gripperTargetsMm_[0]
      << ",\"positionMm\":";
  if (gripperPositions[0] >= 0.0) {
    out << gripperPositions[0];
  } else {
    out << "null";
  }
  out
      << ",\"message\":\"" << jsonEscape(gripperLastMessage_[0]) << "\""
      << ",\"lastCommandTs\":" << gripperLastCommandTs_[0]
      << "},\"right\":{\"ok\":" << (gripperLastCommandOk_[1] ? "true" : "false")
      << ",\"targetMm\":" << gripperTargetsMm_[1]
      << ",\"positionMm\":";
  if (gripperPositions[1] >= 0.0) {
    out << gripperPositions[1];
  } else {
    out << "null";
  }
  out
      << ",\"message\":\"" << jsonEscape(gripperLastMessage_[1]) << "\""
      << ",\"lastCommandTs\":" << gripperLastCommandTs_[1]
      << "}}";
  out << ",\"gravityCompensation\":["
      << (config_.leftGravityCompensation ? "true" : "false") << ","
      << (config_.rightGravityCompensation ? "true" : "false") << "]"
      << ",\"forceOutputEnabled\":["
      << (forceOutput[0] ? "true" : "false") << ","
      << (forceOutput[1] ? "true" : "false") << "]"
      << ",\"gripperCommand\":{\"speed\":" << config_.gripper.speed
      << ",\"torque\":" << config_.gripper.torque
      << ",\"workerMode\":\"" << (config_.gripper.processWorkersEnabled ? "isolated_process" : "inline") << "\""
      << ",\"processWorkersEnabled\":"
      << (config_.gripper.processWorkersEnabled ? "true" : "false")
      << ",\"deadbandCounts\":" << config_.gripperDeadbandCounts
      << ",\"minCommandIntervalMs\":" << config_.gripperMinCommandIntervalMs
      << ",\"icfTargetProtectionEnabled\":"
      << (config_.gripperIcfTargetProtectionEnabled ? "true" : "false")
      << ",\"icfTargetMinGapMm\":" << config_.gripperIcfTargetMinGapMm << "}}";
  return out.str();
}

void NativeTeleopController::loop() {
  auto previous = std::chrono::steady_clock::now();
  while (running_.load()) {
    const auto started = std::chrono::steady_clock::now();
    const auto dt = std::chrono::duration<double>(started - previous).count();
    previous = started;
    try {
      tick(dt > 0.0 ? dt : 0.01);
    } catch (const std::exception& exc) {
      std::scoped_lock lock(mutex_);
      lastError_ = exc.what();
    }
    int hz = 100;
    {
      std::scoped_lock lock(mutex_);
      hz = std::max(1, config_.loopHz);
    }
    const auto period = std::chrono::microseconds(1000000 / hz);
    const auto elapsed = std::chrono::steady_clock::now() - started;
    if (elapsed < period) {
      std::this_thread::sleep_for(period - elapsed);
    }
  }
}

void NativeTeleopController::tick(double dtSec) {
  const auto hands = omega_.readState();
  tickSideBestEffort(0, hands[0], dtSec);
  tickSideBestEffort(1, hands[1], dtSec);
  try {
    tickGrippers(hands);
  } catch (const std::exception& exc) {
    std::scoped_lock lock(mutex_);
    lastError_ = exc.what();
  }
}

void NativeTeleopController::tickSideBestEffort(int sourceIndex, const Omega7State& hand, double dtSec) {
  try {
    tickSide(sourceIndex, hand, dtSec);
  } catch (const std::exception& exc) {
    std::scoped_lock lock(mutex_);
    lastError_ = exc.what();
    setBlockerUnlocked(sourceIndex, "blocked", exc.what());
    incrementalInputActive_[sourceIndex] = false;
    lastFilteredDelta_[sourceIndex] = {};
    lastRequestedPulse_[sourceIndex] = {};
    lastEmittedPulse_[sourceIndex] = {};
    lastOutputDeltaUi_[sourceIndex] = {};
  }
}

void NativeTeleopController::tickSide(int sourceIndex, const Omega7State& hand, double dtSec) {
  std::scoped_lock lock(mutex_);
  const Side sourceSide = sideFromIndex(sourceIndex);
  const Side targetSide = config_.swapTeleopChannels ? sideFromIndex(1 - sourceIndex) : sourceSide;
  const int targetIndex = sideIndex(targetSide);
  const bool logicalConnected = logicalConnected_[sourceIndex];
  lastDiagnosticTargetSide_[sourceIndex] = targetSide;
  if (!logicalConnected) {
    setBlockerUnlocked(sourceIndex, "idle", "logical hand is disconnected");
    resetKalmanSideUnlocked(sourceIndex);
    referenceValid_[sourceIndex] = false;
    incrementalCarry_[sourceIndex] = {};
    incrementalDirection_[sourceIndex] = {};
    incrementalInputActive_[sourceIndex] = false;
    continuousPulseCarry_[sourceIndex] = {};
    continuousDirection_[sourceIndex] = {};
    continuousStreak_[sourceIndex] = {};
    if (targetActive_[targetIndex]) {
      motion_.stopTeleopSide(targetSide);
      targetActive_[targetIndex] = false;
    }
    return;
  }
  if (!hand.connected || !hand.lastReadOk) {
    setBlockerUnlocked(sourceIndex, "blocked", hand.lastReadError.empty() ? "Omega.7 read is unavailable" : hand.lastReadError);
    resetKalmanSideUnlocked(sourceIndex);
    referenceValid_[sourceIndex] = false;
    incrementalCarry_[sourceIndex] = {};
    incrementalDirection_[sourceIndex] = {};
    incrementalInputActive_[sourceIndex] = false;
    continuousPulseCarry_[sourceIndex] = {};
    continuousDirection_[sourceIndex] = {};
    continuousStreak_[sourceIndex] = {};
    if (targetActive_[targetIndex]) {
      motion_.stopTeleopSide(targetSide);
      targetActive_[targetIndex] = false;
    }
    return;
  }
  if (config_.requireClutch && !hand.clutchPressed) {
    setBlockerUnlocked(sourceIndex, "blocked", "clutch is required but not pressed");
    resetKalmanSideUnlocked(sourceIndex);
    referenceValid_[sourceIndex] = false;
    incrementalCarry_[sourceIndex] = {};
    incrementalDirection_[sourceIndex] = {};
    incrementalInputActive_[sourceIndex] = false;
    continuousPulseCarry_[sourceIndex] = {};
    continuousDirection_[sourceIndex] = {};
    continuousStreak_[sourceIndex] = {};
    if (targetActive_[targetIndex]) {
      motion_.stopTeleopSide(targetSide);
      targetActive_[targetIndex] = false;
    }
    return;
  }
  // rawSemanticPose：Omega.7 当前采样姿态 z_k 的语义轴表示，未经过 Kalman 滤波。
  const auto rawSemanticPose = omegaPoseToSemantic(hand.pose, config_.mappingMode);
  // semanticPose：开启滤波时为 Kalman 估计位置 p_hat，关闭时保持 rawSemanticPose。
  const auto semanticPose = kalmanPoseForSide(sourceIndex, rawSemanticPose, dtSec);
  lastSemanticPose_[sourceIndex] = semanticPose;
  lastDiagnosticTargetSide_[sourceIndex] = targetSide;
  if (!referenceValid_[sourceIndex]) {
    referencePose_[sourceIndex] = semanticPose;
    referenceValid_[sourceIndex] = true;
    velocityUiPerSec_[sourceIndex] = {};
    lastRawDelta_[sourceIndex] = {};
    lastFilteredDelta_[sourceIndex] = {};
    lastRequestedPulse_[sourceIndex] = {};
    lastEmittedPulse_[sourceIndex] = {};
    lastOutputDeltaUi_[sourceIndex] = {};
    setBlockerUnlocked(sourceIndex, "reference", "reference captured");
    return;
  }

  // deltas：由当前姿态计算出的从手 UI 增量；随后按 w2 意图权重进行软门控。
  if (config_.controlMode == kIncrementalPositionMode &&
      suppressIncrementalRotationSpikeUnlocked(sourceSide, targetSide, sourceIndex, targetIndex, semanticPose)) {
    return;
  }

  auto deltas = config_.controlMode == kIncrementalPositionMode
      ? incrementalDeltasUi(sourceIndex, targetSide, semanticPose)
      : velocityDeltasUi(sourceIndex, targetSide, semanticPose, dtSec);
  // applyKalmanIntentWeights：开启滤波时使用 v_hat 得到的 w2 衰减低置信度微动。
  deltas = applyKalmanIntentWeights(sourceIndex, deltas);
  if (!hasMotion(deltas)) {
    if (config_.controlMode == kIncrementalPositionMode) {
      const auto stopMessage = incrementalInputActive_[sourceIndex]
          ? "incremental input below output threshold stopped"
          : "incremental zero delta stopped";
      syncIncrementalZeroDeltaUnlocked(sourceSide, targetSide, sourceIndex, targetIndex, semanticPose, stopMessage);
      return;
    }
    setBlockerUnlocked(sourceIndex, "active", "inside native velocity deadzone");
    return;
  }

  const auto limits = effectiveSoftLimits(targetSide, targetIndex);
  const auto result = motion_.updateTeleopTargetUi(
      targetSide,
      deltas,
      config_.translationStepLimitPulse,
      config_.rotationStepLimitPulse,
      config_.translationPulseDeadband,
      config_.rotationPulseDeadband,
      config_.enabledAxes[targetIndex],
      true,
      limits,
      config_.translationMaxVelocityUmS,
      config_.rotationMaxVelocityDegS,
      config_.translationStartVelocityUmS,
      config_.rotationStartVelocityDegS,
      config_.accTimeSec,
      config_.decTimeSec);
  targetActive_[targetIndex] = true;
  setBlockerUnlocked(sourceIndex, "active", "");
  recordActionUnlocked(sourceSide, targetSide, result);
  if (config_.controlMode == kIncrementalPositionMode) {
    referencePose_[sourceIndex] = semanticPose;
  }
}

void NativeTeleopController::syncIncrementalZeroDeltaUnlocked(
    Side sourceSide,
    Side targetSide,
    int sourceIndex,
    int targetIndex,
    const std::array<double, 6>& semanticPose,
  const std::string& message) {
  if (targetActive_[targetIndex]) {
    motion_.stopTeleopSide(targetSide);
    targetActive_[targetIndex] = false;
    recordZeroStopActionUnlocked(sourceSide, targetSide);
  }
  referencePose_[sourceIndex] = semanticPose;
  setBlockerUnlocked(sourceIndex, "active", message);
}

std::array<double, 6> NativeTeleopController::kalmanPoseForSide(
    int sourceIndex,
    const std::array<double, 6>& rawSemanticPose,
    double dtSec) {
  // sourceIndex：当前主手输入索引，用来选择左右手各自独立的滤波状态。
  // rawSemanticPose：六轴观测向量 z_k，来自 Omega.7 当前采样姿态。
  // dtSec：本帧实际循环间隔，后续会夹紧为 boundedDtSec。
  // kalmanFilterEnabled：运行期开关；关闭时不改变返回值，并清空该主手的滤波历史。
  if (!config_.kalmanFilterEnabled) {
    resetKalmanSideUnlocked(sourceIndex);
    return rawSemanticPose;
  }
  // filteredPose：六个语义轴的滤波后位置/角度输出，对应每轴状态中的 p_hat。
  std::array<double, 6> filteredPose{};
  // boundedDtSec：夹紧后的采样周期 dt，进入状态转移矩阵 A 的时间项。
  const double boundedDtSec = std::clamp(dtSec, config_.kalmanDtMinSec, config_.kalmanDtMaxSec);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    // axisIndex：当前处理的语义轴，0-2 为平移轴，3-5 为旋转轴。
    // rawSemanticPose[axisIndex]：该轴本帧观测值 z_k。
    // kalmanStates_[sourceIndex][axisIndex]：该主手该轴的 x/P/Q/R 持久状态。
    filteredPose[axisIndex] = updateKalmanAxis(
        axisIndex,
        rawSemanticPose[axisIndex],
        boundedDtSec,
        kalmanStates_[sourceIndex][axisIndex]);
    // lastIntentWeight_：用更新后的速度估计 v_hat 计算 w2，供本帧下发增量软衰减。
    lastIntentWeight_[sourceIndex][axisIndex] =
        kalmanIntentWeight(axisIndex, kalmanStates_[sourceIndex][axisIndex]);
  }
  return filteredPose;
}

double NativeTeleopController::updateKalmanAxis(
    int axisIndex,
    double measurement,
    double dtSec,
    KalmanAxisState& state) {
  // axisIndex：当前语义轴索引，决定使用平移参数还是旋转参数。
  // measurement：该轴本帧观测值 z_k。
  // dtSec：进入状态转移矩阵 A 的采样时间间隔。
  // state：该轴上一帧保留下来的 x/P/Q/R 状态，本函数会原地更新。
  // rotation：区分平移轴和旋转轴，从而选择不同量纲的 P/Q/R/v_th 参数。
  const bool rotation = axisIndex >= 3;
  // minVariance：P/Q/R 的统一下限，保证后续除法和协方差更新数值稳定。
  const double minVariance = config_.kalmanMinVariance;
  // maxVariance：P/Q/R 的统一上限，防止自适应噪声估计无限增大。
  const double maxVariance = config_.kalmanMaxVariance;
  // initialPositionVariance：P00 初始值，对应状态 x=[p,v]^T 中 p 的不确定度。
  const double initialPositionVariance = rotation
      ? config_.kalmanRotationPositionVariance
      : config_.kalmanTranslationPositionVariance;
  // initialVelocityVariance：P11 初始值，对应状态 x=[p,v]^T 中 v 的不确定度。
  const double initialVelocityVariance = rotation
      ? config_.kalmanRotationVelocityVariance
      : config_.kalmanTranslationVelocityVariance;
  // initialMeasurementVariance：R 初始值，对应观测方程 z=Hx+v 的测量噪声方差。
  const double initialMeasurementVariance = rotation
      ? config_.kalmanRotationMeasurementVariance
      : config_.kalmanTranslationMeasurementVariance;
  // initialProcessPositionVariance：Q00 初始值，对应状态转移中位置过程噪声。
  const double initialProcessPositionVariance = rotation
      ? config_.kalmanRotationProcessPositionVariance
      : config_.kalmanTranslationProcessPositionVariance;
  // initialProcessVelocityVariance：Q11 初始值，对应状态转移中速度过程噪声。
  const double initialProcessVelocityVariance = rotation
      ? config_.kalmanRotationProcessVelocityVariance
      : config_.kalmanTranslationProcessVelocityVariance;
  if (!state.initialized) {
    // 初始化：第一帧没有上一时刻状态，直接令 p_0=z_0，v_0=0。
    state.initialized = true;
    // position：状态向量 x 的 p 分量，用第一帧观测 measurement 初始化。
    state.position = measurement;
    // velocity：状态向量 x 的 v 分量，首帧尚无速度估计，置 0。
    state.velocity = 0.0;
    // p00：状态协方差 P 的 (0,0)，代表 p 的初始不确定度。
    state.p00 = initialPositionVariance;
    // p01：状态协方差 P 的 (0,1)，首帧认为 p 与 v 暂无相关性。
    state.p01 = 0.0;
    // p10：状态协方差 P 的 (1,0)，首帧认为 v 与 p 暂无相关性。
    state.p10 = 0.0;
    // p11：状态协方差 P 的 (1,1)，代表 v 的初始不确定度。
    state.p11 = initialVelocityVariance;
    // q00：过程噪声 Q 的 (0,0)，代表位置过程噪声初值。
    state.q00 = initialProcessPositionVariance;
    // q01：过程噪声 Q 的 (0,1)，首帧交叉项未知，初始化为 0。
    state.q01 = 0.0;
    // q10：过程噪声 Q 的 (1,0)，首帧交叉项未知，初始化为 0。
    state.q10 = 0.0;
    // q11：过程噪声 Q 的 (1,1)，代表速度过程噪声初值。
    state.q11 = initialProcessVelocityVariance;
    // r：测量噪声 R 的初始值，来自 UI/默认参数。
    state.r = initialMeasurementVariance;
    return measurement;
  }

  // 1) 状态预测：x_{k|k-1}=A x_{k-1}+B u_k。
  // controlInput：公式中的 u_k；当前 HAL 没有外部加速度/控制输入，因此取 0。
  const double controlInput = 0.0;
  // predictedPosition：预测位置 p_{k|k-1}=p_{k-1}+v_{k-1}dt+0.5dt^2u。
  const double predictedPosition = state.position + state.velocity * dtSec + 0.5 * dtSec * dtSec * controlInput;
  // predictedVelocity：预测速度 v_{k|k-1}=v_{k-1}+dt*u。
  const double predictedVelocity = state.velocity + dtSec * controlInput;
  // 2) 协方差预测：P_{k|k-1}=A P_{k-1} A^T + Q_{k-1}。
  // predictedP00：P_{k|k-1}(0,0)，预测位置方差。
  const double predictedP00 =
      state.p00 + dtSec * (state.p10 + state.p01) + dtSec * dtSec * state.p11 + state.q00;
  // predictedP01：P_{k|k-1}(0,1)，预测位置-速度协方差。
  const double predictedP01 = state.p01 + dtSec * state.p11 + state.q01;
  // predictedP10：P_{k|k-1}(1,0)，预测速度-位置协方差。
  const double predictedP10 = state.p10 + dtSec * state.p11 + state.q10;
  // predictedP11：P_{k|k-1}(1,1)，预测速度方差。
  const double predictedP11 = state.p11 + state.q11;
  // 3) 观测预测：z_{k|k-1}=H x_{k|k-1}；本实现 H=[1,0]，只观测位置。
  const double predictedMeasurement = predictedPosition;
  // gamma：创新序列 gamma_k=z_k-H*x_hat_{k|k-1}。
  double gamma = measurement - predictedMeasurement;
  if (rotation) {
    // 旋转轴使用角度残差归一化，避免 +180/-180 度附近的周期跳变被当成真实大运动。
    gamma = wrapKalmanRotationResidualDeg(gamma);
  }
  // hPredictedPHt：H P_{k|k-1} H^T；H=[1,0] 时就是 predictedP00。
  const double hPredictedPHt = predictedP00;
  // innovationVariance：创新协方差 S=H P H^T + R，用于计算 Kalman 增益。
  const double innovationVariance = std::max(minVariance, hPredictedPHt + state.r);
  // gainPosition：Kalman 增益 K 的位置分量 K_p=P00/S。
  const double gainPosition = predictedP00 / innovationVariance;
  // gainVelocity：Kalman 增益 K 的速度分量 K_v=P10/S。
  const double gainVelocity = predictedP10 / innovationVariance;

  // 4) 状态校正：x_{k|k}=x_{k|k-1}+K_k gamma_k。
  // position：校正后的 p_hat，作为滤波后位置/角度返回。
  state.position = predictedPosition + gainPosition * gamma;
  // velocity：校正后的 v_hat，作为意图速度估计和 w2 计算基础。
  state.velocity = predictedVelocity + gainVelocity * gamma;
  // 5) 协方差校正：P_{k|k}=(I-KH)P_{k|k-1}，按 H=[1,0] 展开。
  // p00：校正后位置方差，夹紧到合法数值范围。
  state.p00 = std::clamp((1.0 - gainPosition) * predictedP00, minVariance, maxVariance);
  // p01：校正后位置-速度协方差。
  state.p01 = (1.0 - gainPosition) * predictedP01;
  // p10：校正后速度-位置协方差。
  state.p10 = predictedP10 - gainVelocity * predictedP00;
  // pCrossSymmetric：理论上 P01=P10；用平均值消除浮点累计造成的微小不对称。
  const double pCrossSymmetric = 0.5 * (state.p01 + state.p10);
  state.p01 = pCrossSymmetric;
  state.p10 = pCrossSymmetric;
  // p11：校正后速度方差，夹紧到合法数值范围。
  state.p11 = std::clamp(predictedP11 - gainVelocity * predictedP01, minVariance, maxVariance);

  // 6) 过程噪声自适应：Q_k=(1-beta)Q_{k-1}+beta(K gamma gamma^T K^T)。
  // q00Adaptive：K_p*gamma*gamma*K_p，对应 Q 的位置-位置项。
  const double q00Adaptive = gainPosition * gamma * gamma * gainPosition;
  // q01Adaptive：K_p*gamma*gamma*K_v，对应 Q 的位置-速度项。
  const double q01Adaptive = gainPosition * gamma * gamma * gainVelocity;
  // q10Adaptive：K_v*gamma*gamma*K_p，对应 Q 的速度-位置项。
  const double q10Adaptive = gainVelocity * gamma * gamma * gainPosition;
  // q11Adaptive：K_v*gamma*gamma*K_v，对应 Q 的速度-速度项。
  const double q11Adaptive = gainVelocity * gamma * gamma * gainVelocity;
  // q00：按遗忘因子 beta 融合上一时刻 Q00 和本帧自适应估计。
  state.q00 = std::clamp(
      (1.0 - config_.kalmanBeta) * state.q00
          + config_.kalmanBeta * q00Adaptive,
      minVariance,
      maxVariance);
  // q01：按遗忘因子 beta 融合上一时刻 Q01 和本帧自适应估计。
  state.q01 = (1.0 - config_.kalmanBeta) * state.q01
      + config_.kalmanBeta * q01Adaptive;
  // q10：按遗忘因子 beta 融合上一时刻 Q10 和本帧自适应估计。
  state.q10 = (1.0 - config_.kalmanBeta) * state.q10
      + config_.kalmanBeta * q10Adaptive;
  // qCrossSymmetric：理论上 Q01=Q10；用平均值保持过程噪声协方差矩阵对称。
  const double qCrossSymmetric = 0.5 * (state.q01 + state.q10);
  state.q01 = qCrossSymmetric;
  state.q10 = qCrossSymmetric;
  // q11：按遗忘因子 beta 融合上一时刻 Q11 和本帧自适应估计。
  state.q11 = std::clamp(
      (1.0 - config_.kalmanBeta) * state.q11
          + config_.kalmanBeta * q11Adaptive,
      minVariance,
      maxVariance);
  // 7) 测量噪声自适应：R_k=(1-beta)R_{k-1}+beta(gamma gamma^T-H P H^T)。
  // rAdaptive：gamma^2-H P_{k|k-1}H^T；一维观测下 gamma gamma^T 即 gamma^2。
  const double rAdaptive = gamma * gamma - hPredictedPHt;
  // r：按遗忘因子 beta 融合上一时刻 R 和本帧自适应估计，并做数值夹紧。
  state.r = std::clamp(
      (1.0 - config_.kalmanBeta) * state.r + config_.kalmanBeta * rAdaptive,
      minVariance,
      maxVariance);
  return state.position;
}

double NativeTeleopController::kalmanIntentWeight(int axisIndex, const KalmanAxisState& state) const {
  // threshold：该轴的意图速度阈值 v_th；平移轴和旋转轴使用不同量纲参数。
  const double threshold = axisIndex >= 3
      ? config_.kalmanRotationIntentVelocityThreshold
      : config_.kalmanTranslationIntentVelocityThreshold;
  // speed：意图估计速度 |v_hat|，由 Kalman 状态中的速度分量得到。
  const double speed = std::abs(state.velocity);
  // 当 |v_hat| >= v_th 时，认为操作者存在明确意图，w2=1。
  if (speed >= threshold) {
    return 1.0;
  }
  // velocitySigma：速度估计标准差 sqrt(P11)，用于衡量 v_hat 的置信度。
  const double velocitySigma = std::sqrt(std::max(config_.kalmanMinVariance, state.p11));
  // velocityConfidence：速度置信度软因子；速度方差越大，置信度越低。
  const double velocityConfidence = threshold / (threshold + velocitySigma);
  // w2：低于阈值时不硬截断，而按速度比例和置信度共同软衰减。
  return std::clamp((speed / threshold) * velocityConfidence, 0.0, 1.0);
}

std::array<double, 6> NativeTeleopController::applyKalmanIntentWeights(
    int sourceIndex,
    std::array<double, 6> deltas) const {
  // sourceIndex：当前主手输入索引，用来读取该手对应的 lastIntentWeight_。
  // deltas：准备下发给从手的六轴增量，返回前按 w2 逐轴缩放。
  // kalmanFilterEnabled：关闭滤波时保持原 deltas，不引入任何 w2 门控。
  if (!config_.kalmanFilterEnabled) {
    return deltas;
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    // lastIntentWeight_[sourceIndex][axisIndex]：该主手该轴最近计算出的 w2。
    deltas[axisIndex] *= lastIntentWeight_[sourceIndex][axisIndex];
  }
  return deltas;
}

void NativeTeleopController::resetKalmanSideUnlocked(int sourceIndex) {
  // sourceIndex：要重置的主手索引，0/1 分别对应左右两路输入。
  if (sourceIndex < 0 || sourceIndex >= static_cast<int>(kalmanStates_.size())) {
    return;
  }
  // kalmanStates_：断连、松开离合或关闭滤波时清空状态，避免旧状态跨段污染。
  kalmanStates_[sourceIndex] = {};
  // lastIntentWeight_：重置后恢复 w2=1，保证下一段首帧不被旧权重衰减。
  lastIntentWeight_[sourceIndex].fill(1.0);
}

bool NativeTeleopController::suppressIncrementalRotationSpikeUnlocked(
    Side sourceSide,
    Side targetSide,
    int sourceIndex,
    int targetIndex,
    const std::array<double, 6>& semanticPose) {
  for (int axisIndex = 3; axisIndex < 6; ++axisIndex) {
    if (!config_.enabledAxes[targetIndex][axisIndex]) {
      continue;
    }
    const double rawDelta = semanticPose[axisIndex] - referencePose_[sourceIndex][axisIndex];
    if (std::abs(rawDelta) <= kIncrementalRotationSpikeGuardDeg) {
      continue;
    }
    referencePose_[sourceIndex] = semanticPose;
    velocityUiPerSec_[sourceIndex] = {};
    incrementalCarry_[sourceIndex] = {};
    incrementalDirection_[sourceIndex] = {};
    incrementalInputActive_[sourceIndex] = false;
    continuousPulseCarry_[sourceIndex] = {};
    continuousDirection_[sourceIndex] = {};
    continuousStreak_[sourceIndex] = {};
    lastRawDelta_[sourceIndex] = {};
    lastFilteredDelta_[sourceIndex] = {};
    lastRequestedPulse_[sourceIndex] = {};
    lastEmittedPulse_[sourceIndex] = {};
    lastOutputDeltaUi_[sourceIndex] = {};
    lastRawDelta_[sourceIndex][axisIndex] = rawDelta;
    if (targetActive_[targetIndex]) {
      motion_.stopTeleopSide(targetSide);
      targetActive_[targetIndex] = false;
      recordZeroStopActionUnlocked(sourceSide, targetSide);
    }
    setBlockerUnlocked(sourceIndex, "blocked", "incremental rotation input spike suppressed; reference recaptured");
    return true;
  }
  return false;
}

std::array<AxisLimit, 6> NativeTeleopController::effectiveSoftLimits(Side targetSide, int targetIndex) const {
  auto limits = config_.softLimits[targetIndex];
  if (config_.homeReferenceValid[targetIndex]) {
    for (int axisIndex = 0; axisIndex < 3; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const double originUi = pulseToUi(config_.homeReferencePulse[targetIndex][axisIndex], targetSide, axis);
      limits[axisIndex].min += originUi;
      limits[axisIndex].max += originUi;
    }
  }
  if (!config_.rotationWorkLimitEnabled) {
    return limits;
  }
  if (!config_.homeReferenceValid[targetIndex]) {
    throw std::runtime_error("home_reference_missing: rotation work limit requires captured hardware zero");
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (axisIndex < 3) {
      continue;
    }
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const double originUi = pulseToUi(config_.homeReferencePulse[targetIndex][axisIndex], targetSide, axis);
    const auto workLimit = config_.rotationWorkLimits[targetIndex][axisIndex];
    limits[axisIndex].min = std::max(limits[axisIndex].min, originUi + workLimit.min);
    limits[axisIndex].max = std::min(limits[axisIndex].max, originUi + workLimit.max);
  }
  return limits;
}

std::array<double, 6> NativeTeleopController::velocityDeltasUi(
    int sourceIndex,
    Side targetSide,
    const std::array<double, 6>& pose,
    double dtSec) {
  std::array<double, 6> deltas{};
  const int targetIndex = sideIndex(targetSide);
  const double tau = std::max(0.0, config_.nativeVelocitySmoothingMs) / 1000.0;
  const double alpha = tau <= 0.0 ? 1.0 : std::clamp(dtSec / (tau + dtSec), 0.0, 1.0);
  lastRawDelta_[sourceIndex] = {};
  lastFilteredDelta_[sourceIndex] = {};
  lastRequestedPulse_[sourceIndex] = {};
  lastEmittedPulse_[sourceIndex] = {};
  lastOutputDeltaUi_[sourceIndex] = {};
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!config_.enabledAxes[targetIndex][axisIndex]) {
      velocityUiPerSec_[sourceIndex][axisIndex] = 0.0;
      continue;
    }
    const bool rotation = axisIndex >= 3;
    const double offset = pose[axisIndex] - referencePose_[sourceIndex][axisIndex];
    const double deadzone = rotation ? config_.nativeRotationDeadzoneDeg : config_.nativeTranslationDeadzoneM;
    const double fullScale = std::max(deadzone + 1e-9, rotation ? config_.nativeRotationFullScaleDeg
                                                                 : config_.nativeTranslationFullScaleM);
    lastRawDelta_[sourceIndex][axisIndex] = offset;
    double targetVelocity = 0.0;
    if (std::abs(offset) > deadzone) {
      const double normalized = std::clamp((std::abs(offset) - deadzone) / (fullScale - deadzone), 0.0, 1.0);
      const double maxVelocity = rotation ? config_.rotationMaxVelocityDegS : config_.translationMaxVelocityUmS;
      const double direction = (offset >= 0.0 ? 1.0 : -1.0) * mappedDirection(sourceIndex, targetSide, axisIndex);
      targetVelocity = direction * normalized * maxVelocity * config_.axisOutputScale[targetIndex][axisIndex];
      lastFilteredDelta_[sourceIndex][axisIndex] = offset;
    }
    auto& velocity = velocityUiPerSec_[sourceIndex][axisIndex];
    velocity += (targetVelocity - velocity) * alpha;
    if (std::abs(velocity) < (rotation ? 0.001 : 0.01)) {
      velocity = 0.0;
    }
    deltas[axisIndex] = velocity * dtSec;
    lastOutputDeltaUi_[sourceIndex][axisIndex] = deltas[axisIndex];
  }
  return deltas;
}

std::array<double, 6> NativeTeleopController::incrementalDeltasUi(
    int sourceIndex,
    Side targetSide,
    const std::array<double, 6>& pose) {
  std::array<double, 6> deltas{};
  incrementalInputActive_[sourceIndex] = false;
  const int targetIndex = sideIndex(targetSide);
  lastRawDelta_[sourceIndex] = {};
  lastFilteredDelta_[sourceIndex] = {};
  lastRequestedPulse_[sourceIndex] = {};
  lastEmittedPulse_[sourceIndex] = {};
  lastOutputDeltaUi_[sourceIndex] = {};
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!config_.enabledAxes[targetIndex][axisIndex]) {
      continuousPulseCarry_[sourceIndex][axisIndex] = 0.0;
      continuousDirection_[sourceIndex][axisIndex] = 0;
      continuousStreak_[sourceIndex][axisIndex] = 0;
      continue;
    }
    const bool rotation = axisIndex >= 3;
    const double rawDelta = pose[axisIndex] - referencePose_[sourceIndex][axisIndex];
    lastRawDelta_[sourceIndex][axisIndex] = rawDelta;
    const double inputThreshold = rotation ? config_.rotationInputEpsilonDeg : config_.translationInputEpsilonM;
    const bool aboveInputThreshold = std::abs(rawDelta) >= inputThreshold;
    if (aboveInputThreshold) {
      incrementalInputActive_[sourceIndex] = true;
    }
    double filteredDelta = 0.0;
    if (config_.continuousIncrementMode) {
      filteredDelta = aboveInputThreshold ? rawDelta : 0.0;
    } else if (rotation) {
      filteredDelta = applyDeadzone(rawDelta, config_.rotationDeadzoneDeg);
    } else {
      const double baseFiltered = applyDeadzone(rawDelta, config_.translationDeadzoneM);
      if (std::abs(baseFiltered) >= 1e-12) {
        auto& carry = incrementalCarry_[sourceIndex][axisIndex];
        auto& direction = incrementalDirection_[sourceIndex][axisIndex];
        carry += baseFiltered;
        const int carrySign = signOfValue(carry);
        if (carrySign != 0) {
          const bool reversing = direction != 0 && carrySign != direction;
          const double requiredMagnitude = reversing
              ? config_.incrementalTranslationReverseDeadzoneM
              : config_.incrementalTranslationMinEffectiveDeltaM;
          if (std::abs(carry) >= requiredMagnitude) {
            filteredDelta = carry;
            carry = 0.0;
            direction = carrySign;
          }
        } else {
          carry = 0.0;
        }
      }
    }
    lastFilteredDelta_[sourceIndex][axisIndex] = filteredDelta;
    const double scale = (rotation ? config_.rotationScale[sourceIndex] : config_.translationScale[sourceIndex])
        * config_.axisOutputScale[targetIndex][axisIndex];
    const double impulsePulse = filteredDelta * config_.impulseCoeff[targetIndex][axisIndex];
    const double requestedPulseFloat = impulsePulse * scale;
    lastRequestedPulse_[sourceIndex][axisIndex] = requestedPulseFloat;
    double requestedPulse = requestedPulseFloat;
    if (config_.continuousIncrementMode) {
      auto& pulseCarry = continuousPulseCarry_[sourceIndex][axisIndex];
      if (std::abs(requestedPulseFloat) > 1e-12) {
        pulseCarry += requestedPulseFloat;
        requestedPulse = static_cast<double>(applyContinuousPulseGate(
            sourceIndex,
            axisIndex,
            static_cast<long>(std::llround(pulseCarry)),
            pulseCarry));
        if (std::abs(requestedPulse) > 1e-12) {
          const int emittedSign = signOfValue(requestedPulse);
          pulseCarry -= requestedPulse;
          const int carrySign = signOfValue(pulseCarry);
          if (carrySign != 0 && carrySign != emittedSign) {
            pulseCarry = 0.0;
          } else if (std::abs(pulseCarry) < 1e-9) {
            pulseCarry = 0.0;
          }
        }
      } else {
        pulseCarry = 0.0;
        continuousDirection_[sourceIndex][axisIndex] = 0;
        continuousStreak_[sourceIndex][axisIndex] = 0;
        requestedPulse = 0.0;
      }
    }
    lastEmittedPulse_[sourceIndex][axisIndex] = requestedPulse;
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const double unitPulse = pulsePerUnit(targetSide, axis);
    const double physical = requestedPulse / unitPulse;
    deltas[axisIndex] = rotation ? physical : physical * 1000.0;
    lastOutputDeltaUi_[sourceIndex][axisIndex] = deltas[axisIndex];
  }
  return deltas;
}

long NativeTeleopController::applyContinuousPulseGate(
    int sourceIndex,
    int axisIndex,
    long requestedPulse,
    double requestedPulseFloat) {
  if (std::abs(requestedPulseFloat) <= 1e-12) {
    continuousDirection_[sourceIndex][axisIndex] = 0;
    continuousStreak_[sourceIndex][axisIndex] = 0;
    return 0;
  }
  const int sign = requestedPulseFloat > 0.0 ? 1 : -1;
  const bool rotation = axisIndex >= 3;
  const double deadband = rotation ? config_.rotationPulseDeadband : config_.translationPulseDeadband;
  const double minActive = rotation ? config_.rotationMinActivePulse : config_.translationMinActivePulse;
  const long minimum = std::max<long>({
      1,
      static_cast<long>(std::ceil(minActive)),
      static_cast<long>(std::floor(std::max(0.0, deadband))) + 1,
  });
  auto& direction = continuousDirection_[sourceIndex][axisIndex];
  auto& streak = continuousStreak_[sourceIndex][axisIndex];
  if (std::abs(requestedPulse) >= minimum) {
    direction = sign;
    streak = config_.continuousMicroConfirmTicks;
    return requestedPulse;
  }
  if (config_.continuousMicroConfirmTicks <= 0) {
    return 0;
  }
  if (direction == sign) {
    ++streak;
  } else {
    direction = sign;
    streak = 1;
  }
  if (streak < config_.continuousMicroConfirmTicks) {
    return 0;
  }
  return sign * minimum;
}

void NativeTeleopController::tickGrippers(const std::array<Omega7State, 2>& hands) {
  std::scoped_lock lock(mutex_);
  if (!config_.gripperTeleopEnabled) {
    return;
  }
  const auto now = std::chrono::steady_clock::now();
  for (int targetIndex = 0; targetIndex < 2; ++targetIndex) {
    const int sourceIndex = gripperSourceIndex(targetIndex);
    const auto& hand = hands[sourceIndex];
    if (!hand.connected || !hand.lastReadOk) {
      gripperLastRaw_[targetIndex] = -1;
      continue;
    }
    double targetMm = gripperTargetsMm_[targetIndex];
    if (hand.gripperGapAvailable) {
      const double gapMm = hand.gripperGap * 1000.0;
      const double minGap = config_.gripperGapMinMm[targetIndex];
      const double maxGap = std::max(minGap + 1e-6, config_.gripperGapMaxMm[targetIndex]);
      double openRatio = std::clamp((gapMm - minGap) / (maxGap - minGap), 0.0, 1.0);
      if (config_.gripperGapInvert[targetIndex]) {
        openRatio = 1.0 - openRatio;
      }
      targetMm = openRatio * std::max(0.001, config_.gripper.strokeMm);
    } else if (config_.gripperButtonFallback) {
      targetMm = hand.gripperPressed ? 0.0 : std::max(0.001, config_.gripper.strokeMm);
    } else {
      continue;
    }
    targetMm = effectiveGripperTargetMm(targetMm);
    const int raw = static_cast<int>(std::lround(
        (std::max(0.001, config_.gripper.strokeMm) - std::clamp(targetMm, 0.0, config_.gripper.strokeMm))
        / std::max(0.001, config_.gripper.strokeMm) * 255.0));
    const auto elapsedMs = std::chrono::duration<double, std::milli>(now - gripperLastCommandAt_[targetIndex]).count();
    if (gripperLastRaw_[targetIndex] >= 0 && elapsedMs < config_.gripperMinCommandIntervalMs) {
      continue;
    }
    if (gripperLastRaw_[targetIndex] >= 0
        && std::abs(raw - gripperLastRaw_[targetIndex]) < config_.gripperDeadbandCounts) {
      continue;
    }
    const Side targetSide = sideFromIndex(targetIndex);
    enqueueGripperCommand(targetIndex, targetSide, targetMm, config_.gripper.speed, config_.gripper.torque);
    gripperLastRaw_[targetIndex] = raw;
    gripperLastCommandAt_[targetIndex] = now;
    gripperTargetsMm_[targetIndex] = std::clamp(targetMm, 0.0, config_.gripper.strokeMm);
  }
}

void NativeTeleopController::enqueueGripperCommand(
    int targetIndex,
    Side side,
    double targetMm,
    int speed,
    int torque) {
  if (!gripperWorkerRunning_.load()) {
    return;
  }
  {
    std::scoped_lock lock(gripperMutex_);
    pendingGripperCommands_[targetIndex] = PendingGripperCommand{true, targetIndex, side, targetMm, speed, torque};
  }
  gripperCv_.notify_one();
}

double NativeTeleopController::mappedDirection(int sourceIndex, Side targetSide, int axisIndex) const {
  (void)sourceIndex;
  const int targetIndex = sideIndex(targetSide);
  const bool rotation = axisIndex >= 3;
  const double sourceUnit = rotation ? 1.0 : 1e-6;
  const double pulse = sourceUnit * config_.impulseCoeff[targetIndex][axisIndex];
  const auto axis = static_cast<SemanticAxis>(axisIndex);
  const double ui = rotation ? pulse / pulsePerUnit(targetSide, axis) : pulse / pulsePerUnit(targetSide, axis) * 1000.0;
  return ui >= 0.0 ? 1.0 : -1.0;
}

void NativeTeleopController::setBlockerUnlocked(
    int sourceIndex,
    const std::string& state,
    const std::string& message) {
  blockerState_[sourceIndex] = state;
  blockerMessage_[sourceIndex] = message;
}

void NativeTeleopController::recordActionUnlocked(
    Side sourceSide,
    Side targetSide,
    const TeleopTargetUpdateResult& result) {
  const bool appliedMotion = hasMotion(result.appliedDeltaUi);
  if (!appliedMotion && hasLastAction_ && !hasMotion(lastAction_.deltas)) {
    return;
  }
  int dominant = 0;
  for (int i = 1; i < 6; ++i) {
    if (std::abs(result.appliedDeltaUi[i]) > std::abs(result.appliedDeltaUi[dominant])) {
      dominant = i;
    }
  }
  NativeTeleopAction action;
  action.ts = unixTimeMs();
  action.monotonicS = monotonicSeconds();
  action.side = targetSide;
  action.sourceSide = sourceSide;
  action.axisIndex = dominant;
  action.delta = result.appliedDeltaUi[dominant];
  action.deltas = result.appliedDeltaUi;
  action.requestedDeltaPulse = result.requestedDeltaPulse;
  action.appliedDeltaPulse = result.appliedDeltaPulse;
  action.targetPulse = result.targetPulse;
  action.currentPulse = result.currentPulse;
  action.launchDeltaPulse = result.launchDeltaPulse;
  action.updateReturn = result.updateReturn;
  action.stopReason = result.stopReason;
  action.axisIoStatus = result.axisIoStatus;
  action.movingBefore = result.movingBefore;
  action.moveStarted = result.moveStarted;
  action.clipped = result.clipped;
  const int offset = sideIndex(targetSide) * 6;
  for (int i = 0; i < 6; ++i) {
    action.deltaVector[offset + i] = result.appliedDeltaUi[i];
  }
  lastAction_ = action;
  hasLastAction_ = true;
  actionHistory_.push_back(action);
  while (actionHistory_.size() > 1000) {
    actionHistory_.pop_front();
  }
}

void NativeTeleopController::recordZeroStopActionUnlocked(Side sourceSide, Side targetSide) {
  if (hasLastAction_ && !hasMotion(lastAction_.deltas)) {
    return;
  }
  NativeTeleopAction action;
  action.ts = unixTimeMs();
  action.monotonicS = monotonicSeconds();
  action.side = targetSide;
  action.sourceSide = sourceSide;
  action.axisIndex = 0;
  action.delta = 0.0;
  lastAction_ = action;
  hasLastAction_ = true;
  actionHistory_.push_back(action);
  while (actionHistory_.size() > 1000) {
    actionHistory_.pop_front();
  }
}

int NativeTeleopController::gripperSourceIndex(int targetIndex) const {
  std::string source = config_.gripperSourceHand[targetIndex];
  std::transform(source.begin(), source.end(), source.begin(), [](unsigned char ch) {
    return static_cast<char>(std::tolower(ch));
  });
  if (source.find("right") != std::string::npos) {
    return 1;
  }
  if (source.find("left") != std::string::npos) {
    return 0;
  }
  return targetIndex;
}

double NativeTeleopController::effectiveGripperTargetMm(double targetMm) const {
  const double stroke = std::max(0.001, config_.gripper.strokeMm);
  const double bounded = std::clamp(targetMm, 0.0, stroke);
  if (!config_.gripperIcfTargetProtectionEnabled) {
    return bounded;
  }
  const double minGap = std::clamp(config_.gripperIcfTargetMinGapMm, 0.0, stroke);
  return std::clamp(bounded, minGap, stroke);
}

Side NativeTeleopController::sideFromIndex(int index) const {
  return index == 0 ? Side::Left : Side::Right;
}

int NativeTeleopController::sideIndex(Side side) const {
  return side == Side::Left ? 0 : 1;
}

}  // namespace appstation::hal
