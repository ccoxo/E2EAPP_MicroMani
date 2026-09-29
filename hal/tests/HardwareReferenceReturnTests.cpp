// 注入 SDK 替身验证真实执行分支；不加载 DLL 或连接设备。
#define APPSTATION_ENABLE_VENDOR_SDKS 1
#include "../src/LTDMCDriver.cpp"

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
namespace {
constexpr std::array<bool, 6> allAxes{true, true, true, true, true, true};
constexpr std::array<int, 6> axes{2, 0, 5, 8, 1, 7};
constexpr std::array<double, 6> reference{-55, 6060, 49, -2884800, -900027, 12};
std::array<long, 12> positions{};
int moves = 0, homes = 0, stops = 0;
int failure = 0;
LTDMCDriver* current = nullptr;
void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
short __stdcall profile(unsigned short, unsigned short, double, double, double, double, double) { return 0; }
short __stdcall done(unsigned short, unsigned short) { return 1; }
long __stdcall position(unsigned short, unsigned short axis) { return positions[axis]; }
short __stdcall home(unsigned short, unsigned short) { ++homes; return 0; }
short __stdcall stop(unsigned short, unsigned short, unsigned short) { ++stops; return 0; }
short __stdcall move(unsigned short card, unsigned short axis, long pulse, unsigned short mode) {
  require(card == 0, "incorrect hardware side");
  ++moves;
  if (failure == 1 && moves >= 2) return -1;
  if (failure == 2) current->latchEmergencyStop();
  if (failure == 3) current->failControlLease();
  positions[axis] = mode == 1 ? pulse : positions[axis] + pulse;
  return 0;
}
void setup(LTDMCDriver& motion) {
  MotionExecutorTestAccess::initialize(motion);
  current = &motion; moves = homes = stops = failure = 0;
  positions.fill(0);
  for (int i = 0; i < 6; ++i) positions[axes[i]] = static_cast<long>(reference[i]) + 200;
  dmcSetProfile = profile; dmcPMove = move; dmcCheckDone = done;
  dmcGetPosition = position; dmcHomeMove = home; dmcStop = stop;
}
void returnsWithoutSeekingAndIsIdempotent() {
  LTDMCDriver motion; setup(motion);
  motion.homeOriginSide(Side::Right, reference, allAxes, std::nullopt, true);
  require(moves == 6 && homes == 0, "reference return sought mechanical origin");
  for (int i = 0; i < 6; ++i) require(positions[axes[i]] == reference[i], "wrong absolute target");
  motion.homeOriginSide(Side::Right, reference, allAxes, std::nullopt, true);
  require(moves == 6 && homes == 0, "already-at-reference return moved again");
}
void rejectsLargeRotationBeforeAnyAxisMoves() {
  for (int axisIndex = 3; axisIndex < 6; ++axisIndex) {
    for (int sign : {-1, 1}) {
      LTDMCDriver motion; setup(motion);
      // 三个轴均超过一圈；不得按360度取模绕过检查。
      positions[axes[axisIndex]] += sign * 1000000;
      bool rejected = false;
      try { motion.homeOriginSide(Side::Right, reference, allAxes, std::nullopt, true); }
      catch (const std::runtime_error&) { rejected = true; }
      require(rejected && moves == 0 && homes == 0, "large rotation allowed other axes to start");
    }
  }
}
void failedPartialMoveStopsAllAxes() {
  for (int mode : {1, 2, 3}) {
    LTDMCDriver motion; setup(motion); failure = mode;
    bool rejected = false;
    try { motion.homeOriginSide(Side::Right, reference, allAxes, std::nullopt, true); }
    catch (const std::runtime_error&) { rejected = true; }
    require(rejected && moves > 0 && stops > 0 && motion.estopActive(), "failure did not stop and latch");
  }
}
void selectedReturnLeavesOtherAxesUntouched() {
  LTDMCDriver motion; setup(motion);
  positions[axes[3]] += 1000000;
  const auto before = positions;
  const std::array<bool, 6> pitchOnly{false, false, false, false, true, false};
  motion.homeOriginSide(Side::Right, reference, pitchOnly, std::nullopt, true);
  require(moves == 1 && homes == 0, "selected return started extra axes");
  for (int i = 0; i < 6; ++i) {
    require(positions[axes[i]] == (i == 4 ? reference[i] : before[axes[i]]), "unselected position changed");
  }
  bool rejected = false;
  try { motion.homeOriginSide(Side::Right, reference, {}, std::nullopt, true); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && moves == 1, "empty return selection was accepted");
}
}
int main() {
  try {
    returnsWithoutSeekingAndIsIdempotent();
    rejectsLargeRotationBeforeAnyAxisMoves();
    failedPartialMoveStopsAllAxes();
    selectedReturnLeavesOtherAxesUntouched();
    std::cout << "HardwareReferenceReturnTests passed (SDK fakes only)" << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
