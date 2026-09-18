#include "CommandDeadline.h"
#include "ForceControlRuntime.h"
#include "HkvlForceDriver.h"
#include "MotionControlThread.h"
#include "TeleopHardwareTargetExecutor.h"
#include "WorkerExceptionBoundary.h"

#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>

using namespace appstation::hal;

namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

template <typename Check>
void await(Check check, const char* message) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
  while (!check() && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
  require(check(), message);
}

[[noreturn]] void injectedFailure(bool standard) {
  if (standard) throw std::runtime_error("injected worker failure");
  throw 42;
}

HkvlForceDriver::ReadLoop idleReader(std::atomic_int& active) {
  return [&](int, const auto&, const std::atomic_bool& running) {
    ++active;
    struct Exit { std::atomic_int& active; ~Exit() { --active; } } exit{active};
    while (running.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
  };
}

void testMotionFailureAndRecovery(bool standard) {
  LTDMCDriver motion;
  std::atomic_bool fail{true};
  std::atomic_int polls{0}, callbacks{0};
  MotionControlThread worker(motion, [&](const char*) {
    ++callbacks;
    throw 17; // 追加停止动作失败也不能绕过驱动锁存和错误记录。
  }, [&]() {
    ++polls;
    if (fail.load()) injectedFailure(standard);
  });
  worker.start(1000);
  await([&]() { return !worker.running() && !worker.lastError().empty(); }, "motion worker fault was not contained");
  require(motion.estopActive() && callbacks.load() == 1 && polls.load() == 1, "motion fault must latch and stop polling");
  bool blocked = false;
  try { worker.start(1000); } catch (const std::runtime_error&) { blocked = true; }
  require(blocked, "failed motion worker restarted before acknowledgement");
  motion.acknowledgeEmergencyStop();
  fail.store(false);
  worker.start(1000);
  await([&]() { return polls.load() >= 2; }, "motion worker could not restart after explicit recovery");
  worker.stop();
  require(!worker.running(), "motion worker stop must clear running");
}

void testMotionThreadCreationFailure(bool standard) {
  LTDMCDriver motion;
  std::atomic_bool fail{true};
  MotionControlThread worker(motion, {}, []() {}, [&](std::function<void()> entry) -> std::thread {
    if (fail.exchange(false)) injectedFailure(standard);
    return std::thread(std::move(entry));
  });
  bool escaped = false;
  try { worker.start(1000); } catch (...) { escaped = true; }
  require(escaped && !worker.running() && motion.estopActive(), "thread creation failure must be reported and latched");
  motion.acknowledgeEmergencyStop();
  worker.start(1000);
  worker.stop();
}

void testMotionInvalidFrequency() {
  LTDMCDriver motion;
  MotionControlThread worker(motion, {}, []() {});
  for (int hz : {0, -1, 1000001}) {
    bool rejected = false;
    try { worker.start(hz); } catch (const std::invalid_argument&) { rejected = true; }
    require(rejected && !worker.running(), "invalid polling frequency should be rejected before thread creation");
  }
}

void testSerialCallbackFailure(bool standard) {
  std::atomic_int calls{0}, failures{0};
  std::atomic_bool fail{true};
  HkvlForceDriver driver([&](int side, const auto& sample, const std::atomic_bool& running) {
    if (side == 0 && running.load()) sample(HkvlDriverSample{});
    while (running.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
  });
  const auto callback = [&](const HkvlDriverSample&) {
    ++calls;
    if (fail.load()) injectedFailure(standard);
  };
  driver.start({}, callback, [&](int, const char*) { ++failures; throw 18; });
  await([&]() { return !driver.running() && !driver.snapshot(0).sides[0].error.empty(); }, "serial callback escaped or lost its diagnostic");
  require(calls.load() == 1 && failures.load() == 1, "serial callback failure must stop both readers");
  driver.stop();
  fail.store(false);
  driver.start({}, callback);
  await([&]() { return calls.load() >= 2; }, "serial worker restart did not reap the failed thread");
  driver.stop();
}

void testSerialPartialStartupFailure() {
  std::atomic_int active{0}, launches{0}, failures{0};
  HkvlForceDriver driver(idleReader(active), [&](std::function<void()> entry) -> std::thread {
    if (++launches == 2) throw std::runtime_error("second serial worker creation failed");
    return std::thread(std::move(entry));
  });
  bool escaped = false;
  try { driver.start({}, {}, [&](int, const char*) { ++failures; }); } catch (...) { escaped = true; }
  require(escaped && !driver.running() && active.load() == 0 && failures.load() == 1,
      "partial serial startup must stop and join the first worker before returning");
  driver.start({}, {});
  driver.stop();
}

void testForceMonitorFailure(bool standard) {
  std::atomic_int active{0}, stops{0}, ticks{0};
  std::atomic_bool fail{true};
  ForceControlRuntime force([&]() { ++stops; }, []() {}, idleReader(active), [&]() {
    ++ticks;
    if (fail.load()) injectedFailure(standard);
  });
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  force.configure(config, 0);
  force.start();
  await([&]() { return !force.running() && stops.load() >= 2 && force.forceStateJson(0).find("workerError\":\"\"") == std::string::npos; }, "force monitor fault was not visible");
  require(force.safetyLatched() && stops.load() >= 2, "force monitor failure must run the safety stop even if already latched");
  force.stop();
  require(active.load() == 0, "force failure must stop and reap both serial readers");
  bool ackBlocked = false, restartBlocked = false;
  try { force.acknowledgeEmergencyStop(1000); } catch (...) { ackBlocked = true; }
  try { force.start(); } catch (...) { restartBlocked = true; }
  require(ackBlocked && restartBlocked, "force worker fault must block ACK and restart until reconfigured");
  fail.store(false);
  force.configure(config, 1000);
  force.start();
  await([&]() { return ticks.load() >= 2; }, "force reconfiguration could not restart its reaped monitor");
  force.stop();
}

void testForceMonitorCreationFailure() {
  std::atomic_int active{0}, launches{0}, stops{0};
  ForceControlRuntime force([&]() { ++stops; }, []() {}, idleReader(active), []() {},
      [&](std::function<void()> entry) -> std::thread {
        if (++launches == 3) throw 19;
        return std::thread(std::move(entry));
      });
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  force.configure(config, 0);
  bool escaped = false;
  try { force.start(); } catch (...) { escaped = true; }
  require(escaped && !force.running() && active.load() == 0 && force.safetyLatched() && stops.load() >= 2,
      "monitor creation failure must stop serial workers and preserve the safety latch");
}

void testFollowerFailureBoundary(bool standard) {
  LTDMCDriver motion;
  ForceControlRuntime force([]() {}, []() {});
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  force.configure(config, 0);
  std::atomic_int additionalStops{0};
  TeleopHardwareTargetExecutor executor(motion, force, [&](const char*) { ++additionalStops; throw 20; });
  std::thread worker([&]() {
    runWorkerBoundary([&]() { injectedFailure(standard); }, [&](const char* message) { executor.reportControlFailure(message); });
  });
  worker.join();
  require(motion.estopActive() && force.safetyLatched() && additionalStops.load() == 1,
      "follower callback fault must run all safety actions and contain failure of one action");
  bool rejected = false;
  try { motion.acknowledgeEmergencyStop(); } catch (...) { rejected = true; }
  require(rejected && !motion.controlLeaseFresh(), "failed follower cannot recover through ACK without restarting HAL");
}

void testExpiredCommandRejectedBeforeExecution() {
  int executions = 0;
  const auto execute = [&](const std::string& payload, double now) { ensureCommandNotExpired(payload, now); ++executions; };
  execute("{}", 1000);
  execute("{\"commandExpiresAtUnixMs\":1001}", 1000);
  for (const auto* payload : {"{\"commandExpiresAtUnixMs\":1000}", "{\"commandExpiresAtUnixMs\":999}",
      "{\"commandExpiresAtUnixMs\":1e999}", "{\"commandExpiresAtUnixMs\":\"bad\"}"}) {
    bool rejected = false;
    try { execute(payload, 1000); } catch (const std::runtime_error&) { rejected = true; }
    require(rejected, "expired or malformed command deadline must be rejected");
  }
  require(executions == 2, "rejected command must never reach execution");
}
}  // namespace

int main() {
  try {
    for (bool standard : {true, false}) {
      testMotionFailureAndRecovery(standard);
      testMotionThreadCreationFailure(standard);
      testSerialCallbackFailure(standard);
      testForceMonitorFailure(standard);
      testFollowerFailureBoundary(standard);
    }
    testMotionInvalidFrequency();
    testSerialPartialStartupFailure();
    testForceMonitorCreationFailure();
    testExpiredCommandRejectedBeforeExecution();
    std::cout << "WorkerResilienceTests passed (14 cases, no hardware or DDS runtime)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "WorkerResilienceTests failed: " << error.what() << "\n";
    return 1;
  }
}
