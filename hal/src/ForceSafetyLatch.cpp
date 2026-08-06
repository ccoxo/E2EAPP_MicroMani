#include "ForceSafetyLatch.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace appstation::hal {

ForceSafetyLatch::ForceSafetyLatch(
    const ForceSafetyConfig& config,
    double configuredAtMs) {
  configure(config, configuredAtMs);
}

void ForceSafetyLatch::configure(
    const ForceSafetyConfig& config,
    double nowMs) {
  for (std::size_t i = 0; i < config.warn.size(); ++i) {
    if (!(config.warn[i] > 0.0 && config.warn[i] < config.stop[i])) {
      throw std::invalid_argument("force safety thresholds require 0 < warn < stop");
    }
  }
  if (config.watchdogMs <= 0.0 || config.acknowledgeStableMs < 0.0) {
    throw std::invalid_argument("force safety timing values must be positive");
  }
  config_ = config;
  configuredAtMs_ = nowMs;
  latched_ = false;
  trip_ = {};
  latest_ = {};
  consecutiveAtStop_ = {};
  lastSampleMs_ = {};
  hasSample_ = {false, false};
  connected_ = {false, false};
  stableSinceMs_.reset();
}

std::optional<ForceSafetyTrip> ForceSafetyLatch::onSample(
    int side,
    const std::array<double, 6>& values,
    double nowMs) {
  if (side < 0 || side >= 2) {
    throw std::out_of_range("force safety side must be 0 or 1");
  }
  latest_[side] = values;
  lastSampleMs_[side] = nowMs;
  hasSample_[side] = true;
  connected_[side] = true;

  std::optional<ForceSafetyTrip> newTrip;
  for (std::size_t channel = 0; channel < values.size(); ++channel) {
    const double magnitude = std::abs(values[channel]);
    if (magnitude >= config_.stop[channel] * 1.2) {
      consecutiveAtStop_[side][channel] = 0;
      if (!latched_) {
        newTrip = latch(
            side,
            static_cast<int>(channel),
            values[channel],
            "force_120_percent");
      }
      break;
    }
    if (magnitude >= config_.stop[channel]) {
      ++consecutiveAtStop_[side][channel];
      if (consecutiveAtStop_[side][channel] >= 3 && !latched_) {
        newTrip = latch(
            side,
            static_cast<int>(channel),
            values[channel],
            "force_three_samples");
        break;
      }
    } else {
      consecutiveAtStop_[side][channel] = 0;
    }
  }
  updateStableWindow(nowMs);
  return newTrip;
}

std::optional<ForceSafetyTrip> ForceSafetyLatch::checkWatchdog(double nowMs) {
  for (int side = 0; side < 2; ++side) {
    const double ageMs = hasSample_[side]
        ? nowMs - lastSampleMs_[side]
        : nowMs - configuredAtMs_;
    if ((!connected_[side] || !hasSample_[side] || ageMs > config_.watchdogMs)
        && !latched_) {
      stableSinceMs_.reset();
      return latch(side, -1, ageMs, "watchdog_timeout");
    }
  }
  updateStableWindow(nowMs);
  return std::nullopt;
}

std::optional<ForceSafetyTrip> ForceSafetyLatch::latchExternal(
    const std::string& reason,
    double nowMs) {
  (void)nowMs;
  if (latched_) {
    return std::nullopt;
  }
  return latch(-1, -1, 0.0, reason);
}

void ForceSafetyLatch::markDisconnected(int side, double nowMs) {
  if (side < 0 || side >= 2) {
    return;
  }
  connected_[side] = false;
  stableSinceMs_.reset();
  (void)nowMs;
}

bool ForceSafetyLatch::latched() const {
  return latched_;
}

const ForceSafetyTrip& ForceSafetyLatch::trip() const {
  return trip_;
}

double ForceSafetyLatch::dangerIndex() const {
  double danger = 0.0;
  for (int side = 0; side < 2; ++side) {
    if (!hasSample_[side]) {
      continue;
    }
    for (std::size_t channel = 0; channel < latest_[side].size(); ++channel) {
      danger = std::max(
          danger,
          std::abs(latest_[side][channel]) / config_.stop[channel]);
    }
  }
  return std::clamp(danger, 0.0, 1.2);
}

bool ForceSafetyLatch::canAcknowledge(
    double nowMs,
    std::string* blocker) {
  updateStableWindow(nowMs);
  if (!latched_) {
    if (blocker) {
      blocker->clear();
    }
    return true;
  }
  if (!healthyAndUnloaded(nowMs, blocker)) {
    return false;
  }
  if (!stableSinceMs_.has_value()
      || nowMs - *stableSinceMs_ < config_.acknowledgeStableMs) {
    if (blocker) {
      *blocker = "both sensors must remain healthy and below warning thresholds for 500 ms";
    }
    return false;
  }
  if (blocker) {
    blocker->clear();
  }
  return true;
}

void ForceSafetyLatch::acknowledge(double nowMs) {
  std::string blocker;
  if (!canAcknowledge(nowMs, &blocker)) {
    throw std::runtime_error(blocker);
  }
  latched_ = false;
  trip_ = {};
  consecutiveAtStop_ = {};
}

std::optional<ForceSafetyTrip> ForceSafetyLatch::latch(
    int side,
    int channel,
    double value,
    const std::string& reason) {
  latched_ = true;
  trip_ = ForceSafetyTrip{side, channel, value, reason};
  stableSinceMs_.reset();
  return trip_;
}

void ForceSafetyLatch::updateStableWindow(double nowMs) {
  if (!healthyAndUnloaded(nowMs, nullptr)) {
    stableSinceMs_.reset();
    return;
  }
  if (!stableSinceMs_.has_value()) {
    stableSinceMs_ = nowMs;
  }
}

bool ForceSafetyLatch::healthyAndUnloaded(
    double nowMs,
    std::string* blocker) const {
  for (int side = 0; side < 2; ++side) {
    if (!connected_[side] || !hasSample_[side]) {
      if (blocker) {
        *blocker = side == 0 ? "left force sensor is not healthy" : "right force sensor is not healthy";
      }
      return false;
    }
    if (nowMs - lastSampleMs_[side] > config_.watchdogMs) {
      if (blocker) {
        *blocker = side == 0 ? "left force sample is stale" : "right force sample is stale";
      }
      return false;
    }
    for (std::size_t channel = 0; channel < latest_[side].size(); ++channel) {
      if (std::abs(latest_[side][channel]) >= config_.warn[channel]) {
        if (blocker) {
          *blocker = side == 0
              ? "left force sensor is above warning threshold"
              : "right force sensor is above warning threshold";
        }
        return false;
      }
    }
  }
  return true;
}

}  // namespace appstation::hal
