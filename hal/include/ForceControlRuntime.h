/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明ForceControlRuntime 的接口与状态结构；连接 HKVL 采样、安全锁存和柔顺控制，管理监控线程与急停/确认回调。
 * 先看：ForceRuntimeConfig → ForceControlRuntime。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
  std::string source{"hkvl_serial"};
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
      AcknowledgeCallback acknowledge,
      HkvlForceDriver::ReadLoop readLoop = {},
      std::function<void()> monitorTick = {},
      WorkerLauncher launcher = {});
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
  void acknowledgeEmergencyStop(double nowMonotonicMs, AcknowledgeCallback acknowledge = {});
  bool safetyLatched() const;

  void tare(int side, int sampleCount, std::function<bool()> commandAllowed = {});
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
      const std::optional<ForceSafetyTrip>& trip) noexcept;
  void recordEmergencyCallbackFailure(const char* message) noexcept;
  void reportWorkerFailure(const char* message) noexcept;

  EmergencyStopCallback emergencyStop_;
  AcknowledgeCallback acknowledge_;
  std::atomic_bool emergencyCallbackFailed_{false};
  std::string emergencyCallbackError_;
  std::atomic_bool workerFailed_{false};
  std::string workerError_;
  std::function<void()> monitorTick_;
  WorkerLauncher launcher_;
  std::recursive_mutex lifecycleMutex_;
  mutable std::mutex mutex_;
  ForceRuntimeConfig config_{};
  ForceSafetyLatch safety_{};
  std::uint64_t tareEpoch_{0};
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
