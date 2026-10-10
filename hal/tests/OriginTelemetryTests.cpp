// SDK 函数指针替身：不加载 DLL，不连接设备，不执行真实运动。
#define APPSTATION_ENABLE_VENDOR_SDKS 1
#include "../src/LTDMCDriver.cpp"
#include <future>
#include <iostream>

namespace appstation::hal {
struct MotionExecutorTestAccess {
  static void initialize(LTDMCDriver& motion) {
    motion.initialized_ = true;
    motion.enabled_.fill(true);
    motion.commandedEnabled_.fill(true);
  }
};
}
using namespace appstation::hal;
using namespace std::chrono_literals;
namespace {
constexpr std::array<bool, 6> oneAxis{true, false, false, false, false, false};
std::chrono::steady_clock::time_point finishAt;
bool started = false;
bool preMove = false;
int moves = 0;
long target = 1000;
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
short __stdcall profile(unsigned short, unsigned short, double, double, double, double, double) { return 0; }
short __stdcall done(unsigned short, unsigned short) {
  return (started || preMove) && std::chrono::steady_clock::now() < finishAt ? 0 : 1;
}
long __stdcall position(unsigned short card, unsigned short axis) {
  if (card != 0 || axis != 2) return 0;
  if (!started) return 0;
  return std::chrono::steady_clock::now() < finishAt ? 123 : target;
}
short __stdcall move(unsigned short, unsigned short, long pulse, unsigned short) {
  ++moves;
  target = pulse;
  started = true;
  finishAt = std::chrono::steady_clock::now() + 350ms;
  return 0;
}
short __stdcall stop(unsigned short, unsigned short, unsigned short) { return 0; }

void exercise(bool both, bool waitingBeforeMove, bool cancel) {
  LTDMCDriver motion;
  MotionExecutorTestAccess::initialize(motion);
  started = false; preMove = waitingBeforeMove; moves = 0; target = 1000;
  finishAt = std::chrono::steady_clock::now() + 350ms;
  dmcSetProfile = profile; dmcPMove = move; dmcCheckDone = done;
  dmcGetPosition = position; dmcStop = stop;
  const auto initial = motion.readState();
  auto returning = std::async(std::launch::async, [&]() {
    if (both) {
      std::array<double, 12> origin{}; origin[6] = 1000;
      motion.homeAll(origin, {std::array<bool, 6>{}, oneAxis});
    } else {
      motion.homeOriginSide(Side::Right, {1000, 0, 0, 0, 0, 0}, oneAxis);
    }
  });
  int freshUpdates = 0;
  bool measuredIntermediate = false;
  bool movingSeen = false;
  auto lastStamp = initial.readTimestampMs;
  const auto until = std::chrono::steady_clock::now() + 200ms;
  while (std::chrono::steady_clock::now() < until) {
    const auto sample = motion.latestState();
    if (sample.readTimestampMs > lastStamp) {
      ++freshUpdates;
      lastStamp = sample.readTimestampMs;
      movingSeen = movingSeen || sample.axes[6].moving;
      measuredIntermediate = measuredIntermediate || sample.axes[6].pulse == (waitingBeforeMove ? 0 : 123);
    }
    std::this_thread::sleep_for(5ms);
  }
  if (cancel) motion.latchEmergencyStop();
  bool rejected = false;
  try { returning.get(); } catch (const std::runtime_error&) { rejected = true; }
  require(freshUpdates >= 3, "return-to-origin starved measured state updates while holding driver lock");
  require(movingSeen && measuredIntermediate, "origin telemetry did not publish measured moving position");
  if (cancel) {
    require(rejected && motion.estopActive(), "emergency cancellation was bypassed");
    if (waitingBeforeMove) require(moves == 0, "cancelled pre-move wait started an axis");
  } else {
    require(!rejected, "normal origin return failed");
    const auto final = motion.latestState();
    require(final.readTimestampMs > 0 && !final.axes[6].moving && final.axes[6].pulse == 1000,
        "completed return invalidated the final measured snapshot");
  }
}
}
int main() {
  try {
    exercise(false, false, false);
    exercise(true, false, false);
    exercise(false, true, false);
    exercise(false, false, true);
    exercise(true, true, true);
    std::cout << "OriginTelemetryTests passed (5 SDK-fake cases)" << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
