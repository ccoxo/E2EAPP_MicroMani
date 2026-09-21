#include "OfflineMotion.h"
#include "HalCommandDispatcher.h"

#include <chrono>
#include <cmath>
#include <future>
#include <iostream>
#include <stdexcept>

using namespace appstation::hal;

namespace {
constexpr std::array<bool, 6> allAxes{true, true, true, true, true, true};
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
template <typename Action> void rejects(Action action) {
  try { action(); } catch (const std::runtime_error&) { return; }
  throw std::runtime_error("conflicting or stale operation was accepted");
}
struct Fixture {
  LTDMCDriver motion;
  MotionExecutor executor{motion};
  Fixture() {
    MotionExecutorTestAccess::initialize(motion);
    for (auto side : {Side::Left, Side::Right}) executor.enableSide(side, true, allAxes, epoch());
  }
  std::uint64_t epoch() { return motion.commandEpoch(); }
  void manual(Side side = Side::Left) {
    executor.moveRelativeUi(side, SemanticAxis::X, 1, 100, 10, .05, .05, epoch());
  }
  TeleopHardwareTarget target(std::uint64_t sequence, int side = 0) {
    TeleopHardwareTarget target;
    target.sequence = sequence;
    target.stampUnixMs = static_cast<std::uint64_t>(motion.lastEmergencyStopUnixMs() + 1);
    target.side = side;
    target.deltas[0] = 1;
    target.translationStepLimitPulse = 1000;
    target.rotationStepLimitPulse = 1000;
    target.softLimitMin.fill(-1000);
    target.softLimitMax.fill(1000);
    target.translationVelocityUiPerSec = 100;
    target.rotationVelocityUiPerSec = 1;
    return target;
  }
};

void nativeExcludesOtherSources() {
  Fixture f;
  f.executor.beginNative(0, f.epoch());
  rejects([&] { f.manual(); });
  rejects([&] { f.manual(Side::Right); });
  rejects([&] { f.executor.applyExternal(f.target(1), f.epoch()); });
  rejects([&] { f.executor.homeSide(Side::Left, allAxes, f.epoch()); });
  rejects([&] { f.executor.homeAll({}, {allAxes, allAxes}, f.epoch()); });
  auto target = f.target(1);
  require(f.executor.applyNative(target, target.deltas).has_value(), "active native target rejected");
  f.executor.endNative();
  f.manual();
}

void externalOwnershipIsPerSide() {
  Fixture f;
  f.executor.applyExternal(f.target(1), f.epoch());
  f.manual(Side::Right);
  rejects([&] { f.manual(); });
  rejects([&] { f.executor.beginNative(0, f.epoch()); });
  f.executor.stopSide(Side::Left);
  f.manual();
}

void lateAndDuplicateNativeTargetsAreDiscarded() {
  Fixture f;
  f.executor.beginNative(10, f.epoch());
  auto old = f.target(10);
  require(!f.executor.applyNative(old, old.deltas), "previous session target accepted");
  auto target = f.target(11);
  require(f.executor.applyNative(target, target.deltas).has_value(), "new target rejected");
  require(!f.executor.applyNative(target, target.deltas), "duplicate target accepted");
  auto right = f.target(12, 1);
  require(f.executor.applyNative(right, right.deltas).has_value(), "right target rejected");
  f.executor.stopNativeSide(Side::Left, 14);
  target.sequence = 13;
  require(!f.executor.applyNative(target, target.deltas), "target queued before clutch stop accepted");
  f.executor.endNative();
  f.manual();
  target.sequence = 15;
  require(!f.executor.applyNative(target, target.deltas), "stopped native source reclaimed control");
  f.executor.beginNative(15, f.epoch());
  require(!f.executor.applyNative(target, target.deltas), "restart revived old target");
}

void motionCompletionRequiredBeforeHandoff() {
  Fixture f;
  f.manual();
  MotionExecutorTestAccess::moving(f.motion, Side::Left, true);
  rejects([&] { f.executor.beginNative(0, f.epoch()); });
  rejects([&] { f.executor.applyExternal(f.target(1), f.epoch()); });
  MotionExecutorTestAccess::moving(f.motion, Side::Left, false);
  f.executor.beginNative(0, f.epoch());
}

void emergencyDoesNotRestoreOldOwnership() {
  Fixture f;
  f.executor.beginNative(0, f.epoch());
  const auto oldEpoch = f.epoch();
  f.motion.emergencyStop();
  auto target = f.target(1);
  require(!f.executor.applyNative(target, target.deltas), "latched target accepted");
  f.motion.acknowledgeEmergencyStop();
  require(!f.executor.applyNative(target, target.deltas), "ack restored native ownership");
  rejects([&] { f.executor.beginNative(1, oldEpoch); });
  f.executor.enableSide(Side::Left, true, allAxes, f.epoch());
  f.manual();
  require(f.executor.owners()[0] == MotionOwner::Manual, "fresh manual owner missing");
}

void stoppingInactiveNativeDoesNotStopExternal() {
  Fixture f;
  f.executor.applyExternal(f.target(1), f.epoch());
  f.executor.endNative();
  require(f.executor.owners()[0] == MotionOwner::External, "native.stop released external owner");
  rejects([&] { f.manual(); });
}

void dispatcherUsesSharedArbitration() {
  Fixture f;
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController native(f.motion, f.executor, omega, gripper);
  ForceControlRuntime force([] {}, [] {});
  const auto started = std::chrono::steady_clock::now();
  HalCommandDispatcher dispatcher(f.motion, f.executor, omega, native, force, started);
  native.start(false, false);
  rejects([&] { dispatcher.handle("motion.manual_axis_move", R"({"side":"left","axis":"X","step":1,"maxVelocityUiPerSec":100})"); });
  rejects([&] { dispatcher.handle("motion.teleop_target_update", R"({"side":"left","X":1})"); });
  dispatcher.handle("teleop.native.stop", "{}");
  dispatcher.handle("motion.manual_axis_move", R"({"side":"left","axis":"X","step":1,"maxVelocityUiPerSec":100})");
}

void dispatcherReferenceReturnStopsNativeAndRejectsWholeTurn() {
  Fixture f;
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController native(f.motion, f.executor, omega, gripper);
  ForceControlRuntime force([] {}, [] {});
  const auto started = std::chrono::steady_clock::now();
  HalCommandDispatcher dispatcher(f.motion, f.executor, omega, native, force, started);
  native.start(false, false);
  const auto result = dispatcher.handle("motion.return_home_reference",
      R"({"side":"right","pulse":[100,200,300,400,500,600],"enabledAxes":[true,true,true,true,true,true]})");
  require(result.find("referenceReturnCompleted") != std::string::npos, "return not confirmed");
  require(!native.running(), "native teleop still running after reference return");
  const auto before = f.motion.readState();
  rejects([&] { dispatcher.handle("motion.return_home_reference", R"({"side":"right","pulse":[1,2,3,4,5,6]})"); });
  rejects([&] { dispatcher.handle("motion.home_side", R"({"side":"right"})"); });
  rejects([&] { dispatcher.handle("motion.return_home_reference",
      R"({"side":"right","pulse":[900,800,700,600400,500,600],"enabledAxes":[true,true,true,true,true,true]})"); });
  const auto after = f.motion.readState();
  for (int i = 0; i < 12; ++i) require(before.axes[i].pulse == after.axes[i].pulse, "rejection moved another axis");
}

void busyExecutorRejectsInsteadOfQueueingAndCannotBlockEmergencyLatch() {
  Fixture f;
  auto held = MotionExecutorTestAccess::holdExecutor(f.executor);
  rejects([&] { f.manual(); });
  rejects([&] { f.executor.homeSide(Side::Left, allAxes, f.epoch()); });
  rejects([&] { f.executor.applyExternal(f.target(1), f.epoch()); });
  // 不等待执行器锁，模拟阻塞驱动调用期间撤销运动许可。
  f.motion.latchEmergencyStop();
  f.executor.revokeNative();
  require(f.motion.estopActive(), "executor lock blocked emergency latch");
}

void revokeRejectsAlreadyWaitingFollower() {
  Fixture f;
  f.executor.beginNative(0, f.epoch());
  auto held = MotionExecutorTestAccess::holdExecutor(f.executor);
  auto target = f.target(1);
  auto pending = std::async(std::launch::async, [&] {
    return f.executor.applyNative(target, target.deltas);
  });
  f.executor.revokeNative();
  held.unlock();
  require(!pending.get(), "waiting follower executed after control was revoked");
  f.executor.endNative();
  f.manual();
}

void dispatcherEmergencyBypassesBothExecutionAndDriverLocks() {
  Fixture f;
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController native(f.motion, f.executor, omega, gripper);
  ForceControlRuntime force([] {}, [] {});
  const auto started = std::chrono::steady_clock::now();
  HalCommandDispatcher dispatcher(f.motion, f.executor, omega, native, force, started);
  native.setLeaderStatePublisher([](const auto&) {});
  native.start(false, false);
  const auto oldEpoch = f.epoch();
  auto heldExecutor = MotionExecutorTestAccess::holdExecutor(f.executor);
  auto heldDriver = MotionExecutorTestAccess::holdDriver(f.motion);
  auto stopping = std::async(std::launch::async, [&] { return dispatcher.handleEmergencyStop(); });
  // 该时限用于发现锁依赖，不代表实机停机时间承诺。
  const bool completed = stopping.wait_for(std::chrono::seconds(1)) == std::future_status::ready;
  const bool revoked = f.motion.estopActive() && !native.running()
      && !f.motion.commandEpochAllowed(oldEpoch);
  heldDriver.unlock();
  heldExecutor.unlock();
  stopping.get();
  require(completed && revoked, "emergency stop waited for ordinary execution or driver state locks");
  rejects([&] { f.manual(); });
  auto target = f.target(1);
  f.motion.acknowledgeEmergencyStop();
  require(!f.executor.applyNative(target, target.deltas), "ack revived native source after emergency");
  rejects([&] { f.executor.moveRelativeUi(Side::Left, SemanticAxis::X, 1, 100, 10, .05, .05, oldEpoch); });
}

void emergencyCancelsCommandAlreadyAdmittedBeforeDriverAccess() {
  Fixture f;
  const auto oldEpoch = f.epoch();
  auto heldDriver = MotionExecutorTestAccess::holdDriver(f.motion);
  auto command = std::async(std::launch::async, [&] {
    try {
      f.executor.moveRelativeUi(Side::Left, SemanticAxis::X, 1, 100, 10, .05, .05, oldEpoch);
      return false;
    } catch (const std::runtime_error&) { return true; }
  });
  // 等待工作线程进入被 driver 锁阻塞的调用；持续 try_lock 轮询本身会使
  // 普通提交误判 executor 忙而提前拒绝，无法覆盖本用例要求的在途窗口。
  command.wait_for(std::chrono::milliseconds(100));
  const bool admitted = MotionExecutorTestAccess::executorBusy(f.executor);
  // 急停硬件路径不等待主状态锁；确认后，已捕获旧代际的请求仍必须失效。
  f.motion.emergencyStop();
  f.motion.acknowledgeEmergencyStop();
  heldDriver.unlock();
  const bool rejected = command.get();
  require(admitted && rejected, "emergency did not cancel an already-admitted command across acknowledgement");
  require(f.motion.readState().axes[0].uiPosition == 0, "cancelled command changed motion position");
}
}

void replayAbsoluteTargetsDoNotAccumulate() {
  Fixture f;
  auto target = f.target(1);
  target.enabledAxes.fill(true);
  target.deltas[0] = 2;
  const auto first = f.executor.applyExternal(target, f.epoch(), true);
  require(first.targetUi[0] > 0, "absolute target did not move the simulated axis");
  const auto repeated = f.executor.applyExternal(target, f.epoch(), true);
  require(std::abs(first.targetUi[0] - repeated.targetUi[0]) < 0.001,
      "repeated absolute target accumulated displacement");
  target.deltas[0] = -1;
  const auto reverse = f.executor.applyExternal(target, f.epoch(), true);
  require(reverse.targetUi[0] <= first.targetUi[0], "absolute reverse moved forward");
  f.motion.emergencyStop();
  rejects([&] { f.executor.applyExternal(target, f.epoch(), true); });
}

int main() {
  try {
    replayAbsoluteTargetsDoNotAccumulate();
    nativeExcludesOtherSources();
    externalOwnershipIsPerSide();
    lateAndDuplicateNativeTargetsAreDiscarded();
    motionCompletionRequiredBeforeHandoff();
    emergencyDoesNotRestoreOldOwnership();
    stoppingInactiveNativeDoesNotStopExternal();
    dispatcherUsesSharedArbitration();
    dispatcherReferenceReturnStopsNativeAndRejectsWholeTurn();
    busyExecutorRejectsInsteadOfQueueingAndCannotBlockEmergencyLatch();
    revokeRejectsAlreadyWaitingFollower();
    dispatcherEmergencyBypassesBothExecutionAndDriverLocks();
    emergencyCancelsCommandAlreadyAdmittedBeforeDriverAccess();
    std::cout << "MotionExecutorTests passed (13 cases, offline)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "MotionExecutorTests failed: " << error.what() << '\n';
    return 1;
  }
}
