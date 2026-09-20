#include "OfflineMotion.h"
#include "EmergencyStopState.h"
#include "ForceControlRuntime.h"
#include "HalCommandDispatcher.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"
#include "TeleopHardwareTargetExecutor.h"

#include <barrier>
#include <future>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <thread>

using namespace appstation::hal;

namespace {
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

void testStopEpoch() {
  EmergencyStopState state;
  const auto oldCommand = state.epoch();
  state.trip();
  const auto firstStop = state.epoch();
  state.trip();
  require(!state.acknowledge(firstStop), "old acknowledgement must not clear a newer stop");
  require(state.active(), "new stop must remain active");
  require(state.acknowledge(state.epoch()), "current stop can be acknowledged");
  require(!state.permits(oldCommand), "acknowledgement must not revive an old queued command");
  require(state.permits(state.epoch()), "new command can use the current cleared epoch");

  for (int i = 0; i < 200; ++i) {
    state.trip();
    const auto acknowledgedStop = state.epoch();
    std::barrier gate(2);
    std::thread newerStop([&]() { gate.arrive_and_wait(); state.trip(); });
    gate.arrive_and_wait();
    state.acknowledge(acknowledgedStop);
    newerStop.join();
    require(state.active(), "concurrent new stop must win over old acknowledgement");
  }
}

void testMotionAcknowledgementEpoch() {
  LTDMCDriver motion;
  motion.emergencyStop();
  const auto oldStop = motion.commandEpoch();
  motion.emergencyStop();
  bool rejected = false;
  try { motion.acknowledgeEmergencyStop(oldStop); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && motion.estopActive(), "motion driver must reject stale acknowledgement");
  motion.acknowledgeEmergencyStop();
  require(!motion.estopActive(), "current acknowledgement should clear only the latch");
  require(!motion.commandEpochAllowed(oldStop), "acknowledgement must not revive the old command epoch");
}

void testRejectedMotionAcknowledgementKeepsForceLatch() {
  ForceControlRuntime runtime([]() {}, []() {});
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  config.safety.watchdogMs = 1000.0;
  runtime.configure(config, 0.0);
  runtime.acceptSample(0, {}, {}, 1.0, 1);
  runtime.acceptSample(1, {}, {}, 1.0, 1);
  runtime.acceptSample(0, {}, {}, 501.0, 501);
  runtime.acceptSample(1, {}, {}, 501.0, 501);
  bool rejected = false;
  try {
    runtime.acknowledgeEmergencyStop(501.0, []() { throw std::runtime_error("new stop"); });
  } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && runtime.safetyLatched(), "failed motion acknowledgement must retain force safety latch");
}

void testNativeStopRestartAndGripperGate() {
  LTDMCDriver motion;
  MotionExecutor motionExecutor(motion);
  Omega7Driver omega;
  JodellGripperDriver gripper;
  MotionExecutorTestAccess::initialize(motion);
  NativeTeleopController teleop(motion, motionExecutor, omega, gripper);
  // 不初始化硬件、不打开夹爪 worker；这里只验证控制线程生命周期。
  for (int i = 0; i < 10; ++i) {
    teleop.start(false, false);
    teleop.requestEmergencyStop();
    teleop.start(false, false);
    teleop.requestEmergencyStop();
    teleop.stop();
  }
  motion.emergencyStop();
  bool rejected = false;
  try { teleop.start(false, false); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && !teleop.running(), "native teleop must not restart while stop is latched");
  std::string message;
  require(!teleop.commandGripperTarget(Side::Left, 1.02, 1, 1, &message),
      "gripper commands must be blocked before reaching a driver while stopped");
  require(message.find("emergency stop") != std::string::npos, "gripper rejection should identify safety latch");
  require(!gripper.commandTarget(Side::Left, 1.02, 1, 1, &message, false, []() { return false; }),
      "cancelled gripper command must not load SDK or create a worker");
  require(message.find("cancelled") != std::string::npos, "driver must check cancellation before device access");
  motion.acknowledgeEmergencyStop();
  rejected = false;
  const auto oldCommand = motion.commandEpoch();
  motion.emergencyStop();
  motion.acknowledgeEmergencyStop();
  try { teleop.start(false, false, oldCommand); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && !teleop.running(), "acknowledgement must not revive an older native start");
  require(!teleop.commandGripperTarget(Side::Left, 1.02, 1, 1, &message, oldCommand),
      "acknowledgement must not revive an older gripper command");
  NativeTeleopConfig oldConfig;
  oldConfig.leftGravityCompensation = true;
  rejected = false;
  try { teleop.configure(oldConfig, oldCommand); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected, "configuration must not revive Omega force output from an older command");
  teleop.start(false, false);
  teleop.requestEmergencyStop();
  // 析构必须回收已由急停退出但仍 joinable 的线程。
}

void testFollowerDiscardsPreStopTarget() {
  LTDMCDriver motion;
  MotionExecutor motionExecutor(motion);
  ForceControlRuntime force([]() {}, []() {});
  TeleopHardwareTargetExecutor executor(motion, motionExecutor, force);
  TeleopHardwareTarget oldTarget{};
  motion.emergencyStop();
  oldTarget.stampUnixMs = motion.lastEmergencyStopUnixMs();
  motion.acknowledgeEmergencyStop();
  executor.apply(oldTarget);  // 若进入未初始化的运动驱动会抛错；旧目标必须提前丢弃。
}

void testOmegaForceGate() {
  LTDMCDriver motion;
  Omega7Driver omega;
  const auto oldCommand = motion.commandEpoch();
  omega.setGravityCompensation(true, true, 0.45, 1.0,
      [&]() { return motion.commandEpochAllowed(oldCommand); });
  motion.emergencyStop();
  omega.requestEmergencyStop();
  require(omega.forceOutputEnabled() == std::array<bool, 2>{false, false}, "stop must disable both Omega force outputs");
  motion.acknowledgeEmergencyStop();
  bool rejected = false;
  try {
    omega.setGravityCompensation(true, false, 0.45, 1.0,
        [&]() { return motion.commandEpochAllowed(oldCommand); });
  } catch (const std::runtime_error&) { rejected = true; }
  require(rejected, "old force enable must stay invalid after acknowledgement");
  omega.setGravityCompensation(false, false, 0.45, 1.0, []() { return false; });
  require(omega.forceOutputEnabled() == std::array<bool, 2>{false, false}, "all-off must remain allowed");
  const auto newCommand = motion.commandEpoch();
  omega.setGravityCompensation(true, false, 0.45, 1.0,
      [&]() { return motion.commandEpochAllowed(newCommand); });
  require(omega.forceOutputEnabled() == std::array<bool, 2>{true, false}, "explicit new force enable may restore only the requested side");
}

void testInvalidMotionNumbersRejectedBeforeDeviceAccess() {
  LTDMCDriver motion;
  MotionExecutor motionExecutor(motion);
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController teleop(motion, motionExecutor, omega, gripper);
  const auto rejectedAsInvalid = [](auto command) {
    try { command(); }
    catch (const std::runtime_error& error) {
      const std::string message = error.what();
      return message.find("finite") != std::string::npos || message.find("integer range") != std::string::npos;
    }
    return false;
  };
  for (const auto invalid : {std::numeric_limits<double>::quiet_NaN(),
      std::numeric_limits<double>::infinity(), -std::numeric_limits<double>::infinity()}) {
    require(rejectedAsInvalid([&]() { motion.moveRelativeUi(Side::Left, SemanticAxis::X, invalid, 100.0); }),
        "non-finite jog must fail before hardware initialization or SDK access");
    std::array<double, 6> deltas{};
    deltas[0] = invalid;
    require(rejectedAsInvalid([&]() {
      motion.updateTeleopTargetUi(Side::Left, deltas, 10, 10, 0, 0, {}, false, {}, 100, 1);
    }), "non-finite DDS target must fail before SDK access");
    std::string message;
    require(!teleop.commandGripperTarget(Side::Left, invalid, 1, 1, &message),
        "non-finite gripper target must fail before SDK access");
  }
  std::array<double, 12> origin{};
  origin[0] = 1e100;
  require(rejectedAsInvalid([&]() { motion.homeAll(origin, {}); }),
      "work origin must fit the controller integer pulse range");
}

void testAcknowledgementCannotOvertakeForceStopBookkeeping() {
  LTDMCDriver motion;
  MotionExecutor motionExecutor(motion);
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController teleop(motion, motionExecutor, omega, gripper);
  ForceControlRuntime force([]() {}, []() {});
  ForceControlRuntimeTestAccess::markCalibrationReady(force);
  const auto started = std::chrono::steady_clock::now();
  HalCommandDispatcher dispatcher(motion, motionExecutor, omega, teleop, force, started);
  std::promise<void> forceLockHeld;
  std::promise<void> releaseForceLock;
  auto release = releaseForceLock.get_future();
  std::thread forceOperation([&]() {
    force.acknowledgeEmergencyStop(0.0, [&]() {
      forceLockHeld.set_value();
      release.wait();
    });
  });
  forceLockHeld.get_future().wait();
  std::thread stopping([&]() { dispatcher.handleEmergencyStop(); });
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (!motion.estopActive() && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
  auto acknowledgement = std::async(std::launch::async, [&]() {
    try { dispatcher.handle("motion.acknowledge_estop", "{}"); }
    catch (const std::runtime_error&) { return true; }
    return false;
  });
  const bool rejectedPromptly = acknowledgement.wait_for(std::chrono::milliseconds(100)) == std::future_status::ready;
  releaseForceLock.set_value();
  forceOperation.join();
  stopping.join();
  const bool rejected = acknowledgement.get();
  require(rejectedPromptly && rejected && motion.estopActive(),
      "acknowledgement must not overtake the force bookkeeping part of an emergency stop");
}

void testTareHonorsCancellationBeforeDriverAccess() {
  LTDMCDriver motion;
  ForceControlRuntime force([]() {}, []() {});
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  config.safety.watchdogMs = 1000.0;
  force.configure(config, 0.0);
  bool rejected = false;
  try { force.tare(-1, 200, []() { return false; }); }
  catch (const std::runtime_error& error) {
    rejected = std::string(error.what()).find("cancelled by emergency stop") != std::string::npos;
  }
  require(rejected, "cancelled Tare must be refused before reaching the unstarted serial driver");
  force.acceptSample(0, {}, {}, 1.0, 1);
  force.acceptSample(1, {}, {}, 1.0, 1);
  force.acceptSample(0, {}, {}, 501.0, 501);
  force.acceptSample(1, {}, {}, 501.0, 501);
  ForceControlRuntimeTestAccess::markCalibrationReady(force);
  force.acknowledgeEmergencyStop(501.0);
  require(!force.safetyLatched(), "test setup should release the force latch using safe samples");
  motion.emergencyStop();
  const auto oldEpoch = motion.commandEpoch();
  motion.acknowledgeEmergencyStop();
  rejected = false;
  try { force.tare(-1, 200, [&]() { return motion.commandEpochAllowed(oldEpoch); }); }
  catch (const std::runtime_error& error) {
    rejected = std::string(error.what()).find("cancelled by emergency stop") != std::string::npos;
  }
  require(rejected, "an old Tare request must remain invalid after acknowledgement");
}
}  // namespace

int main() {
  try {
    testStopEpoch();
    testMotionAcknowledgementEpoch();
    testRejectedMotionAcknowledgementKeepsForceLatch();
    testNativeStopRestartAndGripperGate();
    testFollowerDiscardsPreStopTarget();
    testOmegaForceGate();
    testInvalidMotionNumbersRejectedBeforeDeviceAccess();
    testAcknowledgementCannotOvertakeForceStopBookkeeping();
    testTareHonorsCancellationBeforeDriverAccess();
    std::cout << "EmergencyStopTests passed (9 cases, including 200 concurrent stop/ack races)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "EmergencyStopTests failed: " << error.what() << "\n";
    return 1;
  }
}
