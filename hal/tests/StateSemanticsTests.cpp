#include "OfflineMotion.h"
#include "HalJson.h"
#include "LTDMCDriver.h"

#include <future>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace appstation::hal;
namespace {
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

void cachedMotionStatePreservesHardwareSampleTime() {
  LTDMCDriver motion;
  MotionExecutorTestAccess::initialize(motion);
  const auto fresh = motion.readState();
  require(!fresh.sampleCached && fresh.readTimestampMs > 0, "fresh sample metadata is invalid");
  auto driverLock = MotionExecutorTestAccess::holdDriver(motion);
  auto future = std::async(std::launch::async, [&]() { return motion.readState(); });
  const auto cached = future.get();
  require(cached.sampleCached, "lock-contended state was not marked cached");
  require(cached.readTimestampMs == fresh.readTimestampMs,
      "cached state fabricated a new controller sample timestamp");
}

void syntheticStateUpdateInvalidatesHardwareSampleTime() {
  LTDMCDriver motion;
  MotionExecutorTestAccess::initialize(motion);
  const auto fresh = motion.readState();
  require(fresh.readTimestampMs > 0, "fresh sample timestamp missing");
  motion.latchEmergencyStop();
  try { motion.emergencyStop(); } catch (...) {}
  const auto cached = motion.latestState();
  require(cached.sampleCached, "command-side snapshot must be marked cached");
  require(cached.readTimestampMs == 0,
      "command-side state update reused an old controller timestamp for synthetic feedback");
}

void motionJsonDoesNotInventMissingControllerTimestamp() {
  MotionState state;
  state.readTimestampMs = 0;
  state.sampleCached = true;
  const auto json = jsonMotionState(state);
  require(json.find("\"timestamp_ms\":0") != std::string::npos,
      "motion JSON invented a controller timestamp before any hardware sample");
}

void motionJsonPreservesIntegerPulsePrecisionAndFeedbackTruth() {
  MotionState state;
  state.readTimestampMs = 123456789;
  state.sampleCached = true;
  state.axes[0].pulse = -2284781.0;
  state.axes[1].pulse = -2284784.0;
  state.axes[0].enabled = false;
  state.axes[0].enabledConfirmed = false;
  state.axes[1].enabled = true;
  state.axes[1].enabledConfirmed = true;
  const auto json = jsonMotionState(state);
  require(json.find("-2284781") != std::string::npos && json.find("-2284784") != std::string::npos,
      "motion JSON rounded distinct controller pulse counts to the same value");
  require(json.find("\"sample_cached\":true") != std::string::npos,
      "motion JSON omitted cached-sample provenance");
  require(json.find("\"enabled_confirmed\":[false,true") != std::string::npos,
      "motion JSON omitted servo-feedback confirmation semantics");
}
}

int main() {
  try {
    cachedMotionStatePreservesHardwareSampleTime();
    syntheticStateUpdateInvalidatesHardwareSampleTime();
    motionJsonDoesNotInventMissingControllerTimestamp();
    motionJsonPreservesIntegerPulsePrecisionAndFeedbackTruth();
    std::cout << "StateSemanticsTests passed (4 offline cases)" << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "StateSemanticsTests failed: " << error.what() << std::endl;
    return 1;
  }
}
