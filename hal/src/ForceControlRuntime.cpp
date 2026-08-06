#include "ForceControlRuntime.h"

#include <algorithm>
#include <cctype>
#include <cmath>
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
    AcknowledgeCallback acknowledge)
    : emergencyStop_(std::move(emergencyStop)),
      acknowledge_(std::move(acknowledge)) {}

ForceControlRuntime::~ForceControlRuntime() {
  stop();
}

void ForceControlRuntime::configure(
    const ForceRuntimeConfig& config,
    double nowMonotonicMs) {
  validateConfig(config);
  const bool restart = running();
  std::optional<ForceSafetyTrip> pendingTrip;
  if (restart) {
    stop();
  }
  {
    std::scoped_lock lock(mutex_);
    config_ = config;
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
  driver_.start(current.serial, [this](const HkvlDriverSample& sample) {
    acceptSample(
        sample.side,
        sample.tared,
        sample.filtered,
        sample.monotonicMs,
        sample.unixMs);
  });
  monitor_ = std::thread([this]() { monitorLoop(); });
}

void ForceControlRuntime::stop() {
  running_.store(false, std::memory_order_release);
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
    trip = safety_.onSample(side, alignedTared, monotonicMs);
    if (trip.has_value()) {
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
    if (trip.has_value()) {
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
    double nowMonotonicMs) {
  std::scoped_lock lock(mutex_);
  if (config_.source == "hkvl_serial") {
    safety_.acknowledge(nowMonotonicMs);
  }
  compliance_.reset();
  lastCompliance_ = {};
  lastComplianceActualUm_ = {};
  // Keep this lock until motion acknowledgement completes so a new force trip cannot race it.
  if (acknowledge_) {
    acknowledge_();
  }
}

bool ForceControlRuntime::safetyLatched() const {
  std::scoped_lock lock(mutex_);
  return safety_.latched();
}

void ForceControlRuntime::tare(int side, int sampleCount) {
  if (!usesHkvl()) {
    throw std::runtime_error("force.tare is only available for hkvl_serial");
  }
  driver_.tare(side, sampleCount);
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
  const bool canAcknowledge =
      safety_.canAcknowledge(nowMonotonicMs, &acknowledgeBlocker);
  out << "},\"safety\":{\"latched\":"
      << (safety_.latched() ? "true" : "false")
      << ",\"reason\":\"" << escapeJson(trip.reason) << "\""
      << ",\"side\":\""
      << (trip.side == 0 ? "left" : trip.side == 1 ? "right" : "")
      << "\",\"channel\":\""
      << (trip.channel >= 0 && trip.channel < 6 ? kForceChannels[trip.channel] : "")
      << "\",\"value\":" << trip.value
      << ",\"canAcknowledge\":" << (canAcknowledge ? "true" : "false")
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
    checkSafety(forceMonotonicMilliseconds());
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
}

void ForceControlRuntime::invokeEmergencyStopIfNeeded(
    const std::optional<ForceSafetyTrip>& trip) {
  if (trip.has_value() && emergencyStop_) {
    emergencyStop_();
  }
}

}  // namespace appstation::hal
