#pragma once

#include <array>
#include <optional>
#include <string>

namespace appstation::hal {

struct ForceSafetyConfig {
  std::array<double, 6> warn{{2.0, 2.0, 3.0, 0.02, 0.02, 0.02}};
  std::array<double, 6> stop{{30.0, 30.0, 30.0, 1.0, 1.0, 1.0}};
  double watchdogMs{50.0};
  double acknowledgeStableMs{500.0};
};

struct ForceSafetyTrip {
  int side{-1};
  int channel{-1};
  double value{0.0};
  std::string reason;
};

class ForceSafetyLatch {
 public:
  explicit ForceSafetyLatch(
      const ForceSafetyConfig& config = {},
      double configuredAtMs = 0.0);

  void configure(const ForceSafetyConfig& config, double nowMs);
  std::optional<ForceSafetyTrip> onSample(
      int side,
      const std::array<double, 6>& values,
      double nowMs);
  std::optional<ForceSafetyTrip> checkWatchdog(double nowMs);
  std::optional<ForceSafetyTrip> latchExternal(
      const std::string& reason,
      double nowMs);
  void markDisconnected(int side, double nowMs);

  bool latched() const;
  const ForceSafetyTrip& trip() const;
  double dangerIndex() const;
  bool canAcknowledge(double nowMs, std::string* blocker = nullptr);
  void acknowledge(double nowMs);

 private:
  std::optional<ForceSafetyTrip> latch(
      int side,
      int channel,
      double value,
      const std::string& reason);
  void updateStableWindow(double nowMs);
  bool healthyAndUnloaded(double nowMs, std::string* blocker) const;

  ForceSafetyConfig config_{};
  double configuredAtMs_{0.0};
  bool latched_{false};
  ForceSafetyTrip trip_{};
  std::array<std::array<double, 6>, 2> latest_{};
  std::array<std::array<int, 6>, 2> consecutiveAtStop_{};
  std::array<double, 2> lastSampleMs_{};
  std::array<bool, 2> hasSample_{{false, false}};
  std::array<bool, 2> connected_{{false, false}};
  std::optional<double> stableSinceMs_;
};

}  // namespace appstation::hal
