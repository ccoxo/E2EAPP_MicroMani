#include "ForceControlRuntime.h"
#include "HttpConnectionWorkers.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"

#include <atomic>
#include <chrono>
#include <iostream>
#include <future>
#include <stdexcept>
#include <thread>

using namespace appstation::hal;

namespace {
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

void testPublisherFailureStopsControl(bool standardException) {
  LTDMCDriver motion;
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController teleop(motion, omega, gripper);
  std::atomic_int calls{0};
  teleop.setLeaderStatePublisher([&](const auto&) {
    calls.fetch_add(1);
    if (standardException) throw std::runtime_error("injected publisher failure");
    throw 42;
  });
  // 没有初始化设备，也没有打开夹爪 worker；只注入进程内发布回调。
  try { teleop.start(false, false); }
  catch (const std::runtime_error&) {
    if (!motion.estopActive()) throw;
  }
  const auto expectedError = standardException ? "injected publisher failure" : "unknown C++ exception";
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
  while ((teleop.running() || teleop.statusJson().find(expectedError) == std::string::npos)
      && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  const bool stopped = !teleop.running() && motion.estopActive();
  const auto status = teleop.statusJson();
  require(stopped, "publisher exception was swallowed while control kept running without a safety latch");
  require(calls.load() == 1, "failed publisher must not be retried by the next control tick");
  require(status.find(standardException ? "injected publisher failure" : "unknown C++ exception") != std::string::npos,
      "fault must remain visible in native diagnostics");
  teleop.stop();
  teleop.setLeaderStatePublisher([&](const auto&) { calls.fetch_add(1); });
  motion.acknowledgeEmergencyStop();
  teleop.start(false, false);
  const auto restartDeadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
  while (calls.load() < 2 && std::chrono::steady_clock::now() < restartDeadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  require(calls.load() >= 2 && teleop.running(), "explicit recovery must reap the failed worker and start a new session");
  teleop.stop();
}

void testForceStopCallbackFailureStaysContained(bool standardException) {
  ForceControlRuntime force([&]() {
    if (standardException) throw std::runtime_error("injected force stop callback failure");
    throw 42;
  }, []() {});
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  config.safety.watchdogMs = 1000.0;
  bool escaped = false;
  try { force.configure(config, 0.0); }
  catch (...) { escaped = true; }
  require(!escaped, "emergency callback exceptions must not escape into serial or monitor workers");
  require(force.safetyLatched(), "callback failure must preserve the force latch");
  force.acceptSample(0, {}, {}, 1.0, 1);
  force.acceptSample(1, {}, {}, 1.0, 1);
  force.acceptSample(0, {}, {}, 501.0, 501);
  force.acceptSample(1, {}, {}, 501.0, 501);
  bool blocked = false;
  try { force.acknowledgeEmergencyStop(501.0); }
  catch (const std::runtime_error&) { blocked = true; }
  require(blocked && force.safetyLatched(), "safe samples alone must not erase a failed emergency callback");
  const auto status = force.forceStateJson(501.0);
  require(status.find(standardException ? "injected force stop callback failure" : "unknown C++ exception") != std::string::npos,
      "force callback failure must remain visible in diagnostics");
}

void testHttpConnectionCapacityAndReuse() {
  HttpConnectionWorkers<2> workers;
  std::promise<void> releasePromise;
  auto release = releasePromise.get_future().share();
  require(workers.tryStart([&]() { release.wait(); }), "first HTTP worker should start");
  require(workers.tryStart([&]() { release.wait(); }), "second HTTP worker should start");
  require(!workers.tryStart([]() {}), "slow HTTP connections must not grow past the fixed capacity");
  releasePromise.set_value();
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
  while (workers.activeCount() != 0 && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
  require(workers.activeCount() == 0 && workers.tryStart([]() {}), "completed HTTP workers should release their slots");
}

void testHttpConnectionFailureReleasesCapacity() {
  HttpConnectionWorkers<1> workers;
  require(workers.tryStart([]() { throw 42; }), "HTTP worker should accept the injected failing callback");
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
  while (workers.activeCount() != 0 && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
  require(workers.activeCount() == 0 && workers.tryStart([]() {}), "HTTP callback failure must release its slot and preserve the process");
}
}  // namespace

int main(int argc, char** argv) {
  try {
    if (argc > 1 && std::string(argv[1]) == "--force-only") {
      testForceStopCallbackFailureStaysContained(true);
      std::cout << "Force callback test passed\n";
      return 0;
    }
    testPublisherFailureStopsControl(true);
    testPublisherFailureStopsControl(false);
    testForceStopCallbackFailureStaysContained(true);
    testForceStopCallbackFailureStaysContained(false);
    testHttpConnectionCapacityAndReuse();
    testHttpConnectionFailureReleasesCapacity();
    std::cout << "ThreadStabilityTests passed (6 cases, callback exceptions and bounded HTTP workers)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ThreadStabilityTests failed: " << error.what() << "\n";
    return 1;
  }
}
