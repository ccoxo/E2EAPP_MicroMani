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
        AxisLimit{-90.0, 90.0},
        AxisLimit{-90.0, 90.0},
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
  {
    std::scoped_lock lock(mutex_);
    config_ = normalized;
  }
  gripper_.configure(normalized.gripper);
  omega_.setGravityCompensation(normalized.leftGravityCompensation, normalized.rightGravityCompensation);
}

void NativeTeleopController::start(bool leftConnected, bool rightConnected) {
  {
    std::scoped_lock lock(mutex_);
    logicalConnected_ = {leftConnected, rightConnected};
    referenceValid_ = {false, false};
    targetActive_ = {false, false};
    velocityUiPerSec_ = {};
    incrementalCarry_ = {};
    incrementalDirection_ = {};
    incrementalInputActive_ = {false, false};
    continuousPulseCarry_ = {};
    continuousDirection_ = {};
    continuousStreak_ = {};
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
  startGripperWorker();
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
  referenceValid_ = {false, false};
  targetActive_ = {false, false};
  velocityUiPerSec_ = {};
    incrementalCarry_ = {};
    incrementalDirection_ = {};
    incrementalInputActive_ = {false, false};
    continuousPulseCarry_ = {};
    continuousDirection_ = {};
    continuousStreak_ = {};
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
  while (gripperWorkerRunning_.load()) {
    std::array<PendingGripperCommand, 2> commands{};
    {
      std::unique_lock lock(gripperMutex_);
      gripperCv_.wait(lock, [&] {
        return !gripperWorkerRunning_.load()
            || pendingGripperCommands_[0].pending
            || pendingGripperCommands_[1].pending;
      });
      if (!gripperWorkerRunning_.load()) {
        break;
      }
      commands = pendingGripperCommands_;
      pendingGripperCommands_ = {};
    }
    for (const auto& command : commands) {
      if (!command.pending) {
        continue;
      }
      std::string message;
      const bool ok = gripper_.commandTarget(command.side, command.targetMm, command.speed, command.torque, &message);
      {
        std::scoped_lock lock(mutex_);
        gripperLastCommandOk_[command.targetIndex] = ok;
        gripperLastMessage_[command.targetIndex] = message;
        gripperLastCommandTs_[command.targetIndex] = unixTimeMs();
        if (!ok) {
          lastError_ = std::string("native gripper ") + sideName(command.side) + ": " + message;
        }
      }
    }
  }
}

bool NativeTeleopController::running() const {
  return running_.load();
}

std::string NativeTeleopController::statusJson() const {
  const auto forceOutput = omega_.forceOutputEnabled();
  const auto gripperPositions = gripper_.positionMm();
  std::scoped_lock lock(mutex_);
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
      << ",\"deadbandCounts\":" << config_.gripperDeadbandCounts
      << ",\"minCommandIntervalMs\":" << config_.gripperMinCommandIntervalMs << "}}";
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
  const auto semanticPose = omegaPoseToSemantic(hand.pose, config_.mappingMode);
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

  const auto deltas = config_.controlMode == kIncrementalPositionMode
      ? incrementalDeltasUi(sourceIndex, targetSide, semanticPose)
      : velocityDeltasUi(sourceIndex, targetSide, semanticPose, dtSec);
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

std::array<AxisLimit, 6> NativeTeleopController::effectiveSoftLimits(Side targetSide, int targetIndex) const {
  auto limits = config_.softLimits[targetIndex];
  if (!config_.workOriginValid[targetIndex]) {
    return limits;
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const double originUi = pulseToUi(config_.workOriginPulse[targetIndex][axisIndex], targetSide, axis);
    limits[axisIndex].min = originUi + config_.softLimits[targetIndex][axisIndex].min;
    limits[axisIndex].max = originUi + config_.softLimits[targetIndex][axisIndex].max;
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
    const double impulsePulse = filteredDelta * config_.impulseCoeff[sourceIndex][axisIndex];
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
    const int raw = static_cast<int>(std::lround(
        (std::max(0.001, config_.gripper.strokeMm) - std::clamp(targetMm, 0.0, config_.gripper.strokeMm))
        / std::max(0.001, config_.gripper.strokeMm) * 255.0));
    const auto elapsedMs = std::chrono::duration<double, std::milli>(now - gripperLastCommandAt_[targetIndex]).count();
    if (gripperLastRaw_[targetIndex] >= 0
        && std::abs(raw - gripperLastRaw_[targetIndex]) < config_.gripperDeadbandCounts
        && elapsedMs < config_.gripperMinCommandIntervalMs) {
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
  const bool rotation = axisIndex >= 3;
  const double sourceUnit = rotation ? 1.0 : 1e-6;
  const double pulse = sourceUnit * config_.impulseCoeff[sourceIndex][axisIndex];
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

Side NativeTeleopController::sideFromIndex(int index) const {
  return index == 0 ? Side::Left : Side::Right;
}

int NativeTeleopController::sideIndex(Side side) const {
  return side == Side::Left ? 0 : 1;
}

}  // namespace appstation::hal
