// Offline vendor-branch hardware-home tests. SDK entry points are replaced with in-memory fakes;
// this executable never loads LTDMC.dll or communicates with hardware.
#define APPSTATION_ENABLE_VENDOR_SDKS 1
#include "../src/LTDMCDriver.cpp"

#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>
#include <string>
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

enum class Behavior { Success, StoppedNoHome, HomeReadError, DoneError, StartSecondFails, TripEstop, FailLease, Travel };
Behavior behavior = Behavior::Success;
std::vector<unsigned short> started;
std::array<int, 16> polls{};
long stopReasonValue = 201;
unsigned long startIo = 4112;
unsigned long stopIo = 4114;
int stopCalls = 0;
int emergencyCalls = 0;
int disableCalls = 0;
short stopReturn = 0;
short emergencyReturn = 0;
short disableReturn = 0;
unsigned short expectedCard = 0;
LTDMCDriver* current = nullptr;
std::array<unsigned short, 16> configuredDirection{};
std::array<double, 16> configuredVelocityMode{};
std::array<unsigned short, 16> configuredHomeMode{};
std::array<unsigned short, 16> configuredEzCount{};
std::array<unsigned short, 16> configuredLogic{};
std::array<double, 16> configuredLowVelocity{};
std::array<double, 16> configuredHighVelocity{};

void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}

bool wasStarted(unsigned short axis) {
  return std::find(started.begin(), started.end(), axis) != started.end();
}

short __stdcall setPulse(unsigned short, unsigned short, unsigned short) { return 0; }
short __stdcall setEl(unsigned short, unsigned short, unsigned short, unsigned short, unsigned short) { return 0; }
short __stdcall setHome(unsigned short card, unsigned short axis, unsigned short direction, double velocityMode,
    unsigned short mode, unsigned short ezCount) {
  require(card == expectedCard, "HOME configured on wrong card");
  configuredDirection[axis] = direction;
  configuredVelocityMode[axis] = velocityMode;
  configuredHomeMode[axis] = mode;
  configuredEzCount[axis] = ezCount;
  return 0;
}
short __stdcall setLogic(unsigned short card, unsigned short axis, unsigned short logic, double) {
  require(card == expectedCard, "HOME logic configured on wrong card");
  configuredLogic[axis] = logic;
  return 0;
}
short __stdcall setProfile(unsigned short card, unsigned short axis, double low, double high, double, double, double) {
  require(card == expectedCard, "HOME profile configured on wrong card");
  configuredLowVelocity[axis] = low;
  configuredHighVelocity[axis] = high;
  return 0;
}
short __stdcall setSProfile(unsigned short, unsigned short, unsigned short, double) { return 0; }
short __stdcall startHome(unsigned short card, unsigned short axis) {
  require(card == expectedCard, "HOME started on wrong card");
  if (!started.empty()) {
    const auto previous = started.back();
    require(polls[previous] >= 3, "next HOME axis started before previous axis completed");
  }
  started.push_back(axis);
  if (behavior == Behavior::StartSecondFails && started.size() == 2) return -7;
  return 0;
}
short __stdcall checkDone(unsigned short, unsigned short axis) {
  if (!wasStarted(axis)) return 1;
  if (behavior == Behavior::DoneError) return -8;
  if (behavior == Behavior::StoppedNoHome) return polls[axis] >= 3 ? 1 : 0;
  if (behavior == Behavior::Travel) return 0;
  return polls[axis] >= 3 ? 1 : 0;
}
long __stdcall position(unsigned short, unsigned short axis) {
  if (!wasStarted(axis)) return 90000 + axis;
  if (behavior == Behavior::Travel) return 90000 + axis + polls[axis] * 1000;
  return polls[axis] >= 3 ? 1000 + axis : 90000 + axis;
}
short __stdcall homeResult(unsigned short, unsigned short axis, unsigned short* result) {
  ++polls[axis];
  if (behavior == Behavior::HomeReadError) return -9;
  if (behavior == Behavior::TripEstop) current->latchEmergencyStop();
  if (behavior == Behavior::FailLease) current->failControlLease();
  if (behavior == Behavior::StoppedNoHome || behavior == Behavior::Travel) {
    *result = 0;
    return 0;
  }
  *result = polls[axis] >= 3 ? 1 : 0;
  return 0;
}
unsigned long __stdcall axisIo(unsigned short card, unsigned short axis) {
  require(card == expectedCard, "axis IO read from wrong card");
  return wasStarted(axis) ? stopIo : startIo;
}
short __stdcall getStopReason(unsigned short card, unsigned short, long* reason) {
  require(card == expectedCard, "stop reason read from wrong card");
  *reason = stopReasonValue;
  return 0;
}
short __stdcall stopAxis(unsigned short, unsigned short, unsigned short) { ++stopCalls; return stopReturn; }
short __stdcall emergencyStopCard(unsigned short) { ++emergencyCalls; return emergencyReturn; }
short __stdcall writeSevon(unsigned short, unsigned short, unsigned short value) {
  if (value == 0) ++disableCalls;
  return disableReturn;
}

HardwareHomeConfig config(ReferenceSeekMode mode = ReferenceSeekMode::OriginSignal) {
  HardwareHomeConfig result;
  result.referenceMode = mode;
  for (std::size_t i = 0; i < result.axes.size(); ++i) {
    auto& axis = result.axes[i];
    axis.direction = 0;
    axis.velocityMode = 1;
    axis.mode = 0;
    axis.ezCount = 1;
    axis.logic = 1;
    axis.lowVelocityUi = i < 3 ? 300.0 : 0.5;
    axis.highVelocityUi = i < 3 ? 1000.0 : 2.0;
    axis.accTimeSec = 0.2;
    axis.decTimeSec = 0.2;
    axis.maxSearchUi = i < 3 ? 55000.0 : 90.0;
  }
  return result;
}

void setup(LTDMCDriver& motion, Behavior next = Behavior::Success) {
  behavior = next;
  started.clear();
  polls.fill(0);
  configuredDirection.fill(999);
  configuredVelocityMode.fill(-1);
  configuredHomeMode.fill(999);
  configuredEzCount.fill(999);
  configuredLogic.fill(999);
  configuredLowVelocity.fill(-1);
  configuredHighVelocity.fill(-1);
  stopReasonValue = 201;
  startIo = 4112;
  stopIo = 4114;
  stopCalls = emergencyCalls = disableCalls = 0;
  stopReturn = emergencyReturn = disableReturn = 0;
  expectedCard = 0;
  current = &motion;
  MotionExecutorTestAccess::initialize(motion);
  dmcSetPulseOutmode = setPulse;
  dmcSetElMode = setEl;
  dmcSetHomeMode = setHome;
  dmcSetHomePinLogic = setLogic;
  dmcSetProfile = setProfile;
  dmcSetSProfile = setSProfile;
  dmcHomeMove = startHome;
  dmcGetHomeResult = homeResult;
  dmcCheckDone = checkDone;
  dmcGetPosition = position;
  dmcAxisIoStatus = axisIo;
  dmcGetStopReason = getStopReason;
  dmcStop = stopAxis;
  dmcEmgStop = emergencyStopCard;
  dmcWriteSevonPin = writeSevon;
}

void strictHomeIsSequentialAndConfiguresOnlySelectedAxes() {
  LTDMCDriver motion; setup(motion);
  auto home = config();
  home.axes[0].direction = 1;
  home.axes[0].velocityMode = 0;
  home.axes[0].mode = 3;
  home.axes[0].ezCount = 2;
  home.axes[0].logic = 0;
  home.axes[0].lowVelocityUi = 111;
  home.axes[0].highVelocityUi = 222;
  const auto refs = motion.homeSide(Side::Right, {true, false, true, false, false, false}, home);
  require(refs == std::array<bool, 6>{}, "strict ORG HOME must not claim limit references");
  require(started == std::vector<unsigned short>{2, 5}, "selected right X/Z physical axes or sequential order is wrong");
  require(polls[2] >= 3 && polls[5] >= 3, "HOME returned before each selected axis completed");
  require(configuredDirection[2] == 1 && configuredVelocityMode[2] == 0
      && configuredHomeMode[2] == 3 && configuredEzCount[2] == 2 && configuredLogic[2] == 0,
      "per-axis HOME mode was not applied");
  require(configuredLowVelocity[2] > 0 && configuredHighVelocity[2] > configuredLowVelocity[2],
      "per-axis HOME profile was not applied");
  require(configuredDirection[0] == 999 && configuredDirection[8] == 999,
      "unselected axes had their HOME configuration rewritten");
}

void strictHomeStoppedWithoutHomeFailsPromptly() {
  LTDMCDriver motion; setup(motion, Behavior::StoppedNoHome);
  std::string error;
  try { motion.homeSide(Side::Right, {false, true, false, false, false, false}, config()); }
  catch (const std::runtime_error& exc) { error = exc.what(); }
  require(error.find("hardware home stopped without completion") != std::string::npos,
      "done=1/homeResult=0 did not fail explicitly");
  require(motion.estopActive() && stopCalls > 0 && polls[0] < 100,
      "failed HOME did not stop/latch promptly");
}

void positiveLimitReferenceRequiresExplicitModeAndEdge() {
  LTDMCDriver motion; setup(motion, Behavior::StoppedNoHome);
  auto positive = config(ReferenceSeekMode::PositiveLimit);
  const auto selection = std::array<bool, 6>{true, false, false, false, false, false};
  const auto refs = motion.homeSide(Side::Right, selection, positive);
  require(refs == selection && !motion.estopActive(), "valid explicit positive-limit reference was rejected");

  setup(motion, Behavior::StoppedNoHome);
  startIo = 4114;
  bool rejected = false;
  try { motion.homeSide(Side::Right, selection, positive); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started.empty(), "positive-limit seek started while EL+ was already active");

  setup(motion, Behavior::StoppedNoHome);
  stopIo = 4112;
  rejected = false;
  try { motion.homeSide(Side::Right, selection, positive); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && motion.estopActive(), "positive-limit reference accepted without EL+ OFF->ON edge");

  setup(motion, Behavior::StoppedNoHome);
  rejected = false;
  try { motion.homeSide(Side::Right, selection, config()); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && motion.estopActive(), "strict ORG HOME silently fell back to positive-limit reference");
}

void positiveLimitModeRejectsUnsupportedAxis() {
  LTDMCDriver motion; setup(motion, Behavior::StoppedNoHome);
  bool rejected = false;
  try { motion.homeSide(Side::Right, {false, true, false, false, false, false},
      config(ReferenceSeekMode::PositiveLimit)); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started.empty(), "unsupported axis entered positive-limit reference mode");
}

void maxSearchTravelStopsRunaway() {
  LTDMCDriver motion; setup(motion, Behavior::Travel);
  auto home = config();
  home.axes[0].maxSearchUi = 100.0;  // right X ~= 500 pulse with current calibration
  std::string error;
  try { motion.homeSide(Side::Right, {true, false, false, false, false, false}, home); }
  catch (const std::runtime_error& exc) { error = exc.what(); }
  require(error.find("exceeded configured travel") != std::string::npos,
      "HOME runaway was not bounded by maxSearchUi");
  require(motion.estopActive() && stopCalls > 0, "travel watchdog did not stop and latch");
}

void failuresStopAndLatch() {
  for (const auto mode : {Behavior::HomeReadError, Behavior::DoneError, Behavior::StartSecondFails,
                           Behavior::TripEstop, Behavior::FailLease}) {
    LTDMCDriver motion; setup(motion, mode);
    bool rejected = false;
    try { motion.homeSide(Side::Right, {false, false, false, true, true, true}, config()); }
    catch (const std::runtime_error&) { rejected = true; }
    require(rejected && motion.estopActive() && stopCalls > 0,
        "HOME failure/interruption did not stop all motion and latch safety");
  }
}

void missingExportsAndEmptyMaskNeverMove() {
  LTDMCDriver motion; setup(motion);
  dmcGetHomeResult = nullptr;
  bool rejected = false;
  try { motion.homeSide(Side::Right, {true, false, false, false, false, false}, config()); }
  catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started.empty(), "missing completion export allowed HOME to start");
  setup(motion);
  rejected = false;
  try { motion.homeSide(Side::Right, {}, config()); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && started.empty(), "empty HOME selection allowed motion");
}

void emergencyVendorFailuresAreReported() {
  LTDMCDriver motion; setup(motion);
  stopReturn = -5;
  emergencyReturn = -6;
  disableReturn = -7;
  std::string error;
  try { motion.emergencyStop(); } catch (const std::runtime_error& exc) { error = exc.what(); }
  require(motion.estopActive(), "vendor stop failure cleared software emergency latch");
  require(error.find("hardware emergency action reported failures") != std::string::npos,
      "vendor stop/disable failure was reported as successful emergency stop");
  require(emergencyCalls > 0 && stopCalls > 0 && disableCalls > 0,
      "best-effort emergency path abandoned remaining safety actions after one failure");
}

void homeFailureIncludesEmergencyVendorFailure() {
  LTDMCDriver motion; setup(motion, Behavior::StoppedNoHome);
  stopReturn = -5;
  std::string error;
  try { motion.homeSide(Side::Right, {false, true, false, false, false, false}, config()); }
  catch (const std::runtime_error& exc) { error = exc.what(); }
  require(error.find("hardware home stopped without completion") != std::string::npos
      && error.find("hardware emergency action reported failures") != std::string::npos,
      "HOME error lost the secondary emergency-stop hardware failure");
}

}  // namespace

int main() {
  try {
    strictHomeIsSequentialAndConfiguresOnlySelectedAxes();
    strictHomeStoppedWithoutHomeFailsPromptly();
    positiveLimitReferenceRequiresExplicitModeAndEdge();
    positiveLimitModeRejectsUnsupportedAxis();
    maxSearchTravelStopsRunaway();
    failuresStopAndLatch();
    missingExportsAndEmptyMaskNeverMove();
    emergencyVendorFailuresAreReported();
    homeFailureIncludesEmergencyVendorFailure();
    std::cout << "HardwareHomingTests passed (9 offline vendor-fake cases)" << std::endl;
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "HardwareHomingTests failed: " << error.what() << std::endl;
    return 1;
  }
}
