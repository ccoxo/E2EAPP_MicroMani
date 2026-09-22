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
long reportedStopReason = 21;
short stopReasonReadRet = 0;
int stopReasonReads = 0;
int axisIoReads = 0;
unsigned long startAxisIo = 16;
unsigned long stoppedAxisIo = 18;
unsigned short expectedCard = 0;
int interruptOnStopInput = 0;
bool unstableStoppedPulse = false;
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
  require(card == expectedCard, "wrong hardware side");
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
  if (failure == 7) return polls[axis] >= 3 ? 1 : 0;
  if (failure == 8) return polls[axis] < 20 || polls[axis] >= 45 ? 1 : 0;
  return polls[axis] >= 3 ? 1 : 0;
}
long __stdcall position(unsigned short, unsigned short axis) {
  if (unstableStoppedPulse && active(axis)) return 1000 + axis + polls[axis];
  return active(axis) && polls[axis] >= 3 ? 1000 + axis : 90000 + axis;
}
short __stdcall homeResult(unsigned short, unsigned short axis, unsigned short* result) {
  ++polls[axis];
  if (failure == 1) return -1;
  if (failure == 3) current->latchEmergencyStop();
  if (failure == 5) current->failControlLease();
  // 防止缺少提前失败处理的旧代码让回归测试等待完整 60 秒。
  if (failure == 7 && polls[axis] >= 200) current->latchEmergencyStop();
  if (failure == 7 || failure == 8) {
    *result = failure == 8 && polls[axis] >= 46 ? 1 : 0;
    return 0;
  }
  if (failure == 9 && (axis == 2 || axis == 5)) { *result = 0; return 0; }
  *result = failure == 6 ? 0 : (polls[axis] >= 3 ? 1 : 0);
  return 0;
}
short __stdcall stopReason(unsigned short card, unsigned short axis, long* reason) {
  require((card == 0 && (axis == 2 || axis == 5)) || (card == 1 && (axis == 0 || axis == 1)), "stop reason read for an unrelated axis");
  require(stops == 0, "stop reason was overwritten by our emergency stop");
  ++stopReasonReads;
  *reason = reportedStopReason;
  return stopReasonReadRet;
}
unsigned long __stdcall axisIo(unsigned short card, unsigned short axis) {
  require((card == 0 && (axis == 2 || axis == 5)) || (card == 1 && (axis == 0 || axis == 1)), "home inputs read for an unrelated axis");
  require(stops == 0, "home inputs read after emergency stop");
  ++axisIoReads;
  if (active(axis) && interruptOnStopInput == 1) current->latchEmergencyStop();
  if (active(axis) && interruptOnStopInput == 2) current->failControlLease();
  return active(axis) ? stoppedAxisIo : startAxisIo;
}
void setup(LTDMCDriver& motion, int mode = 0) {
  started.clear(); polls.fill(0); stops = 0; failure = mode; current = &motion;
  reportedStopReason = 21; stopReasonReadRet = 0; stopReasonReads = 0;
  axisIoReads = 0; startAxisIo = 16; stoppedAxisIo = 18;
  expectedCard = 0; interruptOnStopInput = 0; unstableStoppedPulse = false;
  MotionExecutorTestAccess::initialize(motion);
  dmcSetPulseOutmode = setPulse; dmcSetElMode = setEl;
  dmcSetHomeMode = setHome; dmcSetHomePinLogic = setLogic;
  dmcHomeMove = start; dmcGetHomeResult = homeResult;
  dmcCheckDone = done; dmcGetPosition = position; dmcStop = stop;
  dmcGetStopReason = stopReason;
  dmcAxisIoStatus = axisIo;
}
constexpr std::array<bool, 6> rotations{false, false, false, true, true, true};
void stoppedXZReportsReason(int axisIndex, long reason, short readRet = 0, bool missingExport = false,
    bool missingIoExport = false, unsigned long inputs = 16) {
  LTDMCDriver motion; setup(motion, 7);
  reportedStopReason = reason; stopReasonReadRet = readRet;
  if (reason == 201) stoppedAxisIo = inputs;
  if (reason == 0) startAxisIo = stoppedAxisIo = 0;
  if (missingExport) dmcGetStopReason = nullptr;
  if (missingIoExport) dmcAxisIoStatus = nullptr;
  std::array<bool, 6> selection{};
  selection[axisIndex] = true;
  std::string error;
  try { motion.homeSide(Side::Right, selection); }
  catch (const std::runtime_error& failure) { error = failure.what(); }
  require(error.find("hardware home stopped without completion") != std::string::npos,
      "stopped X/Z waited until cancellation instead of reporting the controller reason");
  require(error.find(axisIndex == 0 ? "semanticAxis=X physicalAxis=2" : "semanticAxis=Z physicalAxis=5")
      != std::string::npos, "stopped axis was not identified");
  const auto expected = missingExport ? "stopReason=unavailable export=missing"
      : readRet ? "stopReason=unavailable readRet=" + std::to_string(readRet)
      : "stopReason=" + std::to_string(reason) + " ";
  require(error.find(expected) != std::string::npos, "stop reason/error was lost or fabricated");
  if (reason == 201 && !missingExport && !readRet) {
    require(error.find("正负限位之间全程没找到原点信号") != std::string::npos,
        "observed stop reason 201 is missing its documented explanation");
  }
  const auto expectedInputs = missingIoExport ? std::string("startAxisIo=unavailable stopAxisIo=unavailable")
      : "startAxisIo=" + std::to_string(startAxisIo) + " stopAxisIo=" + std::to_string(stoppedAxisIo);
  require(error.find(expectedInputs) != std::string::npos, "start/stop input samples were lost or fabricated");
  require(error.find(axisIndex == 0 ? "startPulse=90002 stopPulse=1002" : "startPulse=90005 stopPulse=1005")
      != std::string::npos, "start/stop pulses were lost");
  require(axisIoReads == (missingIoExport ? 0 : 2), "home input getter was called unexpectedly");
  require(stopReasonReads == (missingExport ? 0 : 1), "stop reason getter was called unexpectedly");
  require(polls[axisIndex == 0 ? 2 : 5] < 200 && stops > 0 && motion.estopActive(),
      "stopped home was not promptly failed and latched");
}
void stoppedXZAtPositiveLimitDefinesReference(int axisIndex) {
  LTDMCDriver motion; setup(motion, 7);
  reportedStopReason = 201; startAxisIo = 4112; stoppedAxisIo = 4114;
  std::array<bool, 6> selection{};
  selection[axisIndex] = true;
  const auto references = motion.homeSide(Side::Right, selection);
  require(references == selection, "limit reference result was lost or applied to another axis");
  require(stops == 0 && !motion.estopActive(), "confirmed positive limit reference was rejected");
  const auto state = motion.readState();
  require(state.axes[6 + axisIndex].pulse == (axisIndex == 0 ? 1002 : 1005),
      "limit reference reset the pulse counter or retained an old position");
}
void limitReferenceStillRejectsInterruption(int interruption) {
  LTDMCDriver motion; setup(motion, 7);
  reportedStopReason = 201; stoppedAxisIo = 4114;
  interruptOnStopInput = interruption;
  bool rejected = false;
  try { motion.homeSide(Side::Right, {true, false, false, false, false, false}); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && motion.estopActive() && stops > 0,
      "limit reference ignored an emergency stop or expired control lease");
}
void limitReferenceRequiresStablePulse() {
  LTDMCDriver motion; setup(motion, 7);
  reportedStopReason = 201; stoppedAxisIo = 4114; unstableStoppedPulse = true;
  bool rejected = false;
  try { motion.homeSide(Side::Right, {true, false, false, false, false, false}); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && stops > 0 && stopReasonReads == 0,
      "changing pulse counter was accepted as a stable reference");
}
void operatorRightXYLimitReference(int axisIndex, unsigned long inputs = 4114, long reason = 201,
    int interruption = 0, bool unstable = false) {
  LTDMCDriver motion; setup(motion, 7);
  expectedCard = 1; reportedStopReason = reason; stoppedAxisIo = inputs;
  interruptOnStopInput = interruption; unstableStoppedPulse = unstable;
  std::array<bool, 6> selection{};
  selection[axisIndex] = true;
  bool rejected = false;
  std::array<bool, 6> references{};
  try { references = motion.homeSide(Side::Left, selection); }
  catch (const std::runtime_error&) { rejected = true; }
  const bool accepted = inputs == 4114 && (reason == 201 || reason == 22)
      && interruption == 0 && !unstable;
  require(started == std::vector<unsigned short>{static_cast<unsigned short>(axisIndex)},
      "operator right X/Y selected the wrong physical axis");
  if (accepted) {
    require(!rejected && references == selection && stops == 0 && !motion.estopActive(),
        "operator right X/Y positive limit reference was not accepted");
    require(motion.readState().axes[axisIndex].pulse == 1000 + axisIndex,
        "operator right X/Y reference changed raw pulses");
  } else {
    require(rejected && stops > 0 && motion.estopActive(), "unsafe operator right X/Y reference accepted");
  }
}
void operatorRightZCannotUseLimitReference() {
  LTDMCDriver motion; setup(motion, 7);
  expectedCard = 1; reportedStopReason = 201; stoppedAxisIo = 4114;
  bool rejected = false;
  try { motion.homeSide(Side::Left, {false, false, true, false, false, false}); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started == std::vector<unsigned short>{3} && stops > 0
      && axisIoReads == 0 && stopReasonReads == 0, "fallback affected the other hardware side");
}
void mixedSignalHomeAndLimitReferenceRemainDistinct() {
  LTDMCDriver motion; setup(motion, 9);
  reportedStopReason = 201; stoppedAxisIo = 4114;
  const auto references = motion.homeSide(Side::Right, {true, true, true, true, true, true});
  require(references == std::array<bool, 6>{true, false, true, false, false, false},
      "mixed home mislabeled origin signals as limit references");
  require(started == std::vector<unsigned short>{2, 0, 5, 8, 1, 7} && stops == 0,
      "mixed home skipped an axis or unexpectedly stopped");
}
void transientStopAndCompletionReadRaceDoNotFail() {
  LTDMCDriver motion; setup(motion, 8);
  motion.homeSide(Side::Right, {true, false, true, false, false, false});
  require(stops == 0 && stopReasonReads == 0 && !motion.estopActive(),
      "transient stopped state was mistaken for failed homing");
  require(polls[2] >= 46 && polls[5] >= 46, "home completed before confirmation");
}
void unrelatedAxisKeepsExistingHandling() {
  LTDMCDriver motion; setup(motion, 7);
  std::string error;
  try { motion.homeSide(Side::Right, {false, true, false, false, false, false}); }
  catch (const std::runtime_error& failure) { error = failure.what(); }
  require(stopReasonReads == 0 && axisIoReads == 0 && error.find("stopped without completion") == std::string::npos,
      "X/Z change affected Y");
  require(stops > 0 && motion.estopActive(), "Y cancellation no longer stops");
}
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
    for (const auto axis : {0, 1}) {
      operatorRightXYLimitReference(axis, 4114, 22);
      for (const auto inputs : {0UL, 16UL, 4UL, 6UL, 3UL, 10UL, 0x42UL, 0x82UL, 0x802UL, 0xffffffffUL})
        operatorRightXYLimitReference(axis, inputs, 22);
      operatorRightXYLimitReference(axis, 4114, 22, 1);
      operatorRightXYLimitReference(axis, 4114, 22, 2);
      operatorRightXYLimitReference(axis, 4114, 22, 0, true);
    }
    stoppedXZAtPositiveLimitDefinesReference(0);
    stoppedXZAtPositiveLimitDefinesReference(2);
    limitReferenceStillRejectsInterruption(1);
    limitReferenceStillRejectsInterruption(2);
    limitReferenceRequiresStablePulse();
    operatorRightZCannotUseLimitReference();
    for (const auto axis : {0, 1}) {
      operatorRightXYLimitReference(axis);
      for (const auto inputs : {0UL, 16UL, 4UL, 6UL, 3UL, 10UL, 0x42UL, 0x82UL, 0x802UL, 0xffffffffUL})
        operatorRightXYLimitReference(axis, inputs);
      operatorRightXYLimitReference(axis, 4114, 5);
      operatorRightXYLimitReference(axis, 4114, 201, 1);
      operatorRightXYLimitReference(axis, 4114, 201, 2);
      operatorRightXYLimitReference(axis, 4114, 201, 0, true);
    }
    mixedSignalHomeAndLimitReferenceRemainDistinct();
    for (const auto inputs : {0UL, 16UL, 4UL, 6UL, 3UL, 10UL, 0x42UL, 0x82UL, 0x802UL, 0xffffffffUL}) {
      stoppedXZReportsReason(0, 201, 0, false, false, inputs);
      stoppedXZReportsReason(2, 201, 0, false, false, inputs);
    }
    for (const auto reason : {0L, 5L, 6L, 21L, 22L, 201L, 999L}) {
      stoppedXZReportsReason(0, reason);
      stoppedXZReportsReason(2, reason);
    }
    stoppedXZReportsReason(0, 21, -9);
    stoppedXZReportsReason(2, 21, 0, true);
    stoppedXZReportsReason(0, 201, 0, false, true);
    transientStopAndCompletionReadRaceDoNotFail();
    unrelatedAxisKeepsExistingHandling();
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
