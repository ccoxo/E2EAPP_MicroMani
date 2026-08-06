#pragma once

#include "ForceComplianceController.h"
#include "ForceSafetyLatch.h"
#include "HkvlForceDriver.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace appstation::hal {

struct ForceRuntimeConfig {
  std::string source{"nidaq"};
  HkvlSerialConfig serial{};
  std::array<std::array<double, 6>, 2> axisSign{{
      {{-1.0, 1.0, -1.0, 1.0, -1.0, -1.0}},
      {{-1.0, -1.0, -1.0, 1.0, 1.0, 1.0}},
  }};
  ForceSafetyConfig safety{};
  ForceComplianceConfig compliance{};
};

class ForceControlRuntime {
 public:
  using EmergencyStopCallback = std::function<void()>;
  using AcknowledgeCallback = std::function<void()>;

  ForceControlRuntime(
      EmergencyStopCallback emergencyStop,
      AcknowledgeCallback acknowledge);
  ~ForceControlRuntime();

  ForceControlRuntime(const ForceControlRuntime&) = delete;
  ForceControlRuntime& operator=(const ForceControlRuntime&) = delete;

  void configure(const ForceRuntimeConfig& config, double nowMonotonicMs);
  ForceRuntimeConfig config() const;
  void start();
  void stop();
  bool running() const;
  bool usesHkvl() const;

  void acceptSample(
      int side,
      const std::array<double, 6>& tared,
      const std::array<double, 6>& filtered,
      double monotonicMs,
      std::int64_t unixMs);
  void checkSafety(double nowMonotonicMs);
  void recordExternalEmergencyStop(const std::string& reason, double nowMonotonicMs);
  void acknowledgeEmergencyStop(double nowMonotonicMs);
  bool safetyLatched() const;

  void tare(int side, int sampleCount);
  ForceComplianceResult complianceCorrection(
      int side,
      std::uint64_t targetMonotonicMs);
  void commitCompliance(
      int side,
      const std::array<double, 2>& requestedUm,
      const std::array<double, 2>& actualUm);
  void resetCompliance();
  std::string forceStateJson(double nowMonotonicMs);

 private:
  static void validateConfig(const ForceRuntimeConfig& config);
  void monitorLoop();
  void invokeEmergencyStopIfNeeded(
      const std::optional<ForceSafetyTrip>& trip);

  EmergencyStopCallback emergencyStop_;
  AcknowledgeCallback acknowledge_;
  mutable std::mutex mutex_;
  ForceRuntimeConfig config_{};
  ForceSafetyLatch safety_{};
  ForceComplianceController compliance_;
  std::array<std::array<double, 6>, 2> latestTared_{};
  std::array<std::array<double, 6>, 2> latestFiltered_{};
  std::array<double, 2> latestMonotonicMs_{};
  std::array<std::int64_t, 2> latestUnixMs_{};
  std::array<bool, 2> hasSample_{{false, false}};
  std::array<ForceComplianceResult, 2> lastCompliance_{};
  std::array<std::array<double, 2>, 2> lastComplianceActualUm_{};
  HkvlForceDriver driver_;
  std::atomic<bool> running_{false};
  std::thread monitor_;
};

double forceMonotonicMilliseconds();

}  // namespace appstation::hal
