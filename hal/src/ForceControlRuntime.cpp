/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：连接 HKVL 采样、安全锁存和柔顺控制，管理监控线程与急停/确认回调。
 * 先看：ForceControlRuntime::configure → ForceControlRuntime::config → ForceControlRuntime::start → ForceControlRuntime::stop。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "ForceControlRuntime.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace appstation::hal {
namespace {

const std::array<const char*, 6> kForceChannels{{
    "Fx", "Fy", "Fz", "Mx", "My", "Mz",
}};

std::string lowercase(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
    return static_cast<char>(std::tolower(ch));
  });
  return value;
}

std::string escapeJson(const std::string& value) {
  std::ostringstream out;
  for (const char ch : value) {
    if (ch == '"' || ch == '\\') {
      out << '\\';
    }
    if (ch == '\n') {
      out << "\\n";
    } else if (ch != '\r') {
      out << ch;
    }
  }
  return out.str();
}

template <std::size_t Size>
void appendArray(
    std::ostringstream& out,
    const std::array<double, Size>& values) {
  out << "[";
  for (std::size_t i = 0; i < values.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << values[i];
  }
  out << "]";
}

}  // namespace

double forceMonotonicMilliseconds() {
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration<double, std::milli>(now).count();
}

ForceControlRuntime::ForceControlRuntime(
    EmergencyStopCallback emergencyStop,
    AcknowledgeCallback acknowledge,
    HkvlForceDriver::ReadLoop readLoop,
    std::function<void()> monitorTick,
    WorkerLauncher launcher)
    : emergencyStop_(std::move(emergencyStop)),
      acknowledge_(std::move(acknowledge)),
      monitorTick_(std::move(monitorTick)), launcher_(launcher),
      driver_(std::move(readLoop), std::move(launcher)) {}

ForceControlRuntime::~ForceControlRuntime() {
  stop();
}

void ForceControlRuntime::configure(
    const ForceRuntimeConfig& config,
    double nowMonotonicMs) {
  std::scoped_lock lifecycleLock(lifecycleMutex_);
  validateConfig(config);
  const bool restart = running();
  std::optional<ForceSafetyTrip> pendingTrip;
  stop();
  {
    std::scoped_lock lock(mutex_);
    config_ = config;
    emergencyCallbackFailed_.store(false);
    emergencyCallbackError_.clear();
    workerFailed_.store(false);
    workerError_.clear();
    ++tareEpoch_;
    tareInProgress_ = false;
    calibration_ = {};
    if (config.source == "hkvl_serial") {
      calibration_.state = "waiting_sensors";
      calibration_.reason = "operator must confirm both sensors are unloaded";
    }
    safety_.configure(config.safety, nowMonotonicMs);
    if (config.source == "hkvl_serial") {
      pendingTrip = safety_.latchExternal(
          "force_configuration_pending",
          nowMonotonicMs);
    }
    compliance_.configure(config.compliance);
    latestTared_ = {};
    latestFiltered_ = {};
    latestMonotonicMs_ = {};
    latestUnixMs_ = {};
    hasSample_ = {false, false};
    lastCompliance_ = {};
    lastComplianceActualUm_ = {};
  }
  invokeEmergencyStopIfNeeded(pendingTrip);
  if (restart) {
    start();
  }
}

ForceRuntimeConfig ForceControlRuntime::config() const {
  std::scoped_lock lock(mutex_);
  return config_;
}

void ForceControlRuntime::start() {
  std::scoped_lock lifecycleLock(lifecycleMutex_);
  if (workerFailed_.load()) throw std::runtime_error("force worker failure requires reconfiguration before restart");
  if (!running() && monitor_.joinable()) monitor_.join();
  if (running_.exchange(true, std::memory_order_acq_rel)) {
    return;
  }
  ForceRuntimeConfig current;
  {
    std::scoped_lock lock(mutex_);
    current = config_;
  }
  if (current.source != "hkvl_serial") {
    return;
  }
  try {
    driver_.start(current.serial, [this](const HkvlDriverSample& sample) {
      acceptSample(sample.side, sample.tared, sample.filtered, sample.monotonicMs, sample.unixMs);
    }, [this](int, const char* error) { reportWorkerFailure(error); });
    if (workerFailed_.load()) throw std::runtime_error("force serial reader failed during startup");
    monitor_ = launchWorker(launcher_, [this]() {
      runWorkerBoundary([this]() { monitorLoop(); }, [this](const char* error) { reportWorkerFailure(error); });
    });
  } catch (...) {
    reportWorkerFailure("force worker startup failed");
    stop();
    throw;
  }
}

void ForceControlRuntime::stop() {
  std::scoped_lock lifecycleLock(lifecycleMutex_);
  const bool wasRunning = running_.exchange(false, std::memory_order_acq_rel);
  std::optional<ForceSafetyTrip> pendingTrip;
  {
    std::scoped_lock lock(mutex_);
    ++tareEpoch_;
    if (config_.source == "hkvl_serial") {
      if (wasRunning) {
        pendingTrip = safety_.latchExternal("force_acquisition_stopped", forceMonotonicMilliseconds());
      }
      calibration_ = {};
      calibration_.state = "waiting_sensors";
      calibration_.reason = "force acquisition stopped; repeat startup force self-check";
      tareInProgress_ = false;
    }
  }
  invokeEmergencyStopIfNeeded(pendingTrip);
  driver_.stop();
  if (monitor_.joinable()) {
    monitor_.join();
  }
  resetCompliance();
}

bool ForceControlRuntime::running() const {
  return running_.load(std::memory_order_acquire);
}

bool ForceControlRuntime::usesHkvl() const {
  std::scoped_lock lock(mutex_);
  return config_.source == "hkvl_serial";
}

void ForceControlRuntime::acceptSample(
    int side,
    const std::array<double, 6>& tared,
    const std::array<double, 6>& filtered,
    double monotonicMs,
    std::int64_t unixMs) {
  if (side < 0 || side >= 2) {
    throw std::out_of_range("force sample side must be 0 or 1");
  }
  std::optional<ForceSafetyTrip> trip;
  {
    std::scoped_lock lock(mutex_);
    if (config_.source != "hkvl_serial") {
      return;
    }
    std::array<double, 6> alignedTared{};
    std::array<double, 6> alignedFiltered{};
    for (std::size_t axis = 0; axis < alignedTared.size(); ++axis) {
      alignedTared[axis] = tared[axis] * config_.axisSign[side][axis];
      alignedFiltered[axis] = filtered[axis] * config_.axisSign[side][axis];
    }
    latestTared_[side] = alignedTared;
    latestFiltered_[side] = alignedFiltered;
    latestMonotonicMs_[side] = monotonicMs;
    latestUnixMs_[side] = unixMs;
    hasSample_[side] = true;
    // 安全阈值使用方向校准后的去皮值，柔顺计算另用滤波值，避免低通滤波延迟削弱超限检测。
    trip = safety_.onSample(side, alignedTared, monotonicMs);
    if (tareInProgress_ && calibration_.epoch == tareEpoch_) {
      const auto tareTrip = tareSafety_.onSample(side, alignedTared, monotonicMs);
      if (tareTrip.has_value()) trip = tareTrip;
    }
    if (trip.has_value()) {
      ++tareEpoch_;
      compliance_.reset();
      lastCompliance_ = {};
      lastComplianceActualUm_ = {};
    }
  }
  invokeEmergencyStopIfNeeded(trip);
}

void ForceControlRuntime::checkSafety(double nowMonotonicMs) {
  std::optional<ForceSafetyTrip> trip;
  {
    std::scoped_lock lock(mutex_);
    if (config_.source != "hkvl_serial") {
      return;
    }
    trip = safety_.checkWatchdog(nowMonotonicMs);
    if (tareInProgress_ && calibration_.epoch == tareEpoch_) {
      const auto tareTrip = tareSafety_.checkWatchdog(nowMonotonicMs);
      if (tareTrip.has_value()) trip = tareTrip;
    }
    if (trip.has_value()) {
      ++tareEpoch_;
      compliance_.reset();
      lastCompliance_ = {};
      lastComplianceActualUm_ = {};
    }
  }
  invokeEmergencyStopIfNeeded(trip);
}

void ForceControlRuntime::recordExternalEmergencyStop(
    const std::string& reason,
    double nowMonotonicMs) {
  std::optional<ForceSafetyTrip> trip;
  {
    std::scoped_lock lock(mutex_);
    ++tareEpoch_;
    if (config_.source == "hkvl_serial") {
      trip = safety_.latchExternal(reason, nowMonotonicMs);
    }
    compliance_.reset();
    lastCompliance_ = {};
    lastComplianceActualUm_ = {};
  }
  (void)trip;
}

void ForceControlRuntime::acknowledgeEmergencyStop(
    double nowMonotonicMs,
    AcknowledgeCallback acknowledge) {
  std::scoped_lock lock(mutex_);
  if (emergencyCallbackFailed_.load()) {
    throw std::runtime_error("force emergency-stop callback failed; diagnose and reconfigure before acknowledgement");
  }
  if (workerFailed_.load()) throw std::runtime_error("force worker failed; reconfigure before acknowledgement");
  if (config_.source == "hkvl_serial") {
    if (calibration_.state != "ready_for_ack" && calibration_.state != "ready") {
      throw std::runtime_error("startup force self-check is not complete");
    }
    std::string blocker;
    if (!safety_.canAcknowledge(nowMonotonicMs, &blocker)) {
      throw std::runtime_error(blocker);
    }
  }
  // 先确认同一运动急停代际；若其已更新，力锁存也必须保留。
  const auto& callback = acknowledge ? acknowledge : acknowledge_;
  if (callback) callback();
  if (config_.source == "hkvl_serial") {
    safety_.acknowledge(nowMonotonicMs);
    calibration_.state = "ready";
  }
  compliance_.reset();
  lastCompliance_ = {};
  lastComplianceActualUm_ = {};
  // 上述运动确认和力锁存确认共用此锁，新力事件只能在二者完成后进入。
}

bool ForceControlRuntime::safetyLatched() const {
  std::scoped_lock lock(mutex_);
  return workerFailed_.load() || safety_.latched();
}

std::string ForceControlRuntime::tare(int side, int sampleCount, std::function<bool()> commandAllowed) {
  std::unique_lock operationLock(tareMutex_, std::try_to_lock);
  if (!operationLock.owns_lock()) throw std::runtime_error("force self-check is already running");
  if (side != -1) throw std::runtime_error("HKVL startup force self-check requires side=all");
  if (sampleCount < kHkvlTareMinSamples || sampleCount > kHkvlTareMaxSamples) {
    throw std::runtime_error("invalid HKVL tare sample count; expected 200..1000");
  }
  std::uint64_t epoch;
  std::optional<ForceSafetyTrip> pendingTrip;
  {
    std::scoped_lock lock(mutex_);
    if (config_.source != "hkvl_serial") {
      throw std::runtime_error("force.tare is only available for hkvl_serial");
    }
    if (workerFailed_.load() || emergencyCallbackFailed_.load()) {
      throw std::runtime_error("force worker or emergency-stop callback failed; reconfigure before self-check");
    }
    if (commandAllowed && !commandAllowed()) throw std::runtime_error("HKVL tare cancelled by emergency stop");
    epoch = ++tareEpoch_;
    pendingTrip = safety_.latchExternal("force_tare_pending", forceMonotonicMilliseconds());
    compliance_.reset();
    lastCompliance_ = {};
    lastComplianceActualUm_ = {};
    calibration_ = {};
    calibration_.epoch = epoch;
    calibration_.state = "checking_stability";
    calibration_.progress = 5;
    tareSafety_.configure(config_.safety, forceMonotonicMilliseconds());
    for (int index = 0; index < 2; ++index) {
      if (hasSample_[index]) {
        tareSafety_.onSample(index, latestTared_[index], latestMonotonicMs_[index]);
      }
    }
    tareInProgress_ = true;
  }
  invokeEmergencyStopIfNeeded(pendingTrip);
  const auto allowed = [&]() {
    return tareEpoch_ == epoch && !tareSafety_.latched()
        && !workerFailed_.load() && !emergencyCallbackFailed_.load()
        && (!commandAllowed || commandAllowed());
  };
  try {
    const auto result = driver_.tare(side, sampleCount, std::chrono::milliseconds(kHkvlTareWindowTimeoutMs),
        [&](const std::function<void()>& commit) {
          std::scoped_lock lock(mutex_);
          if (!allowed()) return false;
          commit();
          return true;
        },
        [&](const std::string& state, int progress) {
          std::scoped_lock lock(mutex_);
          if (!allowed()) throw std::runtime_error("HKVL tare cancelled by emergency stop");
          calibration_.state = state;
          calibration_.progress = progress;
        });
    std::scoped_lock lock(mutex_);
    if (!allowed()) throw std::runtime_error("HKVL tare cancelled by emergency stop");
    tareInProgress_ = false;
    // 提交后重新等待双侧新样本和稳定窗口，再允许操作者确认恢复。
    safety_.markDisconnected(0, forceMonotonicMilliseconds());
    safety_.markDisconnected(1, forceMonotonicMilliseconds());
    calibration_.state = "ready_for_ack";
    calibration_.progress = 100;
    calibration_.hasResult = true;
    calibration_.result = result;
    std::ostringstream out;
    out << "{\"ok\":true,\"calibration\":";
    appendCalibrationJson(out);
    out << "}";
    return out.str();
  } catch (...) {
    std::string reason = "unknown force self-check failure";
    try { throw; }
    catch (const std::exception& error) { reason = error.what(); }
    catch (...) {}
    std::scoped_lock lock(mutex_);
    if (calibration_.epoch == epoch) {
      tareInProgress_ = false;
      calibration_.state = "failed";
      calibration_.progress = 0;
      calibration_.reason = reason;
      calibration_.hasResult = false;
    }
    throw;
  }
}

ForceComplianceResult ForceControlRuntime::complianceCorrection(
    int side,
    std::uint64_t targetMonotonicMs) {
  std::scoped_lock lock(mutex_);
  const bool fresh = hasSample_[side]
      && static_cast<double>(targetMonotonicMs) - latestMonotonicMs_[side]
          <= config_.safety.watchdogMs;
  lastCompliance_[side] = compliance_.correction(
      side,
      latestFiltered_[side],
      fresh,
      safety_.latched(),
      targetMonotonicMs);
  return lastCompliance_[side];
}

void ForceControlRuntime::commitCompliance(
    int side,
    const std::array<double, 2>& requestedUm,
    const std::array<double, 2>& actualUm) {
  std::scoped_lock lock(mutex_);
  compliance_.commit(side, requestedUm, actualUm);
  for (std::size_t axis = 0; axis < 2; ++axis) {
    const double lower = std::min(0.0, requestedUm[axis]);
    const double upper = std::max(0.0, requestedUm[axis]);
    lastComplianceActualUm_[side][axis] =
        std::clamp(actualUm[axis], lower, upper);
    if (lastComplianceActualUm_[side][axis] != requestedUm[axis]
        && lastCompliance_[side].clipReason[axis].empty()) {
      lastCompliance_[side].clipReason[axis] = "motion_limit";
    }
  }
}

void ForceControlRuntime::resetCompliance() {
  std::scoped_lock lock(mutex_);
  compliance_.reset();
  lastCompliance_ = {};
  lastComplianceActualUm_ = {};
}

std::string ForceControlRuntime::forceStateJson(
    double nowMonotonicMs) {
  const auto driverSnapshot = driver_.snapshot(nowMonotonicMs);
  std::scoped_lock lock(mutex_);

  std::ostringstream out;
  out << std::setprecision(10);
  out << "{\"source\":\"" << escapeJson(config_.source) << "\""
      << ",\"protocol\":\"" << escapeJson(config_.serial.protocol) << "\""
      << ",\"left\":";
  appendArray(out, latestFiltered_[0]);
  out << ",\"right\":";
  appendArray(out, latestFiltered_[1]);
  out << ",\"rawLeft\":";
  appendArray(out, latestTared_[0]);
  out << ",\"rawRight\":";
  appendArray(out, latestTared_[1]);
  out << ",\"sensorRawLeft\":";
  appendArray(out, driverSnapshot.sides[0].raw);
  out << ",\"sensorRawRight\":";
  appendArray(out, driverSnapshot.sides[1].raw);
  out << ",\"dangerIndex\":" << safety_.dangerIndex();
  out << ",\"calibration\":";
  appendCalibrationJson(out);
  const double skewMs = hasSample_[0] && hasSample_[1]
      ? std::abs(latestMonotonicMs_[0] - latestMonotonicMs_[1])
      : 0.0;
  out << ",\"leftRightSkewMs\":" << skewMs << ",\"sides\":{";

  for (int side = 0; side < 2; ++side) {
    if (side > 0) {
      out << ",";
    }
    const auto& metrics = driverSnapshot.sides[side];
    const double ageMs = hasSample_[side]
        ? std::max(0.0, nowMonotonicMs - latestMonotonicMs_[side])
        : 0.0;
    const bool connected = metrics.connected;
    const bool healthy =
        connected && hasSample_[side] && ageMs <= config_.safety.watchdogMs;
    auto alignedTareBias = metrics.tareBias;
    for (std::size_t axis = 0; axis < alignedTareBias.size(); ++axis) {
      alignedTareBias[axis] *= config_.axisSign[side][axis];
    }
    out << "\"" << (side == 0 ? "left" : "right") << "\":{"
        << "\"port\":\""
        << escapeJson(side == 0 ? config_.serial.leftPort : config_.serial.rightPort)
        << "\",\"connected\":" << (connected ? "true" : "false")
        << ",\"healthy\":" << (healthy ? "true" : "false")
        << ",\"sampleAgeMs\":" << ageMs
        << ",\"sampleHz\":" << metrics.sampleHz
        << ",\"validFrames\":" << metrics.validFrames
        << ",\"crcErrors\":" << metrics.crcErrors
        << ",\"nonFiniteFrames\":" << metrics.nonFiniteFrames
        << ",\"resyncBytes\":" << metrics.resyncBytes
        << ",\"error\":\"" << escapeJson(metrics.error) << "\""
        << ",\"axisSign\":";
    appendArray(out, config_.axisSign[side]);
    out << ",\"sensorTareBias\":";
    appendArray(out, metrics.tareBias);
    out << ",\"tareBias\":";
    appendArray(out, alignedTareBias);
    out << "}";
  }
  const auto& trip = safety_.trip();
  std::string acknowledgeBlocker;
  bool canAcknowledge =
      safety_.canAcknowledge(nowMonotonicMs, &acknowledgeBlocker);
  if (config_.source == "hkvl_serial"
      && calibration_.state != "ready_for_ack" && calibration_.state != "ready") {
    canAcknowledge = false;
    acknowledgeBlocker = "startup force self-check is not complete";
  }
  if (emergencyCallbackFailed_.load()) {
    canAcknowledge = false;
    acknowledgeBlocker = "force emergency-stop callback failed; diagnose and reconfigure before acknowledgement";
  }
  if (workerFailed_.load()) {
    canAcknowledge = false;
    acknowledgeBlocker = "force worker failed; reconfigure before acknowledgement";
  }
  out << "},\"safety\":{\"latched\":"
      << (safety_.latched() ? "true" : "false")
      << ",\"reason\":\"" << escapeJson(trip.reason) << "\""
      << ",\"side\":\""
      << (trip.side == 0 ? "left" : trip.side == 1 ? "right" : "")
      << "\",\"channel\":\""
      << (trip.channel >= 0 && trip.channel < 6 ? kForceChannels[trip.channel] : "")
      << "\",\"value\":" << trip.value
      << ",\"canAcknowledge\":" << (canAcknowledge ? "true" : "false")
      << ",\"emergencyCallbackError\":\"" << escapeJson(emergencyCallbackError_) << "\""
      << ",\"workerError\":\"" << escapeJson(workerError_) << "\""
      << ",\"acknowledgeBlocker\":\"" << escapeJson(acknowledgeBlocker) << "\"}"
      << ",\"compliance\":{\"enabled\":"
      << (config_.compliance.enabled ? "true" : "false");
  for (int side = 0; side < 2; ++side) {
    const auto cumulative = compliance_.cumulativeOffset(side);
    out << ",\"" << (side == 0 ? "left" : "right") << "\":{"
        << "\"mappingConfirmed\":"
        << (config_.compliance.sides[side].mappingConfirmed ? "true" : "false")
        << ",\"active\":" << (lastCompliance_[side].active ? "true" : "false")
        << ",\"requestedUm\":";
    appendArray(out, lastCompliance_[side].requestedUm);
    out << ",\"correctionUm\":";
    appendArray(out, lastCompliance_[side].correctionUm);
    out << ",\"actualUm\":";
    appendArray(out, lastComplianceActualUm_[side]);
    out << ",\"cumulativeOffsetUm\":";
    appendArray(out, cumulative);
    out << ",\"clipReason\":[\""
        << escapeJson(lastCompliance_[side].clipReason[0]) << "\",\""
        << escapeJson(lastCompliance_[side].clipReason[1]) << "\"]}";
  }
  out << "}}";
  return out.str();
}

void ForceControlRuntime::appendCalibrationJson(std::ostringstream& out) const {
  out << "{\"state\":\"" << escapeJson(calibration_.state) << "\""
      << ",\"progress\":" << calibration_.progress
      << ",\"reason\":\"" << escapeJson(calibration_.reason) << "\""
      << ",\"completedAtUnixMs\":"
      << (calibration_.hasResult ? calibration_.result.completedAtUnixMs : 0)
      << ",\"sides\":{";
  if (!calibration_.hasResult) {
    out << "}}";
    return;
  }
  for (int side = 0; side < 2; ++side) {
    if (side > 0) {
      out << ",";
    }
    const auto& result = calibration_.result.sides[side];
    out << "\"" << (side == 0 ? "left" : "right") << "\":{";
    out << "\"bias\":";
    appendArray(out, result.bias);
    out << ",\"preMean\":";
    appendArray(out, result.before.mean);
    out << ",\"preStdDev\":";
    appendArray(out, result.before.standardDeviation);
    out << ",\"prePeakToPeak\":";
    appendArray(out, result.before.peakToPeak);
    out << ",\"residualMean\":";
    appendArray(out, result.after.mean);
    out << ",\"residualStdDev\":";
    appendArray(out, result.after.standardDeviation);
    out << ",\"residualPeakToPeak\":";
    appendArray(out, result.after.peakToPeak);
    out << "}";
  }
  out << "}}";
}

void ForceControlRuntime::validateConfig(
    const ForceRuntimeConfig& config) {
  if (config.source != "nidaq" && config.source != "hkvl_serial") {
    throw std::invalid_argument("force.source must be nidaq or hkvl_serial");
  }
  if (config.safety.watchdogMs <= 0.0
      || config.safety.acknowledgeStableMs < 0.0) {
    throw std::invalid_argument("force safety timing values must be positive");
  }
  for (std::size_t channel = 0; channel < config.safety.warn.size(); ++channel) {
    const double upper = channel < 3 ? 30.0 : 1.0;
    if (!(config.safety.warn[channel] > 0.0
          && config.safety.warn[channel] < config.safety.stop[channel]
          && config.safety.stop[channel] <= upper)) {
      throw std::invalid_argument(
          "force safety thresholds require 0 < warn < stop <= sensor range");
    }
  }
  if (config.source == "hkvl_serial") {
    if (lowercase(config.serial.leftPort) == lowercase(config.serial.rightPort)) {
      throw std::invalid_argument("HKVL left and right serial ports must differ");
    }
    if (config.serial.protocol != "hkvl_active_v1") {
      throw std::invalid_argument("HKVL protocol must be hkvl_active_v1");
    }
    if (config.serial.baudrate != 1000000) {
      throw std::invalid_argument("HKVL baudrate must be 1000000");
    }
    if (config.serial.expectedSampleHz != 1000) {
      throw std::invalid_argument("HKVL expected sample rate must be 1000 Hz");
    }
    if (config.serial.lowpassEnabled && config.serial.lowpassCutoffHz <= 0.0) {
      throw std::invalid_argument("HKVL low-pass cutoff must be positive");
    }
    for (const auto& side : config.axisSign) {
      for (const double value : side) {
        if (value != -1.0 && value != 1.0) {
          throw std::invalid_argument(
              "HKVL force axis signs must be exactly -1 or 1");
        }
      }
    }
  }
  if (config.compliance.enabled) {
    for (const auto& side : config.compliance.sides) {
      if (!side.mappingConfirmed) {
        throw std::invalid_argument(
            "force compliance requires confirmed mapping for both sides");
      }
    }
  }
  for (const auto& side : config.compliance.sides) {
    for (const double value : side.matrix) {
      if (!std::isfinite(value)) {
        throw std::invalid_argument("force compliance matrix must be finite");
      }
    }
    for (std::size_t axis = 0; axis < 2; ++axis) {
      if (side.deadbandN[axis] < 0.0
          || side.gainUmPerNs[axis] < 0.0
          || side.maxStepUm[axis] < 0.0
          || side.maxOffsetUm[axis] < 0.0) {
        throw std::invalid_argument(
            "force compliance limits and gains must be non-negative");
      }
    }
  }
}

void ForceControlRuntime::monitorLoop() {
  while (running_.load(std::memory_order_acquire)) {
    if (monitorTick_) monitorTick_();
    else checkSafety(forceMonotonicMilliseconds());
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
}

void ForceControlRuntime::invokeEmergencyStopIfNeeded(
    const std::optional<ForceSafetyTrip>& trip) noexcept {
  if (trip.has_value() && emergencyStop_) {
    try { emergencyStop_(); }
    catch (const std::exception& error) { recordEmergencyCallbackFailure(error.what()); }
    catch (...) { recordEmergencyCallbackFailure("unknown C++ exception in force emergency-stop callback"); }
  }
}

void ForceControlRuntime::reportWorkerFailure(const char* message) noexcept {
  const bool first = !workerFailed_.exchange(true);
  running_.store(false, std::memory_order_release);
  driver_.requestStop();
  // 先撤销执行权限；不能把安全动作排在诊断锁或字符串分配后。
  if (first) {
    try { if (emergencyStop_) emergencyStop_(); }
    catch (const std::exception& error) { recordEmergencyCallbackFailure(error.what()); }
    catch (...) { recordEmergencyCallbackFailure("unknown C++ exception in force worker shutdown"); }
  }
  // 故障状态先原子锁定；分配或诊断失败也不能放行 ACK。
  try {
    std::scoped_lock lock(mutex_);
    ++tareEpoch_;
    safety_.latchExternal("force_worker_failed", forceMonotonicMilliseconds());
    compliance_.reset();
    lastCompliance_ = {};
    if (first) workerError_ = message;
  } catch (...) { std::fputs("force worker fault: latch/diagnostic update failed\n", stderr); }
  std::fprintf(stderr, "force worker stopped: %s\n", message);
}

void ForceControlRuntime::recordEmergencyCallbackFailure(const char* message) noexcept {
  // 原始力锁存仍保留；先标记故障，诊断字符串分配失败也不能允许确认恢复。
  emergencyCallbackFailed_.store(true);
  std::fprintf(stderr, "force emergency-stop callback failed: %s\n", message);
  try {
    std::scoped_lock lock(mutex_);
    emergencyCallbackError_ = message;
  } catch (...) {
    std::fputs("force emergency-stop callback diagnostic update failed\n", stderr);
  }
}

}  // namespace appstation::hal
