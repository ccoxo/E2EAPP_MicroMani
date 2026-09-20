#include "ControlLeaseGuardian.h"
#include "HalCommandDispatcher.h"
#include "JodellGripperDriver.h"

#include <atomic>
#include <iostream>
#include <stdexcept>

using namespace appstation::hal;

namespace {
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
template<class Callback> void rejects(Callback callback, const char* message) {
  try { callback(); } catch (const std::runtime_error&) { return; }
  throw std::runtime_error(message);
}

void testInitiallyClosed() {
  EmergencyStopState stop;
  ControlLeaseState lease;
  lease.require(stop);
  require(stop.active() && !lease.fresh(), "startup must be latched without a lease");
  rejects([&] { lease.acknowledge(stop, stop.epoch()); }, "ack without lease must fail");
  lease.renew(stop, "backend-a", 1, ControlLeaseState::nowMs());
  require(stop.active(), "first heartbeat must not acknowledge stop");
  rejects([&] { lease.acknowledge(stop, stop.epoch()); }, "pending physical stop must block ack");
  lease.completeStop(lease.poll(stop));
  lease.acknowledge(stop, stop.epoch());
  require(!stop.active(), "explicit current acknowledgement should succeed");
}

void testExpiryBeforeLateHeartbeat() {
  EmergencyStopState stop;
  ControlLeaseState lease;
  const auto now = ControlLeaseState::nowMs();
  lease.require(stop);
  lease.renew(stop, "backend-a", 1, now);
  lease.completeStop(lease.poll(stop, now));
  lease.acknowledge(stop, stop.epoch());
  const auto command = stop.epoch();
  // 故意不调用 watchdog；迟到的续租自身必须观察已经到期的旧期限。
  lease.renew(stop, "backend-a", 2, now + ControlLeaseState::kTimeoutMs);
  require(stop.active() && !stop.permits(command), "late heartbeat must latch before renewing");
  rejects([&] { lease.acknowledge(stop, command); }, "old ack cannot revive expired command");
}

void testPollAndNoRepeatedTrip() {
  EmergencyStopState stop;
  ControlLeaseState lease;
  const auto now = ControlLeaseState::nowMs();
  lease.require(stop);
  lease.renew(stop, "backend-a", 1, now);
  lease.completeStop(lease.poll(stop, now));
  require(lease.poll(stop, now + 2499) == 0, "must not expire early");
  require(lease.poll(stop, now + 2500) != 0, "deadline must be exclusive");
  const auto epoch = stop.epoch();
  lease.poll(stop, now + 3000);
  require(stop.epoch() == epoch, "same expiration should only trip once");
  require(!lease.fresh(now + 2500), "expired deadline must deny execution");
}

void testReplayAndOwnership() {
  EmergencyStopState stop;
  ControlLeaseState lease;
  const auto now = ControlLeaseState::nowMs();
  lease.require(stop);
  lease.renew(stop, "backend-a", 2, now);
  rejects([&] { lease.renew(stop, "backend-a", 2, now + 1); }, "duplicate sequence must fail");
  rejects([&] { lease.renew(stop, "backend-a", 1, now + 1); }, "old sequence must fail");
  rejects([&] { lease.renew(stop, "backend-b", 1, now + 1); }, "live owner cannot be replaced");
  lease.renew(stop, "backend-b", 1, now + 2500);
  require(stop.active(), "new owner after expiry must remain latched");
}

void testGuardianFailureCannotRecoverByHeartbeat() {
  EmergencyStopState stop;
  ControlLeaseState lease;
  lease.require(stop);
  lease.renew(stop, "backend-a", 1, ControlLeaseState::nowMs());
  lease.completeStop(lease.poll(stop));
  lease.fail(stop);
  rejects([&] { lease.renew(stop, "backend-a", 2, ControlLeaseState::nowMs()); }, "failed guardian must reject renewals");
  rejects([&] { lease.acknowledge(stop, stop.epoch()); }, "failed guardian cannot be acknowledged");
  require(stop.active() && !lease.fresh(), "guardian failure must be permanent for this runtime");
}

void testNewTripNotCompletedByOldStop() {
  EmergencyStopState stop;
  ControlLeaseState lease;
  const auto now = ControlLeaseState::nowMs();
  lease.require(stop);
  lease.renew(stop, "backend-a", 1, now);
  const auto initial = lease.poll(stop, now);
  lease.poll(stop, now + 2500);
  lease.completeStop(initial);
  require(lease.poll(stop, now + 2501) > initial, "old stop completion cannot clear new pending stop");
}

std::string payload(std::int64_t issuedAt, int sequence = 1) {
  return "{\"sessionId\":\"backend-a\",\"sequence\":" + std::to_string(sequence)
      + ",\"timeoutMs\":2500,\"issuedAtUnixMs\":" + std::to_string(issuedAt) + "}";
}

void testDispatcherRejectsStaleAndMalformedLease() {
  LTDMCDriver motion;
  MotionExecutor motionExecutor(motion);
  Omega7Driver omega;
  JodellGripperDriver gripper;
  NativeTeleopController native(motion, motionExecutor, omega, gripper);
  ForceControlRuntime force([] {}, [] {});
  ForceRuntimeConfig forceConfig;
  forceConfig.source = "nidaq";
  force.configure(forceConfig, 0.0);
  const auto started = std::chrono::steady_clock::now();
  HalCommandDispatcher dispatcher(motion, motionExecutor, omega, native, force, started);
  motion.requireControlLease();
  const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count();
  rejects([&] { dispatcher.handle("control.lease", payload(now - 2000)); }, "stale lease must fail");
  rejects([&] { dispatcher.handle("control.lease", payload(now + 2000)); }, "future lease must fail");
  rejects([&] { dispatcher.handle("control.lease", "{}"); }, "missing identity must fail");
  require(dispatcher.handle("control.lease", payload(now)).find("leaseFresh\":true") != std::string::npos,
      "fresh lease must be explicitly acknowledged");
  rejects([&] { dispatcher.handle("motion.acknowledge_estop", "{}"); }, "pending stop must block dispatcher ack");
  const auto stop = motion.pollControlLease();
  dispatcher.handleEmergencyStop();
  motion.completeControlLeaseStop(stop);
  dispatcher.handle("motion.acknowledge_estop", "{}");
  require(motion.commandEpochAllowed(motion.commandEpoch()), "fresh lease and explicit ack should permit new commands");
}

void testIndependentGuardianAndShutdown() {
  LTDMCDriver motion;
  std::atomic_int stops{0};
  ControlLeaseGuardian guardian(motion, [&] { motion.emergencyStop(); ++stops; });
  guardian.start();
  for (int i = 0; i < 100 && stops.load() == 0; ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  require(stops.load() != 0 && motion.estopActive(), "guardian must stop independently of DDS polling");
  guardian.stop();
  rejects([&] { motion.renewControlLease("backend-a", 1); }, "shutdown guardian cannot be renewed");
}

void testBlockedShutdownRevokesBeforeWaiting() {
  LTDMCDriver motion;
  std::atomic_int calls{0};
  std::atomic_bool release{false};
  ControlLeaseGuardian guardian(motion, [&] {
    if (++calls > 1) while (!release.load()) std::this_thread::yield();
    motion.emergencyStop();
  });
  guardian.start();
  for (int i = 0; i < 100 && (calls.load() == 0 || motion.pollControlLease() != 0); ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  motion.renewControlLease("backend-a", 1);
  std::thread closing([&] { guardian.stop(); });
  for (int i = 0; i < 100 && calls.load() < 2; ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  bool denied = false;
  try { motion.renewControlLease("backend-a", 2); } catch (const std::runtime_error&) { denied = true; }
  release.store(true);
  closing.join();
  require(denied, "blocked shutdown must revoke renewals before waiting on SDK or join");
}
}

int main() {
  try {
    testInitiallyClosed();
    testExpiryBeforeLateHeartbeat();
    testPollAndNoRepeatedTrip();
    testReplayAndOwnership();
    testGuardianFailureCannotRecoverByHeartbeat();
    testNewTripNotCompletedByOldStop();
    testDispatcherRejectsStaleAndMalformedLease();
    testIndependentGuardianAndShutdown();
    testBlockedShutdownRevokesBeforeWaiting();
    std::cout << "ControlLeaseTests passed (9 cases)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
