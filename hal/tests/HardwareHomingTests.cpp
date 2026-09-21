// 直接注入 SDK 函数指针覆盖真实回零分支；不调用 initialize、不加载 DLL 或连接设备。
#define APPSTATION_ENABLE_VENDOR_SDKS 1
#include "../src/LTDMCDriver.cpp"

#include <vector>

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
std::vector<unsigned short> started;
std::array<int, 12> polls{};
int stops = 0;
int failure = 0;
LTDMCDriver* current = nullptr;

void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
short __stdcall setPulse(unsigned short, unsigned short, unsigned short) { return 0; }
short __stdcall setEl(unsigned short, unsigned short, unsigned short, unsigned short, unsigned short) { return 0; }
short __stdcall setHome(unsigned short, unsigned short, unsigned short, double, unsigned short, unsigned short) { return 0; }
short __stdcall setLogic(unsigned short, unsigned short, unsigned short, double) { return 0; }
short __stdcall stop(unsigned short, unsigned short, unsigned short) { ++stops; return 0; }
short __stdcall start(unsigned short card, unsigned short axis) {
  require(card == 0, "wrong hardware side");
  started.push_back(axis);
  if (failure == 2 && started.size() == 2) return -1;
  return 0;
}
bool active(unsigned short axis) {
  return std::find(started.begin(), started.end(), axis) != started.end();
}
short __stdcall done(unsigned short, unsigned short axis) {
  if (!active(axis)) return 1;
  if (failure == 4) return -1;
  if (failure == 6) return 1; // 已停但未寻到原点，不能视作回零成功。
  return polls[axis] >= 3 ? 1 : 0;
}
long __stdcall position(unsigned short, unsigned short axis) {
  return active(axis) && polls[axis] >= 3 ? 1000 + axis : 90000 + axis;
}
short __stdcall homeResult(unsigned short, unsigned short axis, unsigned short* result) {
  ++polls[axis];
  if (failure == 1) return -1;
  if (failure == 3) current->latchEmergencyStop();
  if (failure == 5) current->failControlLease();
  *result = failure == 6 ? 0 : (polls[axis] >= 3 ? 1 : 0);
  return 0;
}
void setup(LTDMCDriver& motion, int mode = 0) {
  started.clear(); polls.fill(0); stops = 0; failure = mode; current = &motion;
  MotionExecutorTestAccess::initialize(motion);
  dmcSetPulseOutmode = setPulse; dmcSetElMode = setEl;
  dmcSetHomeMode = setHome; dmcSetHomePinLogic = setLogic;
  dmcHomeMove = start; dmcGetHomeResult = homeResult;
  dmcCheckDone = done; dmcGetPosition = position; dmcStop = stop;
}
constexpr std::array<bool, 6> rotations{false, false, false, true, true, true};
void succeedsOnlyAfterAllSelectedAxesHome() {
  LTDMCDriver motion; setup(motion);
  motion.homeSide(Side::Right, rotations);
  require(started == std::vector<unsigned short>{8, 1, 7}, "unselected axis moved");
  require(polls[8] >= 3 && polls[1] >= 3 && polls[7] >= 3, "returned before completion");
  const auto state = motion.readState();
  require(state.axes[9].pulse == 1008 && state.axes[10].pulse == 1001 && state.axes[11].pulse == 1007,
      "completion snapshot contains pre-home positions");
  require(stops == 0, "unexpected stop on success");
}
void rejectsAndStops(int mode) {
  LTDMCDriver motion; setup(motion, mode);
  bool rejected = false;
  try { motion.homeSide(Side::Right, rotations); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && stops > 0 && motion.estopActive(), "failed homing did not stop and latch");
}
void missingExportAndEmptyMaskNeverStart() {
  LTDMCDriver motion; setup(motion);
  dmcGetHomeResult = nullptr;
  bool rejected = false;
  try { motion.homeSide(Side::Right, rotations); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started.empty(), "missing completion export allowed motion");
  setup(motion); rejected = false;
  try { motion.homeSide(Side::Right, {}); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started.empty(), "empty selection allowed motion");
}
void allSixAxesCanHome() {
  LTDMCDriver motion; setup(motion);
  motion.homeSide(Side::Right, {true, true, true, true, true, true});
  require(started == std::vector<unsigned short>{2, 0, 5, 8, 1, 7}, "six-axis home selection incorrect");
  for (auto axis : started) require(polls[axis] >= 3, "six-axis home returned before completion");
}
}
int main() {
  try {
    succeedsOnlyAfterAllSelectedAxesHome();
    allSixAxesCanHome();
    missingExportAndEmptyMaskNeverStart();
    for (int mode = 1; mode <= 6; ++mode) rejectsAndStops(mode);
    std::cout << "HardwareHomingTests passed (SDK fakes only, including 60s timeout)" << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
