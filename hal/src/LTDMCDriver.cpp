#include "LTDMCDriver.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <utility>

#ifdef _WIN32
#include <windows.h>
#endif

namespace appstation::hal {

namespace {
constexpr std::array<bool, 6> kAllAxesEnabled{true, true, true, true, true, true};
constexpr std::array<std::array<bool, 6>, 2> kAllSidesAxesEnabled{{kAllAxesEnabled, kAllAxesEnabled}};

std::int64_t unixTimeMs() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

#ifdef _WIN32
using DmcBoardInit = short(__stdcall*)();
using DmcGetPosition = long(__stdcall*)(unsigned short, unsigned short);
using DmcStop = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
using DmcEmgStop = short(__stdcall*)(unsigned short);
using DmcSetProfile =
    short(__stdcall*)(unsigned short, unsigned short, double, double, double, double, double);
using DmcSetSProfile = short(__stdcall*)(unsigned short, unsigned short, unsigned short, double);
using DmcPMove = short(__stdcall*)(unsigned short, unsigned short, long, unsigned short);
using DmcUpdateTargetPosition = short(__stdcall*)(unsigned short, unsigned short, long, unsigned short);
using DmcCheckDone = short(__stdcall*)(unsigned short, unsigned short);
using DmcSetPulseOutmode = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
using DmcSetElMode = short(__stdcall*)(unsigned short, unsigned short, unsigned short, unsigned short, unsigned short);
using DmcSetHomeMode =
    short(__stdcall*)(unsigned short, unsigned short, unsigned short, double, unsigned short, unsigned short);
using DmcSetHomePinLogic = short(__stdcall*)(unsigned short, unsigned short, unsigned short, double);
using DmcHomeMove = short(__stdcall*)(unsigned short, unsigned short);
using DmcWriteSevonPin = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
using DmcReadSevonPin = short(__stdcall*)(unsigned short, unsigned short);
using DmcAxisIoStatus = unsigned long(__stdcall*)(unsigned short, unsigned short);
using DmcReadRdyPin = short(__stdcall*)(unsigned short, unsigned short);
using DmcReadErcPin = short(__stdcall*)(unsigned short, unsigned short);
using DmcReadSevrstPin = short(__stdcall*)(unsigned short, unsigned short);
using DmcGetStopReason = short(__stdcall*)(unsigned short, unsigned short, long*);
using DmcGetElMode =
    short(__stdcall*)(unsigned short, unsigned short, unsigned short*, unsigned short*, unsigned short*);

HMODULE ltdmcModule = nullptr;
DmcBoardInit dmcBoardInit = nullptr;
DmcGetPosition dmcGetPosition = nullptr;
DmcStop dmcStop = nullptr;
DmcEmgStop dmcEmgStop = nullptr;
DmcSetProfile dmcSetProfile = nullptr;
DmcSetSProfile dmcSetSProfile = nullptr;
DmcPMove dmcPMove = nullptr;
DmcUpdateTargetPosition dmcUpdateTargetPosition = nullptr;
DmcCheckDone dmcCheckDone = nullptr;
DmcSetPulseOutmode dmcSetPulseOutmode = nullptr;
DmcSetElMode dmcSetElMode = nullptr;
DmcSetHomeMode dmcSetHomeMode = nullptr;
DmcSetHomePinLogic dmcSetHomePinLogic = nullptr;
DmcHomeMove dmcHomeMove = nullptr;
DmcWriteSevonPin dmcWriteSevonPin = nullptr;
DmcReadSevonPin dmcReadSevonPin = nullptr;
DmcAxisIoStatus dmcAxisIoStatus = nullptr;
DmcReadRdyPin dmcReadRdyPin = nullptr;
DmcReadErcPin dmcReadErcPin = nullptr;
DmcReadSevrstPin dmcReadSevrstPin = nullptr;
DmcGetStopReason dmcGetStopReason = nullptr;
DmcGetElMode dmcGetElMode = nullptr;
#endif

std::string dmcAxisFailureMessage(const char* operation, short ret, unsigned short card, unsigned short axis) {
  std::ostringstream out;
  out << operation << " failed"
      << " ret=" << ret
      << " card=" << card
      << " axis=" << axis;
  return out.str();
}

std::string dmcFailureMessage(
    const char* operation,
    short ret,
    unsigned short card,
    unsigned short axis,
    long deltaPulse) {
  std::ostringstream out;
  out << operation << " failed"
      << " ret=" << ret
      << " card=" << card
      << " axis=" << axis
      << " deltaPulse=" << deltaPulse;
  return out.str();
}

std::string dmcAbsoluteFailureMessage(
    const char* operation,
    short ret,
    unsigned short card,
    unsigned short axis,
    long deltaPulse,
    long targetPulse,
    long currentPulse) {
  std::ostringstream out;
  out << operation << " failed"
      << " ret=" << ret
      << " card=" << card
      << " axis=" << axis
      << " deltaPulse=" << deltaPulse
      << " targetPulse=" << targetPulse
      << " currentPulse=" << currentPulse;
  return out.str();
}

#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
constexpr long kWorkOriginSettledPulseTolerance = 100;

void startWorkOriginMoveOrThrow(
    unsigned short card,
    unsigned short axisNo,
    long targetPulse,
    long deltaPulse,
    long currentPulse) {
  if (std::abs(deltaPulse) <= kWorkOriginSettledPulseTolerance) {
    return;
  }
  const auto absoluteRet = dmcPMove(card, axisNo, targetPulse, 1);
  if (absoluteRet == 0) {
    return;
  }
  const auto relativeRet = dmcPMove(card, axisNo, deltaPulse, 0);
  if (relativeRet == 0) {
    return;
  }
  std::ostringstream out;
  out << dmcAbsoluteFailureMessage("dmc_pmove", absoluteRet, card, axisNo, deltaPulse, targetPulse, currentPulse)
      << "; relative fallback ret=" << relativeRet;
  throw std::runtime_error(out.str());
}
#endif

std::string dmcTeleopFailureMessage(
    const char* operation,
    short ret,
    unsigned short card,
    unsigned short axis,
    long deltaPulse,
    long targetPulse,
    double basePulse,
    double targetUi,
    const AxisLimit& limit,
    bool moving) {
  std::ostringstream out;
  out << operation << " failed"
      << " ret=" << ret
      << " card=" << card
      << " axis=" << axis
      << " deltaPulse=" << deltaPulse
      << " targetPulse=" << targetPulse
      << " basePulse=" << basePulse
      << " targetUi=" << targetUi
      << " limit=[" << limit.min << "," << limit.max << "]"
      << " moving=" << (moving ? "true" : "false");
  return out.str();
}

std::string dmcBusyMessage(unsigned short card, unsigned short axis, long deltaPulse) {
  std::ostringstream out;
  out << "axis busy before dmc_pmove"
      << " card=" << card
      << " axis=" << axis
      << " deltaPulse=" << deltaPulse;
  return out.str();
}

unsigned short cardForSide(appstation::hal::Side side) {
  return side == appstation::hal::Side::Left ? static_cast<unsigned short>(1) : static_cast<unsigned short>(0);
}

const char* sideName(appstation::hal::Side side) {
  return side == appstation::hal::Side::Left ? "left" : "right";
}

const char* axisName(appstation::hal::SemanticAxis axis) {
  switch (axis) {
    case appstation::hal::SemanticAxis::X:
      return "X";
    case appstation::hal::SemanticAxis::Y:
      return "Y";
    case appstation::hal::SemanticAxis::Z:
      return "Z";
    case appstation::hal::SemanticAxis::Roll:
      return "Roll";
    case appstation::hal::SemanticAxis::Pitch:
      return "Pitch";
    case appstation::hal::SemanticAxis::Yaw:
      return "Yaw";
  }
  return "Unknown";
}

void appendNullableShort(std::ostringstream& out, const char* key, bool available, short value) {
  out << ",\"" << key << "\":";
  if (available) {
    out << value;
  } else {
    out << "null";
  }
}

void appendNullableUnsignedLong(std::ostringstream& out, const char* key, bool available, unsigned long value) {
  out << ",\"" << key << "\":";
  if (available) {
    out << value;
  } else {
    out << "null";
  }
}

void appendNullableLong(std::ostringstream& out, const char* key, bool available, long value) {
  out << ",\"" << key << "\":";
  if (available) {
    out << value;
  } else {
    out << "null";
  }
}

void appendNullableHex(std::ostringstream& out, const char* key, bool available, unsigned long value) {
  out << ",\"" << key << "\":";
  if (available) {
    out << "\"0x" << std::uppercase << std::hex << value << std::dec << std::nouppercase << "\"";
  } else {
    out << "null";
  }
}

int stageAxisCount(appstation::hal::Side side) {
  return side == appstation::hal::Side::Left ? 6 : 9;
}

bool usesSevonPin(appstation::hal::Side side, appstation::hal::SemanticAxis axis) {
  return physicalAxis(side, axis) < stageAxisCount(side);
}

bool hasReadableSevonFeedback(appstation::hal::Side side, appstation::hal::SemanticAxis axis) {
  if (side == appstation::hal::Side::Right) {
    return false;
  }
  return usesSevonPin(side, axis);
}

bool ignoreUnsupportedSevonWriteFailure(
    appstation::hal::Side side,
    appstation::hal::SemanticAxis axis,
    short ret) {
  return side == appstation::hal::Side::Right && usesSevonPin(side, axis) && ret == 2;
}

double clipTeleopTargetToLimit(double baseUi, double targetUi, const AxisLimit& limit) {
  if (limit.min > limit.max) {
    return baseUi;
  }
  if (baseUi < limit.min) {
    return targetUi > baseUi ? (std::min)(targetUi, limit.max) : baseUi;
  }
  if (baseUi > limit.max) {
    return targetUi < baseUi ? (std::max)(targetUi, limit.min) : baseUi;
  }
  return std::clamp(targetUi, limit.min, limit.max);
}

double velocityToPulsePerSec(Side side, SemanticAxis axis, double velocityUiPerSec) {
  const auto pulseScale = std::abs(pulsePerUnit(side, axis));
  const auto velocityScale = isRotation(axis) ? pulseScale : pulseScale / 1000.0;
  return (std::max)(1.0, velocityUiPerSec * velocityScale);
}

long clampPulseStep(long deltaPulse, double stepLimitPulse) {
  const auto limit = static_cast<long>(std::llround(stepLimitPulse));
  if (limit <= 0 || std::abs(deltaPulse) <= limit) {
    return deltaPulse;
  }
  return deltaPulse > 0 ? limit : -limit;
}

#ifdef _WIN32
void applyMotionProfile(
    unsigned short card,
    unsigned short axisNo,
    double startVelocityPulse,
    double maxVelocityPulse,
    double accTimeSec,
    double decTimeSec,
    long deltaPulse) {
  if (startVelocityPulse >= maxVelocityPulse) {
    startVelocityPulse = (std::max)(1.0, maxVelocityPulse * 0.5);
  }
  const auto retProfile = dmcSetProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, accTimeSec, decTimeSec, 0.0);
  if (retProfile != 0) {
    throw std::runtime_error(dmcFailureMessage("dmc_set_profile", retProfile, card, axisNo, deltaPulse));
  }
  if (dmcSetSProfile) {
    dmcSetSProfile(card, axisNo, 0, 0.0);
  }
}

int updateTeleopTargetBestEffort(unsigned short card, unsigned short axisNo, long targetPulse) {
  // Match ICF teleop: target refresh return codes must not break the 10 ms command loop.
  const auto retUpdate = dmcUpdateTargetPosition(card, axisNo, targetPulse, 1);
  return retUpdate;
}

struct AxisHoldResult {
  double pulse{0.0};
  int updateReturn{0};
};

AxisHoldResult stopTeleopAxisAtCurrentBestEffort(unsigned short card, unsigned short axisNo) {
  const auto retStop = dmcStop(card, axisNo, 0);
  if (retStop != 0) {
    throw std::runtime_error(dmcAxisFailureMessage("dmc_stop", retStop, card, axisNo));
  }
  const auto currentPulse = dmcGetPosition(card, axisNo);
  const auto retUpdate = updateTeleopTargetBestEffort(card, axisNo, currentPulse);
  return {static_cast<double>(currentPulse), retUpdate};
}

template <size_t N>
void waitForAxesDone(
    const std::array<std::pair<unsigned short, unsigned short>, N>& axes,
    size_t count,
    const char* operation,
    int timeoutMs) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
  while (true) {
    bool allDone = true;
    for (size_t i = 0; i < count; ++i) {
      if (dmcCheckDone(axes[i].first, axes[i].second) == 0) {
        allDone = false;
        break;
      }
    }
    if (allDone) {
      return;
    }
    if (std::chrono::steady_clock::now() >= deadline) {
      throw std::runtime_error(std::string(operation) + " wait timeout");
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
}
#endif
}  // namespace

bool LTDMCDriver::initialize() {
  std::scoped_lock lock(mutex_);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  ltdmcModule = LoadLibraryA("LTDMC.dll");
  if (!ltdmcModule) {
    lastError_ = "LTDMC.dll not found";
    initialized_ = false;
    publishStateSnapshotLocked();
    return false;
  }
  dmcBoardInit = reinterpret_cast<DmcBoardInit>(GetProcAddress(ltdmcModule, "dmc_board_init"));
  dmcGetPosition = reinterpret_cast<DmcGetPosition>(GetProcAddress(ltdmcModule, "dmc_get_position"));
  dmcStop = reinterpret_cast<DmcStop>(GetProcAddress(ltdmcModule, "dmc_stop"));
  dmcEmgStop = reinterpret_cast<DmcEmgStop>(GetProcAddress(ltdmcModule, "dmc_emg_stop"));
  dmcSetProfile = reinterpret_cast<DmcSetProfile>(GetProcAddress(ltdmcModule, "dmc_set_profile"));
  dmcSetSProfile = reinterpret_cast<DmcSetSProfile>(GetProcAddress(ltdmcModule, "dmc_set_s_profile"));
  dmcPMove = reinterpret_cast<DmcPMove>(GetProcAddress(ltdmcModule, "dmc_pmove"));
  dmcUpdateTargetPosition =
      reinterpret_cast<DmcUpdateTargetPosition>(GetProcAddress(ltdmcModule, "dmc_update_target_position"));
  dmcCheckDone = reinterpret_cast<DmcCheckDone>(GetProcAddress(ltdmcModule, "dmc_check_done"));
  dmcSetPulseOutmode = reinterpret_cast<DmcSetPulseOutmode>(GetProcAddress(ltdmcModule, "dmc_set_pulse_outmode"));
  dmcSetElMode = reinterpret_cast<DmcSetElMode>(GetProcAddress(ltdmcModule, "dmc_set_el_mode"));
  dmcSetHomeMode = reinterpret_cast<DmcSetHomeMode>(GetProcAddress(ltdmcModule, "dmc_set_homemode"));
  dmcSetHomePinLogic = reinterpret_cast<DmcSetHomePinLogic>(GetProcAddress(ltdmcModule, "dmc_set_home_pin_logic"));
  dmcHomeMove = reinterpret_cast<DmcHomeMove>(GetProcAddress(ltdmcModule, "dmc_home_move"));
  dmcWriteSevonPin = reinterpret_cast<DmcWriteSevonPin>(GetProcAddress(ltdmcModule, "dmc_write_sevon_pin"));
  dmcReadSevonPin = reinterpret_cast<DmcReadSevonPin>(GetProcAddress(ltdmcModule, "dmc_read_sevon_pin"));
  dmcAxisIoStatus = reinterpret_cast<DmcAxisIoStatus>(GetProcAddress(ltdmcModule, "dmc_axis_io_status"));
  dmcReadRdyPin = reinterpret_cast<DmcReadRdyPin>(GetProcAddress(ltdmcModule, "dmc_read_rdy_pin"));
  dmcReadErcPin = reinterpret_cast<DmcReadErcPin>(GetProcAddress(ltdmcModule, "dmc_read_erc_pin"));
  dmcReadSevrstPin = reinterpret_cast<DmcReadSevrstPin>(GetProcAddress(ltdmcModule, "dmc_read_sevrst_pin"));
  dmcGetStopReason = reinterpret_cast<DmcGetStopReason>(GetProcAddress(ltdmcModule, "dmc_get_stop_reason"));
  dmcGetElMode = reinterpret_cast<DmcGetElMode>(GetProcAddress(ltdmcModule, "dmc_get_el_mode"));
  if (!dmcBoardInit || !dmcGetPosition || !dmcSetProfile || !dmcPMove || !dmcCheckDone) {
    lastError_ = "required LTDMC exports missing: board_init/get_position/set_profile/pmove/check_done";
    initialized_ = false;
    publishStateSnapshotLocked();
    return false;
  }
  const auto boards = dmcBoardInit();
  initialized_ = boards >= 2;
  if (!initialized_) {
    lastError_ = "dmc_board_init found fewer than 2 boards";
  } else {
    try {
      configureStageAxes(Side::Left);
      configureStageAxes(Side::Right);
      for (auto& value : enabled_) {
        value = false;
      }
      for (auto& value : commandedEnabled_) {
        value = false;
      }
      for (auto& value : teleopTargetActive_) {
        value = false;
      }
    } catch (const std::exception& exc) {
      lastError_ = exc.what();
    }
  }
  publishStateSnapshotLocked();
  return initialized_;
#else
  lastError_ = "APPSTATION_ENABLE_VENDOR_SDKS is OFF; LTDMC real calls disabled";
  initialized_ = false;
  publishStateSnapshotLocked();
  return false;
#endif
}

HalHealth LTDMCDriver::health(double uptimeS) const {
  std::unique_lock<std::mutex> lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) {
    return cachedHealth(uptimeS);
  }
  HalHealth health{initialized_, false, "hal-real/0.1", uptimeS};
  if (!lastError_.empty()) {
    health.version += " " + lastError_;
  }
  return health;
}

void LTDMCDriver::ensureMotionReturnAllowed() const {
  if (estopActive_.load(std::memory_order_acquire)) {
    throw std::runtime_error("emergency stop active; acknowledge safety before returning to work origin");
  }
}

MotionState LTDMCDriver::readState() {
  std::unique_lock<std::mutex> lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) {
    return cachedStateSnapshot();
  }
  ensureInitialized();
  MotionState state;
  state.readTimestampMs = unixTimeMs();
  state.estopActive = estopActive_.load(std::memory_order_acquire);
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto index = stateIndex(side, axis);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
      if (!hasReadableSevonFeedback(side, axis)) {
        enabled_[index] = commandedEnabled_[index];
      } else if (dmcReadSevonPin) {
        enabled_[index] = dmcReadSevonPin(card, axisNo) > 0;
        commandedEnabled_[index] = enabled_[index];
      }
      state.axes[index].moving = dmcCheckDone(card, axisNo) == 0;
#endif
      state.axes[index].pulse = pulse_[index];
      state.axes[index].uiPosition = pulseToUi(pulse_[index], side, axis);
      state.axes[index].enabled = enabled_[index];
    }
  }
  publishStateSnapshotLocked(state);
  return state;
}

MotionState LTDMCDriver::cachedStateSnapshot() const {
  std::scoped_lock snapshotLock(snapshotMutex_);
  auto state = cachedState_;
  state.readTimestampMs = unixTimeMs();
  state.estopActive = estopActive_.load(std::memory_order_acquire);
  return state;
}

HalHealth LTDMCDriver::cachedHealth(double uptimeS) const {
  std::scoped_lock snapshotLock(snapshotMutex_);
  HalHealth health{cachedInitialized_, false, "hal-real/0.1", uptimeS};
  if (!cachedLastError_.empty()) {
    health.version += " " + cachedLastError_;
  }
  return health;
}

void LTDMCDriver::publishStateSnapshotLocked() {
  MotionState state;
  state.readTimestampMs = unixTimeMs();
  state.estopActive = estopActive_.load(std::memory_order_acquire);
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto index = stateIndex(side, axis);
      state.axes[index].pulse = pulse_[index];
      state.axes[index].uiPosition = pulseToUi(pulse_[index], side, axis);
      state.axes[index].enabled = enabled_[index];
    }
  }
  publishStateSnapshotLocked(state);
}

void LTDMCDriver::publishStateSnapshotLocked(const MotionState& state) {
  std::scoped_lock snapshotLock(snapshotMutex_);
  cachedState_ = state;
  cachedInitialized_ = initialized_;
  cachedLastError_ = lastError_;
}

std::string LTDMCDriver::axisDiagnosticsJson() {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  std::ostringstream out;
  out << "{\"timestamp_ms\":" << unixTimeMs() << ",\"axes\":[";
  bool first = true;
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto index = stateIndex(side, axis);
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      auto pulse = static_cast<long>(std::llround(pulse_[index]));
      short done = -1;
      short sevon = -1;
      short rdy = -1;
      short erc = -1;
      short sevrst = -1;
      short stopReasonRet = -1;
      short elModeRet = -1;
      long stopReason = 0;
      unsigned short elEnable = 0;
      unsigned short elLogic = 0;
      unsigned short elMode = 0;
      unsigned long axisIo = 0;
      bool axisIoAvailable = false;
      bool sevonAvailable = false;
      bool rdyAvailable = false;
      bool ercAvailable = false;
      bool sevrstAvailable = false;
      bool stopReasonFunctionAvailable = false;
      bool stopReasonAvailable = false;
      bool elModeFunctionAvailable = false;
      bool elModeAvailable = false;
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
      pulse = dmcGetPosition(card, axisNo);
      done = dmcCheckDone(card, axisNo);
      if (dmcAxisIoStatus) {
        axisIo = dmcAxisIoStatus(card, axisNo);
        axisIoAvailable = true;
      }
      if (dmcReadSevonPin && usesSevonPin(side, axis)) {
        sevon = dmcReadSevonPin(card, axisNo);
        sevonAvailable = true;
      }
      if (dmcReadRdyPin) {
        rdy = dmcReadRdyPin(card, axisNo);
        rdyAvailable = true;
      }
      if (dmcReadErcPin) {
        erc = dmcReadErcPin(card, axisNo);
        ercAvailable = true;
      }
      if (dmcReadSevrstPin) {
        sevrst = dmcReadSevrstPin(card, axisNo);
        sevrstAvailable = true;
      }
      if (dmcGetStopReason) {
        stopReasonFunctionAvailable = true;
        stopReasonRet = dmcGetStopReason(card, axisNo, &stopReason);
        stopReasonAvailable = stopReasonRet == 0;
      }
      if (dmcGetElMode) {
        elModeFunctionAvailable = true;
        elModeRet = dmcGetElMode(card, axisNo, &elEnable, &elLogic, &elMode);
        elModeAvailable = elModeRet == 0;
      }
#endif
      pulse_[index] = static_cast<double>(pulse);
      if (!first) {
        out << ",";
      }
      first = false;
      out << "{\"side\":\"" << sideName(side) << "\""
          << ",\"axis\":\"" << axisName(axis) << "\""
          << ",\"semanticIndex\":" << axisIndex
          << ",\"card\":" << card
          << ",\"physicalAxis\":" << axisNo
          << ",\"pulse\":" << pulse
          << ",\"uiPosition\":" << pulseToUi(pulse, side, axis)
          << ",\"enabled\":" << (enabled_[index] ? "true" : "false")
          << ",\"commandedEnabled\":" << (commandedEnabled_[index] ? "true" : "false");
      appendNullableShort(out, "checkDone", true, done);
      appendNullableUnsignedLong(out, "axisIoStatus", axisIoAvailable, axisIo);
      appendNullableHex(out, "axisIoStatusHex", axisIoAvailable, axisIo);
      appendNullableShort(out, "sevon", sevonAvailable, sevon);
      appendNullableShort(out, "rdy", rdyAvailable, rdy);
      appendNullableShort(out, "erc", ercAvailable, erc);
      appendNullableShort(out, "sevrst", sevrstAvailable, sevrst);
      appendNullableShort(out, "getStopReasonRet", stopReasonFunctionAvailable, stopReasonRet);
      appendNullableLong(out, "stopReason", stopReasonAvailable, stopReason);
      appendNullableShort(out, "getElModeRet", elModeFunctionAvailable, elModeRet);
      appendNullableShort(out, "elEnable", elModeAvailable, static_cast<short>(elEnable));
      appendNullableShort(out, "elLogic", elModeAvailable, static_cast<short>(elLogic));
      appendNullableShort(out, "elMode", elModeAvailable, static_cast<short>(elMode));
      out << "}";
    }
  }
  out << "]}";
  return out.str();
}

void LTDMCDriver::emergencyStop() {
  estopSequence_.fetch_add(1, std::memory_order_acq_rel);
  estopActive_.store(true, std::memory_order_release);
  stopAllAxesBestEffort();
  disableAllAxesBestEffort();

  std::unique_lock<std::mutex> stateLock(mutex_, std::try_to_lock);
  if (!stateLock.owns_lock()) {
    return;
  }
  for (auto& active : teleopTargetActive_) {
    active = false;
  }
  for (auto& enabled : enabled_) {
    enabled = false;
  }
  for (auto& commanded : commandedEnabled_) {
    commanded = false;
  }
  publishStateSnapshotLocked();
}

void LTDMCDriver::stopAllAxesBestEffort() noexcept {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (dmcEmgStop) {
    for (const auto side : {Side::Left, Side::Right}) {
      const auto card = cardForSide(side);
      dmcEmgStop(card);
    }
  }
  if (dmcStop) {
    for (const auto side : {Side::Left, Side::Right}) {
      const auto card = cardForSide(side);
      for (int axisNo = 0; axisNo < stageAxisCount(side); ++axisNo) {
        dmcStop(card, static_cast<unsigned short>(axisNo), 1);
      }
    }
  }
#endif
}

void LTDMCDriver::disableAllAxesBestEffort() noexcept {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (dmcWriteSevonPin) {
    for (const auto side : {Side::Left, Side::Right}) {
      const auto card = cardForSide(side);
      for (int axisNo = 0; axisNo < stageAxisCount(side); ++axisNo) {
        dmcWriteSevonPin(card, static_cast<unsigned short>(axisNo), 0);
      }
    }
  }
#endif
}

void LTDMCDriver::clearEstopIfUnchanged(std::uint64_t sequenceAtStart) {
  if (estopSequence_.load(std::memory_order_acquire) == sequenceAtStart) {
    estopActive_.store(false, std::memory_order_release);
  }
}

std::string LTDMCDriver::enableSide(Side side, bool enabled) {
  return enableSide(side, enabled, kAllAxesEnabled);
}

std::string LTDMCDriver::enableSide(Side side, bool enabled, const std::array<bool, 6>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
  int succeeded = 0;
  int failed = 0;
  std::ostringstream failures;
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcWriteSevonPin) {
    throw std::runtime_error("required LTDMC export missing: dmc_write_sevon_pin");
  }
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisEnabled = enabled && enabledAxes[axisIndex];
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (!axisEnabled && dmcCheckDone(card, axisNo) == 0) {
      if (failed > 0) {
        failures << "; ";
      }
      failures << dmcAxisFailureMessage("disable servo while axis moving", -1, card, axisNo);
      const auto index = stateIndex(side, axis);
      enabled_[index] = true;
      commandedEnabled_[index] = true;
      ++failed;
      continue;
    }
    if (!usesSevonPin(side, axis)) {
      const auto index = stateIndex(side, axis);
      enabled_[index] = axisEnabled;
      commandedEnabled_[index] = axisEnabled;
      ++succeeded;
      continue;
    }
    const auto ret = dmcWriteSevonPin(card, axisNo, axisEnabled ? 1 : 0);
    if (ignoreUnsupportedSevonWriteFailure(side, axis, ret)) {
      const auto index = stateIndex(side, axis);
      enabled_[index] = axisEnabled;
      commandedEnabled_[index] = axisEnabled;
      ++succeeded;
      continue;
    }
    if (ret != 0) {
      if (failed > 0) {
        failures << "; ";
      }
      failures << dmcAxisFailureMessage("dmc_write_sevon_pin", ret, card, axisNo);
      const auto index = stateIndex(side, axis);
      enabled_[index] = false;
      commandedEnabled_[index] = false;
      ++failed;
      continue;
    }
    const auto index = stateIndex(side, axis);
    enabled_[index] = axisEnabled;
    commandedEnabled_[index] = axisEnabled;
    ++succeeded;
  }
#else
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisEnabled = enabled && enabledAxes[axisIndex];
    const auto index = stateIndex(side, axis);
    enabled_[index] = axisEnabled;
    commandedEnabled_[index] = axisEnabled;
    ++succeeded;
  }
#endif
  clearEstopIfUnchanged(estopSequenceAtStart);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    teleopTargetActive_[stateIndex(side, static_cast<SemanticAxis>(axisIndex))] = false;
  }
  publishStateSnapshotLocked();
  if (failed > 0) {
    throw std::runtime_error(failures.str());
  }
  std::ostringstream message;
  message << (enabled ? "enable" : "disable") << " side completed"
          << " succeeded=" << succeeded
          << " failed=" << failed;
  if (failed > 0) {
    message << " failures=" << failures.str();
  }
  return message.str();
}

void LTDMCDriver::homeSide(Side side) {
  homeSide(side, kAllAxesEnabled);
}

void LTDMCDriver::homeSide(Side side, const std::array<bool, 6>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcHomeMove || !dmcSetPulseOutmode || !dmcSetElMode || !dmcSetHomeMode || !dmcSetHomePinLogic) {
    throw std::runtime_error("required LTDMC home exports missing");
  }
  configureStageAxes(side);
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (dmcCheckDone(card, axisNo) == 0) {
      throw std::runtime_error(dmcAxisFailureMessage("axis busy before dmc_home_move", -1, card, axisNo));
    }
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    const auto ret = dmcHomeMove(card, axisNo);
    if (ret != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_home_move", ret, card, axisNo));
    }
  }
#endif
  clearEstopIfUnchanged(estopSequenceAtStart);
  for (auto& active : teleopTargetActive_) {
    active = false;
  }
  publishStateSnapshotLocked();
}

void LTDMCDriver::homeAll(const std::array<double, 12>& workOriginPulse) {
  homeAll(workOriginPulse, kAllSidesAxesEnabled);
}

void LTDMCDriver::homeAll(
    const std::array<double, 12>& workOriginPulse,
    const std::array<std::array<bool, 6>, 2>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  ensureMotionReturnAllowed();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetProfile || !dmcPMove || !dmcCheckDone || !dmcGetPosition) {
    throw std::runtime_error("required LTDMC motion exports missing");
  }
  constexpr double kTranslationMaxVelocityUi = 4000.0;
  constexpr double kRotationMaxVelocityUi = 6.0;
  constexpr double kTranslationStartVelocityUi = 300.0;
  constexpr double kRotationStartVelocityUi = 0.5;
  constexpr double kRampSec = 0.05;
  std::array<std::pair<unsigned short, unsigned short>, 12> homeAxes{};
  size_t homeAxisCount = 0;
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      if (!enabledAxes[sideIndex][axisIndex]) {
        continue;
      }
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      if (!axisMotionEnabled(side, axis)) {
        throw std::runtime_error("motion axis is not servo-enabled; enable required axes before returning to work origin");
      }
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      homeAxes[homeAxisCount++] = {card, axisNo};
    }
  }
  waitForAxesDone(homeAxes, homeAxisCount, "home_all pre-move", 3000);
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      if (!enabledAxes[sideIndex][axisIndex]) {
        continue;
      }
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      const auto index = stateIndex(side, axis);
      const auto targetPulse = static_cast<long>(std::llround(workOriginPulse[index]));
      const auto currentPulse = dmcGetPosition(card, axisNo);
      const auto deltaPulse = targetPulse - currentPulse;
      const auto rotation = isRotation(axis);
      const auto maxVelocityPulse =
          velocityToPulsePerSec(side, axis, rotation ? kRotationMaxVelocityUi : kTranslationMaxVelocityUi);
      const auto startVelocityPulse =
          velocityToPulsePerSec(side, axis, rotation ? kRotationStartVelocityUi : kTranslationStartVelocityUi);
      const auto retProfile =
          dmcSetProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, kRampSec, kRampSec, 0.0);
      if (retProfile != 0) {
        throw std::runtime_error(dmcFailureMessage("dmc_set_profile", retProfile, card, axisNo, deltaPulse));
      }
      if (dmcSetSProfile) {
        dmcSetSProfile(card, axisNo, 0, 0.0);
      }
      startWorkOriginMoveOrThrow(card, axisNo, targetPulse, deltaPulse, currentPulse);
      pulse_[index] = static_cast<double>(targetPulse);
      teleopTargetPulse_[index] = pulse_[index];
      teleopTargetActive_[index] = false;
    }
  }
  publishStateSnapshotLocked();
  waitForAxesDone(homeAxes, homeAxisCount, "home_all", 60000);
  if (dmcGetPosition) {
    for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
      const auto side = sideIndex == 0 ? Side::Left : Side::Right;
      const auto card = cardForSide(side);
      for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
        const auto axis = static_cast<SemanticAxis>(axisIndex);
        const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
        const auto index = stateIndex(side, axis);
        pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
        teleopTargetPulse_[index] = pulse_[index];
        teleopTargetActive_[index] = false;
      }
    }
  }
#else
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      if (!enabledAxes[sideIndex][axisIndex]) {
        continue;
      }
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      if (!axisMotionEnabled(side, axis)) {
        throw std::runtime_error("motion axis is not servo-enabled; enable required axes before returning to work origin");
      }
      const auto index = stateIndex(side, static_cast<SemanticAxis>(axisIndex));
      pulse_[index] = workOriginPulse[index];
    }
  }
#endif
  clearEstopIfUnchanged(estopSequenceAtStart);
  publishStateSnapshotLocked();
}

void LTDMCDriver::homeOriginSide(Side side, const std::array<double, 6>& workOriginPulse) {
  homeOriginSide(side, workOriginPulse, kAllAxesEnabled);
}

void LTDMCDriver::homeOriginSide(
    Side side,
    const std::array<double, 6>& workOriginPulse,
    const std::array<bool, 6>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  ensureMotionReturnAllowed();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetProfile || !dmcPMove || !dmcCheckDone || !dmcGetPosition) {
    throw std::runtime_error("required LTDMC motion exports missing");
  }
  constexpr double kTranslationMaxVelocityUi = 4000.0;
  constexpr double kRotationMaxVelocityUi = 6.0;
  constexpr double kTranslationStartVelocityUi = 300.0;
  constexpr double kRotationStartVelocityUi = 0.5;
  constexpr double kRampSec = 0.05;
  const auto card = cardForSide(side);
  std::array<std::pair<unsigned short, unsigned short>, 6> homeAxes{};
  size_t homeAxisCount = 0;
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!axisMotionEnabled(side, axis)) {
      throw std::runtime_error("motion axis is not servo-enabled; enable required axes before returning to work origin");
    }
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    homeAxes[homeAxisCount++] = {card, axisNo};
  }
  waitForAxesDone(homeAxes, homeAxisCount, "home_origin_side pre-move", 3000);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    const auto index = stateIndex(side, axis);
    const auto targetPulse = static_cast<long>(std::llround(workOriginPulse[axisIndex]));
    const auto currentPulse = dmcGetPosition(card, axisNo);
    const auto deltaPulse = targetPulse - currentPulse;
    const auto rotation = isRotation(axis);
    const auto maxVelocityPulse =
        velocityToPulsePerSec(side, axis, rotation ? kRotationMaxVelocityUi : kTranslationMaxVelocityUi);
    const auto startVelocityPulse =
        velocityToPulsePerSec(side, axis, rotation ? kRotationStartVelocityUi : kTranslationStartVelocityUi);
    const auto retProfile =
        dmcSetProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, kRampSec, kRampSec, 0.0);
    if (retProfile != 0) {
      throw std::runtime_error(dmcFailureMessage("dmc_set_profile", retProfile, card, axisNo, deltaPulse));
    }
    if (dmcSetSProfile) {
      dmcSetSProfile(card, axisNo, 0, 0.0);
    }
    startWorkOriginMoveOrThrow(card, axisNo, targetPulse, deltaPulse, currentPulse);
    pulse_[index] = static_cast<double>(targetPulse);
    teleopTargetActive_[index] = false;
  }
  publishStateSnapshotLocked();
  waitForAxesDone(homeAxes, homeAxisCount, "home_origin_side", 60000);
  if (dmcGetPosition) {
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      const auto index = stateIndex(side, axis);
      pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
      teleopTargetPulse_[index] = pulse_[index];
      teleopTargetActive_[index] = false;
    }
  }
#else
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!axisMotionEnabled(side, axis)) {
      throw std::runtime_error("motion axis is not servo-enabled; enable required axes before returning to work origin");
    }
    const auto index = stateIndex(side, static_cast<SemanticAxis>(axisIndex));
    pulse_[index] = workOriginPulse[axisIndex];
    teleopTargetActive_[index] = false;
  }
#endif
  clearEstopIfUnchanged(estopSequenceAtStart);
  publishStateSnapshotLocked();
}

void LTDMCDriver::moveAllUi(const std::array<double, 12>& targetUi, const std::array<AxisLimit, 12>& limits) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  checkLimits(targetUi, limits);
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto index = stateIndex(side, axis);
      pulse_[index] = uiToPulse(targetUi[index], side, axis);
      teleopTargetActive_[index] = false;
    }
  }
#ifdef APPSTATION_ENABLE_VENDOR_SDKS
  // TODO: 将语义轴映射到 physicalAxis(side, axis)，应用运动参数并提交运动命令。
#endif
}

void LTDMCDriver::moveRelativeUi(
    Side side,
    SemanticAxis axis,
    double deltaUi,
    double maxVelocityUiPerSec,
    double startVelocityUiPerSec,
    double accTimeSec,
    double decTimeSec) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
  const auto rotation = isRotation(axis);
  // 硬件测试安全边界：避免相对 jog 误把轴带出工作台范围。
  // 上层调用方仍需按任务继续施加软限位。
  const auto maxStep = rotation ? 2.0 : 5000.0;
  if (std::abs(deltaUi) > maxStep) {
    throw std::runtime_error(rotation ? "rotation jog exceeds 2 degree" : "translation jog exceeds 5000 um");
  }
  if (maxVelocityUiPerSec <= 0) {
    throw std::runtime_error("max velocity must be positive");
  }
  const auto index = stateIndex(side, axis);
  const auto deltaPulse = static_cast<long>(std::llround(uiToPulse(deltaUi, side, axis)));
  if (deltaPulse == 0) {
    throw std::runtime_error("jog delta rounds to zero pulses");
  }
  if (!axisMotionEnabled(side, axis)) {
    throw std::runtime_error("axis is not servo-enabled");
  }
  const auto card = side == Side::Left ? static_cast<unsigned short>(1) : static_cast<unsigned short>(0);
  const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
  const auto pulseScale = std::abs(pulsePerUnit(side, axis));
  // pulsePerUnit 对平移表示 pulse/mm，对旋转表示 pulse/degree。
  // UI 速度分别是 um/s 和 deg/s，因此平移速度需要 /1000 做单位转换。
  const auto velocityScale = rotation ? pulseScale : pulseScale / 1000.0;
  const auto maxVelocityPulse = (std::max)(1.0, maxVelocityUiPerSec * velocityScale);
  double startVelocityPulse;
  if (startVelocityUiPerSec > 0) {
    startVelocityPulse = (std::max)(1.0, startVelocityUiPerSec * velocityScale);
  } else {
    startVelocityPulse = (std::max)(1.0, maxVelocityPulse * 0.2);
  }
  // LTDMC 固件不接受 dmc_set_profile 中 Min_Vel == Max_Vel。
  // 否则会返回参数错误，后续 dmc_pmove 可能沿用旧 profile；这里强制留出小斜坡。
  if (startVelocityPulse >= maxVelocityPulse) {
    startVelocityPulse = (std::max)(1.0, maxVelocityPulse * 0.5);
  }
  // dmc_set_profile 的 Tacc/Tdec 单位是秒，表示从 start ramp 到 max 的时间。
  // 调用方未传正数时，默认使用较保守的 50ms 斜坡。
  const double tacc = accTimeSec > 0 ? accTimeSec : 0.05;
  const double tdec = decTimeSec > 0 ? decTimeSec : 0.05;
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (dmcCheckDone(card, axisNo) == 0) {
    throw std::runtime_error(dmcBusyMessage(card, axisNo, deltaPulse));
  }
  const auto retProfile = dmcSetProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, tacc, tdec, 0.0);
  if (retProfile != 0) {
    throw std::runtime_error(dmcFailureMessage("dmc_set_profile", retProfile, card, axisNo, deltaPulse));
  }
  if (dmcSetSProfile) {
    dmcSetSProfile(card, axisNo, 0, 0.0);
  }
  const auto retMove = dmcPMove(card, axisNo, deltaPulse, 0);
  if (retMove != 0) {
    throw std::runtime_error(dmcFailureMessage("dmc_pmove", retMove, card, axisNo, deltaPulse));
  }
#else
  (void)card;
  (void)axisNo;
  (void)maxVelocityPulse;
  (void)startVelocityPulse;
  (void)tacc;
  (void)tdec;
#endif
  pulse_[index] += static_cast<double>(deltaPulse);
  teleopTargetActive_[index] = false;
  clearEstopIfUnchanged(estopSequenceAtStart);
}

TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(
    Side side,
    const std::array<double, 6>& deltaUi,
    double translationStepPulse,
    double rotationStepPulse,
    double translationPulseDeadband,
    double rotationPulseDeadband,
    const std::array<bool, 6>& enabledAxes,
    bool syncZeroDeltaTarget,
    const std::array<AxisLimit, 6>& limits,
    double translationVelocityUiPerSec,
    double rotationVelocityUiPerSec,
    double translationStartVelocityUiPerSec,
    double rotationStartVelocityUiPerSec,
    double accTimeSec,
    double decTimeSec) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
  if (translationVelocityUiPerSec <= 0 || rotationVelocityUiPerSec <= 0) {
    throw std::runtime_error("teleop velocity must be positive");
  }
  if (translationStepPulse <= 0 || rotationStepPulse <= 0) {
    throw std::runtime_error("teleop pulse step limit must be positive");
  }
  translationPulseDeadband = (std::max)(0.0, translationPulseDeadband);
  rotationPulseDeadband = (std::max)(0.0, rotationPulseDeadband);
  const double tacc = accTimeSec > 0 ? accTimeSec : 0.05;
  const double tdec = decTimeSec > 0 ? decTimeSec : 0.05;
  const auto card = cardForSide(side);
  TeleopTargetUpdateResult result;
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetProfile || !dmcPMove || !dmcCheckDone || !dmcGetPosition || !dmcStop || !dmcUpdateTargetPosition) {
    throw std::runtime_error("required LTDMC teleop exports missing: set_profile/pmove/check_done/get_position/stop/update_target_position");
  }
#endif
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto delta = deltaUi[axisIndex];
    const auto rotation = isRotation(axis);
    const auto index = stateIndex(side, axis);
    double actualPulse = teleopTargetActive_[index] ? teleopTargetPulse_[index] : pulse_[index];
    result.requestedDeltaUi[axisIndex] = delta;
    result.targetPulse[axisIndex] = actualPulse;
    result.targetUi[axisIndex] = pulseToUi(actualPulse, side, axis);
    if (!enabledAxes[axisIndex]) {
      teleopTargetActive_[index] = false;
      continue;
    }
    const auto stepLimitPulse = rotation ? rotationStepPulse : translationStepPulse;
    const auto pulseDeadband = rotation ? rotationPulseDeadband : translationPulseDeadband;
    const auto requestedDeltaPulse = static_cast<long>(std::llround(uiToPulse(delta, side, axis)));
    const auto deadbandedDeltaPulse =
        std::abs(requestedDeltaPulse) <= static_cast<long>(std::llround(pulseDeadband)) ? 0 : requestedDeltaPulse;
    const auto deltaPulse = clampPulseStep(deadbandedDeltaPulse, stepLimitPulse);
    result.requestedDeltaPulse[axisIndex] = static_cast<double>(requestedDeltaPulse);
    if (deltaPulse != requestedDeltaPulse) {
      result.clipped[axisIndex] = true;
    }
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    const bool moving = dmcCheckDone(card, axisNo) == 0;
    actualPulse = static_cast<double>(dmcGetPosition(card, axisNo));
    pulse_[index] = actualPulse;
    const bool reattachMovingTarget = moving && !teleopTargetActive_[index];
    if (reattachMovingTarget) {
      teleopTargetPulse_[index] = actualPulse;
      teleopTargetActive_[index] = true;
    }
#else
    const bool moving = false;
#endif
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    if (dmcAxisIoStatus) {
      result.axisIoStatus[axisIndex] = static_cast<double>(dmcAxisIoStatus(card, axisNo));
    }
    if (dmcGetStopReason) {
      long stopReason = 0;
      if (dmcGetStopReason(card, axisNo, &stopReason) == 0) {
        result.stopReason[axisIndex] = static_cast<double>(stopReason);
      }
    }
#endif
    result.currentPulse[axisIndex] = actualPulse;
    result.movingBefore[axisIndex] = moving;
    if (deltaPulse == 0) {
      const bool zeroDeltaWasActive = teleopTargetActive_[index];
      if (syncZeroDeltaTarget && zeroDeltaWasActive) {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
        if (moving) {
          const auto hold = stopTeleopAxisAtCurrentBestEffort(card, axisNo);
          actualPulse = hold.pulse;
          result.updateReturn[axisIndex] = static_cast<double>(hold.updateReturn);
        } else {
          actualPulse = static_cast<double>(dmcGetPosition(card, axisNo));
          const auto updateTargetPulse = static_cast<long>(std::llround(actualPulse));
          result.updateReturn[axisIndex] = static_cast<double>(
              updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse));
        }
        pulse_[index] = actualPulse;
        result.currentPulse[axisIndex] = actualPulse;
#endif
        teleopTargetPulse_[index] = actualPulse;
        teleopTargetActive_[index] = true;
        result.targetPulse[axisIndex] = actualPulse;
        result.targetUi[axisIndex] = pulseToUi(actualPulse, side, axis);
      }
      continue;
    }
    if (!axisMotionEnabled(side, axis)) {
      throw std::runtime_error("teleop axis is not servo-enabled");
    }
    const auto maxVelocityUiPerSec = rotation ? rotationVelocityUiPerSec : translationVelocityUiPerSec;
    const auto requestedStartVelocity =
        rotation ? rotationStartVelocityUiPerSec : translationStartVelocityUiPerSec;
    const auto maxVelocityPulse = velocityToPulsePerSec(side, axis, maxVelocityUiPerSec);
    const auto startVelocityPulse =
        requestedStartVelocity > 0 ? velocityToPulsePerSec(side, axis, requestedStartVelocity)
                                   : (std::max)(1.0, maxVelocityPulse * 0.2);
    const auto basePulse = actualPulse;
    const auto baseUi = pulseToUi(basePulse, side, axis);
    const auto limit = limits[axisIndex];
    const auto unclippedTargetPulse = basePulse + static_cast<double>(deltaPulse);
    const auto unclippedTargetUi = pulseToUi(unclippedTargetPulse, side, axis);
    const auto targetUi = clipTeleopTargetToLimit(baseUi, unclippedTargetUi, limit);
    const auto targetPulse = uiToPulse(targetUi, side, axis);
    const auto updateTargetPulse = static_cast<long>(std::llround(targetPulse));
    const auto appliedTargetPulse = static_cast<double>(updateTargetPulse);
    const auto appliedTargetUi = pulseToUi(appliedTargetPulse, side, axis);
    const bool targetHeldAtBase = std::abs(appliedTargetPulse - basePulse) <= 0.5;
    if (std::abs(targetUi - unclippedTargetUi) > 1e-9) {
      result.clipped[axisIndex] = true;
    }
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    if (targetHeldAtBase && moving) {
      const auto hold = stopTeleopAxisAtCurrentBestEffort(card, axisNo);
      actualPulse = hold.pulse;
      pulse_[index] = actualPulse;
      const auto actualUi = pulseToUi(actualPulse, side, axis);
      result.currentPulse[axisIndex] = actualPulse;
      result.updateReturn[axisIndex] = static_cast<double>(hold.updateReturn);
      result.appliedDeltaPulse[axisIndex] = 0.0;
      result.appliedDeltaUi[axisIndex] = 0.0;
      result.targetPulse[axisIndex] = actualPulse;
      result.targetUi[axisIndex] = actualUi;
      teleopTargetPulse_[index] = actualPulse;
      teleopTargetActive_[index] = true;
      continue;
    }
    const bool shouldLaunchMove = !moving;
    const auto launchDeltaPulse = deltaPulse;
    result.moveStarted[axisIndex] = shouldLaunchMove;
    result.launchDeltaPulse[axisIndex] = shouldLaunchMove ? static_cast<double>(launchDeltaPulse) : 0.0;
    if (shouldLaunchMove) {
      applyMotionProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, tacc, tdec, launchDeltaPulse);
    }
    result.updateReturn[axisIndex] =
        static_cast<double>(updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse));
#else
    (void)card;
    (void)axisNo;
    (void)moving;
    (void)maxVelocityPulse;
    (void)startVelocityPulse;
    (void)tacc;
    (void)tdec;
    result.moveStarted[axisIndex] = !moving || !teleopTargetActive_[index];
    result.launchDeltaPulse[axisIndex] = result.moveStarted[axisIndex] ? static_cast<double>(deltaPulse) : 0.0;
#endif
    result.appliedDeltaPulse[axisIndex] = appliedTargetPulse - basePulse;
    result.appliedDeltaUi[axisIndex] = appliedTargetUi - baseUi;
    result.targetPulse[axisIndex] = appliedTargetPulse;
    result.targetUi[axisIndex] = appliedTargetUi;
    teleopTargetPulse_[index] = appliedTargetPulse;
    teleopTargetActive_[index] = true;
#if !defined(_WIN32) || !defined(APPSTATION_ENABLE_VENDOR_SDKS)
    pulse_[index] = appliedTargetPulse;
#endif
  }
  clearEstopIfUnchanged(estopSequenceAtStart);
  return result;
}

void LTDMCDriver::stopTeleopSide(Side side) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto index = stateIndex(side, axis);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    if (dmcStop) {
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      const auto retStop = dmcStop(card, axisNo, 0);
      if (retStop != 0) {
        throw std::runtime_error(dmcAxisFailureMessage("dmc_stop", retStop, card, axisNo));
      }
      if (dmcGetPosition) {
        pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
      }
      if (dmcUpdateTargetPosition) {
        const auto currentPulse = static_cast<long>(std::llround(pulse_[index]));
        updateTeleopTargetBestEffort(card, axisNo, currentPulse);
      }
    }
#else
    (void)card;
#endif
    teleopTargetActive_[index] = false;
    teleopTargetPulse_[index] = pulse_[index];
  }
}

void LTDMCDriver::configureStageAxes(Side side) {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetPulseOutmode || !dmcSetElMode || !dmcSetHomeMode || !dmcSetHomePinLogic) {
    throw std::runtime_error("required LTDMC homing configuration exports missing");
  }
  const auto card = cardForSide(side);
  for (int axisNoInt = 0; axisNoInt < stageAxisCount(side); ++axisNoInt) {
    const auto axisNo = static_cast<unsigned short>(axisNoInt);
    const auto retPulse = dmcSetPulseOutmode(card, axisNo, 5);
    if (retPulse != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_set_pulse_outmode", retPulse, card, axisNo));
    }
    const auto retEl = dmcSetElMode(card, axisNo, 1, 1, 0);
    if (retEl != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_set_el_mode", retEl, card, axisNo));
    }
    const auto retHome = dmcSetHomeMode(card, axisNo, 0, 1.0, 0, 1);
    if (retHome != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_set_homemode", retHome, card, axisNo));
    }
    const auto retLogic = dmcSetHomePinLogic(card, axisNo, 1, 0.0);
    if (retLogic != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_set_home_pin_logic", retLogic, card, axisNo));
    }
  }
#else
  (void)side;
#endif
}

void LTDMCDriver::ensureInitialized() const {
  if (!initialized_) {
    throw std::runtime_error("LTDMCDriver is not initialized");
  }
}

void LTDMCDriver::checkLimits(
    const std::array<double, 12>& targetUi,
    const std::array<AxisLimit, 12>& limits) const {
  for (int i = 0; i < 12; ++i) {
    if (targetUi[i] < limits[i].min || targetUi[i] > limits[i].max) {
      throw std::runtime_error("motion target exceeds soft limit");
    }
  }
}

bool LTDMCDriver::axisMotionEnabled(Side side, SemanticAxis axis) const {
  return enabled_[stateIndex(side, axis)];
}

}  // namespace appstation::hal
