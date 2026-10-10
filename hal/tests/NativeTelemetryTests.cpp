// 仅注入内存状态：不加载 SDK、不连接 DDS、不启动硬件线程。
#include "NativeTeleopController.h"
#include <condition_variable>
#include <future>
#include <iostream>
#include <locale>
#include <stdexcept>

using namespace appstation::hal;
namespace appstation::hal {
struct NativeTeleopTelemetryTestAccess {
  static void seed(NativeTeleopController& controller, int count) {
    std::scoped_lock lock(controller.mutex_);
    controller.actionHistory_.clear();
    controller.hasLastAction_ = count > 0;
    for (int i = 0; i < count; ++i) {
      NativeTeleopAction a;
      a.ts = 700000001LL + i;
      a.monotonicS = 100.0 + i * .005;
      a.side = i % 2 ? Side::Right : Side::Left;
      a.sourceSide = i % 2 ? Side::Left : Side::Right;
      a.axisIndex = i % 6;
      a.delta = i * .01;
      for (int j = 0; j < 12; ++j) a.deltaVector[j] = (i % 7 - 3) * .001 * (j + 1);
      a.deltas[0] = a.delta;
      a.requestedDeltaPulse[0] = i + 1;
      controller.actionHistory_.push_back(a);
      controller.lastAction_ = a;
    }
    controller.gripperPositionSampleTs_ = {12345, 67890};
    controller.gripperPositionSampleMonotonicMs_ = {23456, 78901};
    controller.gripperPositionsMm_ = {1.2, 3.4};
    controller.gripperPositionOk_ = {true, false};
  }
  static bool lockAvailable(NativeTeleopController& controller) {
    std::unique_lock lock(controller.mutex_, std::try_to_lock);
    return lock.owns_lock();
  }
};
}
namespace {
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
struct Gate {
  std::mutex mutex;
  std::condition_variable cv;
  bool entered = false, released = false;
};
struct BlockingNumbers : std::num_put<char> {
  Gate& gate;
  explicit BlockingNumbers(Gate& value) : gate(value) {}
  iter_type do_put(iter_type out, std::ios_base& stream, char fill, long long value) const override {
    // lastAction 是最后一条；此值只在序列化第一条历史动作时出现。
    if (value == 700000001LL) {
      std::unique_lock lock(gate.mutex);
      gate.entered = true;
      gate.cv.notify_all();
      gate.cv.wait(lock, [&] { return gate.released; });
    }
    return std::num_put<char>::do_put(out, stream, fill, value);
  }
};
void testHistoryDoesNotHoldControlLock(NativeTeleopController& controller, bool compact) {
  Gate gate;
  const auto previous = std::locale();
  std::locale::global(std::locale(previous, new BlockingNumbers(gate)));
  auto task = std::async(std::launch::async, [&] {
    return compact ? controller.telemetryJson() : controller.statusJson();
  });
  bool entered;
  {
    std::unique_lock lock(gate.mutex);
    entered = gate.cv.wait_for(lock, std::chrono::seconds(2), [&] { return gate.entered; });
  }
  const bool unlocked = entered && NativeTeleopTelemetryTestAccess::lockAvailable(controller);
  {
    std::scoped_lock lock(gate.mutex);
    gate.released = true;
  }
  gate.cv.notify_all();
  const auto result = task.get();
  std::locale::global(previous);
  require(entered, "history serializer gate was not exercised");
  require(unlocked, "history serialization blocks control/feedback mutex");
  require(!result.empty(), "empty status");
}
}
int main(int argc, char** argv) {
  try {
    LTDMCDriver motion;
    MotionExecutor executor(motion);
    Omega7Driver omega;
    JodellGripperDriver gripper;
    NativeTeleopController controller(motion, executor, omega, gripper);
    NativeTeleopTelemetryTestAccess::seed(controller, 1000);
    const auto full = controller.statusJson();
    const auto compact = controller.telemetryJson();
    if (argc > 1 && std::string(argv[1]) == "--json") {
      std::cout << "{\"full\":" << full << ",\"compact\":" << compact << "}";
      return 0;
    }
    require(compact.size() < full.size() * .4, "live history payload remains too large");
    require(full.find("requestedDeltaPulse") != std::string::npos, "full diagnostics lost");
    testHistoryDoesNotHoldControlLock(controller, true);
    testHistoryDoesNotHoldControlLock(controller, false);
    auto benchmark = [&](bool live) {
      auto begin = std::chrono::steady_clock::now();
      for (int i = 0; i < 30; ++i) {
        auto result = live ? controller.telemetryJson() : controller.statusJson();
        require(!result.empty(), "empty benchmark result");
      }
      return std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - begin).count() / 30;
    };
    std::cout << "history=1000 fullBytes=" << full.size() << " compactBytes=" << compact.size()
              << " fullMeanMs=" << benchmark(false) << " compactMeanMs=" << benchmark(true) << "\n";
    NativeTeleopTelemetryTestAccess::seed(controller, 0);
    require(controller.telemetryJson().find("\"actionHistory\":[]") != std::string::npos,
            "empty history serialization invalid");
    std::cout << "NativeTelemetryTests passed: compact/full history releases control lock; size bounded; empty history\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << "\n";
    return 1;
  }
}
