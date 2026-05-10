#include "LTDMCDriver.h"

#include <algorithm>
#include <cmath>
#include <sstream>

#ifdef _WIN32
#include <windows.h>
#endif

namespace appstation::hal {

namespace {
#ifdef _WIN32
using DmcBoardInit = short(__stdcall*)();
using DmcGetPosition = long(__stdcall*)(unsigned short, unsigned short);
using DmcStop = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
using DmcSetProfile =
    short(__stdcall*)(unsigned short, unsigned short, double, double, double, double, double);
using DmcSetSProfile = short(__stdcall*)(unsigned short, unsigned short, unsigned short, double);
using DmcPMove = short(__stdcall*)(unsigned short, unsigned short, long, unsigned short);
using DmcCheckDone = short(__stdcall*)(unsigned short, unsigned short);
using DmcSetPulseOutmode = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
using DmcSetElMode = short(__stdcall*)(unsigned short, unsigned short, unsigned short, unsigned short, unsigned short);
using DmcSetHomeMode =
    short(__stdcall*)(unsigned short, unsigned short, unsigned short, double, unsigned short, unsigned short);
using DmcSetHomePinLogic = short(__stdcall*)(unsigned short, unsigned short, unsigned short, double);
using DmcHomeMove = short(__stdcall*)(unsigned short, unsigned short);
using DmcWriteSevonPin = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
using DmcReadSevonPin = short(__stdcall*)(unsigned short, unsigned short);

HMODULE ltdmcModule = nullptr;
DmcBoardInit dmcBoardInit = nullptr;
DmcGetPosition dmcGetPosition = nullptr;
DmcStop dmcStop = nullptr;
DmcSetProfile dmcSetProfile = nullptr;
DmcSetSProfile dmcSetSProfile = nullptr;
DmcPMove dmcPMove = nullptr;
DmcCheckDone dmcCheckDone = nullptr;
DmcSetPulseOutmode dmcSetPulseOutmode = nullptr;
DmcSetElMode dmcSetElMode = nullptr;
DmcSetHomeMode dmcSetHomeMode = nullptr;
DmcSetHomePinLogic dmcSetHomePinLogic = nullptr;
DmcHomeMove dmcHomeMove = nullptr;
DmcWriteSevonPin dmcWriteSevonPin = nullptr;
DmcReadSevonPin dmcReadSevonPin = nullptr;
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
}  // namespace

bool LTDMCDriver::initialize() {
  std::scoped_lock lock(mutex_);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  ltdmcModule = LoadLibraryA("LTDMC.dll");
  if (!ltdmcModule) {
    lastError_ = "LTDMC.dll not found";
    initialized_ = false;
    return false;
  }
  dmcBoardInit = reinterpret_cast<DmcBoardInit>(GetProcAddress(ltdmcModule, "dmc_board_init"));
  dmcGetPosition = reinterpret_cast<DmcGetPosition>(GetProcAddress(ltdmcModule, "dmc_get_position"));
  dmcStop = reinterpret_cast<DmcStop>(GetProcAddress(ltdmcModule, "dmc_stop"));
  dmcSetProfile = reinterpret_cast<DmcSetProfile>(GetProcAddress(ltdmcModule, "dmc_set_profile"));
  dmcSetSProfile = reinterpret_cast<DmcSetSProfile>(GetProcAddress(ltdmcModule, "dmc_set_s_profile"));
  dmcPMove = reinterpret_cast<DmcPMove>(GetProcAddress(ltdmcModule, "dmc_pmove"));
  dmcCheckDone = reinterpret_cast<DmcCheckDone>(GetProcAddress(ltdmcModule, "dmc_check_done"));
  dmcSetPulseOutmode = reinterpret_cast<DmcSetPulseOutmode>(GetProcAddress(ltdmcModule, "dmc_set_pulse_outmode"));
  dmcSetElMode = reinterpret_cast<DmcSetElMode>(GetProcAddress(ltdmcModule, "dmc_set_el_mode"));
  dmcSetHomeMode = reinterpret_cast<DmcSetHomeMode>(GetProcAddress(ltdmcModule, "dmc_set_homemode"));
  dmcSetHomePinLogic = reinterpret_cast<DmcSetHomePinLogic>(GetProcAddress(ltdmcModule, "dmc_set_home_pin_logic"));
  dmcHomeMove = reinterpret_cast<DmcHomeMove>(GetProcAddress(ltdmcModule, "dmc_home_move"));
  dmcWriteSevonPin = reinterpret_cast<DmcWriteSevonPin>(GetProcAddress(ltdmcModule, "dmc_write_sevon_pin"));
  dmcReadSevonPin = reinterpret_cast<DmcReadSevonPin>(GetProcAddress(ltdmcModule, "dmc_read_sevon_pin"));
  if (!dmcBoardInit || !dmcGetPosition || !dmcSetProfile || !dmcPMove || !dmcCheckDone) {
    lastError_ = "required LTDMC exports missing: board_init/get_position/set_profile/pmove/check_done";
    initialized_ = false;
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
    } catch (const std::exception& exc) {
      lastError_ = exc.what();
    }
  }
  return initialized_;
#else
  lastError_ = "APPSTATION_ENABLE_VENDOR_SDKS is OFF; LTDMC real calls disabled";
  initialized_ = false;
  return false;
#endif
}

HalHealth LTDMCDriver::health(double uptimeS) const {
  std::scoped_lock lock(mutex_);
  HalHealth health{initialized_, false, "hal-real/0.1", uptimeS};
  if (!lastError_.empty()) {
    health.version += " " + lastError_;
  }
  return health;
}

MotionState LTDMCDriver::readState() {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  MotionState state;
  state.estopActive = estopActive_;
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto index = stateIndex(side, axis);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
      if (dmcReadSevonPin) {
        enabled_[index] = dmcReadSevonPin(card, axisNo) > 0;
      }
      state.axes[index].moving = dmcCheckDone(card, axisNo) == 0;
#endif
      state.axes[index].pulse = pulse_[index];
      state.axes[index].uiPosition = pulseToUi(pulse_[index], side, axis);
      state.axes[index].enabled = enabled_[index];
    }
  }
  return state;
}

void LTDMCDriver::emergencyStop() {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (dmcStop) {
    for (unsigned short card : {static_cast<unsigned short>(0), static_cast<unsigned short>(1)}) {
      for (unsigned short axis = 0; axis < 12; ++axis) {
        dmcStop(card, axis, 0);
      }
    }
  }
#endif
  estopActive_ = true;
}

void LTDMCDriver::enableSide(Side side, bool enabled) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcWriteSevonPin) {
    throw std::runtime_error("required LTDMC export missing: dmc_write_sevon_pin");
  }
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (!enabled && dmcCheckDone(card, axisNo) == 0) {
      throw std::runtime_error(dmcAxisFailureMessage("disable servo while axis moving", -1, card, axisNo));
    }
    const auto ret = dmcWriteSevonPin(card, axisNo, enabled ? 1 : 0);
    if (ret != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_write_sevon_pin", ret, card, axisNo));
    }
    enabled_[stateIndex(side, axis)] = enabled;
  }
#else
  (void)side;
  (void)enabled;
#endif
  estopActive_ = false;
}

void LTDMCDriver::homeSide(Side side) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcHomeMove || !dmcSetPulseOutmode || !dmcSetElMode || !dmcSetHomeMode || !dmcSetHomePinLogic) {
    throw std::runtime_error("required LTDMC home exports missing");
  }
  configureStageAxes(side);
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (dmcCheckDone(card, axisNo) == 0) {
      throw std::runtime_error(dmcAxisFailureMessage("axis busy before dmc_home_move", -1, card, axisNo));
    }
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    const auto ret = dmcHomeMove(card, axisNo);
    if (ret != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_home_move", ret, card, axisNo));
    }
  }
#endif
  estopActive_ = false;
}

void LTDMCDriver::homeAll(const std::array<double, 12>& workOriginPulse) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetProfile || !dmcPMove || !dmcCheckDone) {
    throw std::runtime_error("required LTDMC motion exports missing");
  }
  constexpr double kTranslationMaxVelocityPulse = 10000.0;
  constexpr double kRotationMaxVelocityPulse = 5000.0;
  constexpr double kTranslationStartVelocityPulse = 1000.0;
  constexpr double kRotationStartVelocityPulse = 500.0;
  constexpr double kRampSec = 0.05;
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      const auto index = stateIndex(side, axis);
      const auto targetPulse = static_cast<long>(std::llround(workOriginPulse[index]));
      if (dmcCheckDone(card, axisNo) == 0) {
        throw std::runtime_error(dmcAxisFailureMessage("axis busy before home_all", -1, card, axisNo));
      }
      const auto rotation = isRotation(axis);
      const auto maxVelocityPulse = rotation ? kRotationMaxVelocityPulse : kTranslationMaxVelocityPulse;
      const auto startVelocityPulse = rotation ? kRotationStartVelocityPulse : kTranslationStartVelocityPulse;
      const auto retProfile =
          dmcSetProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, kRampSec, kRampSec, 0.0);
      if (retProfile != 0) {
        throw std::runtime_error(dmcFailureMessage("dmc_set_profile", retProfile, card, axisNo, targetPulse));
      }
      if (dmcSetSProfile) {
        dmcSetSProfile(card, axisNo, 0, 0.0);
      }
      const auto retMove = dmcPMove(card, axisNo, targetPulse, 1);
      if (retMove != 0) {
        throw std::runtime_error(dmcFailureMessage("dmc_pmove", retMove, card, axisNo, targetPulse));
      }
      pulse_[index] = static_cast<double>(targetPulse);
    }
  }
#else
  pulse_ = workOriginPulse;
#endif
  estopActive_ = false;
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
    }
  }
#ifdef APPSTATION_ENABLE_VENDOR_SDKS
  // TODO: map semantic axes to physicalAxis(side, axis), apply profiles, and submit motion commands.
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
  const auto rotation = isRotation(axis);
  // Hardware-test safety bound: keep relative jogs from accidentally driving the
  // axis off-stage. Higher-level callers should also enforce per-task limits.
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
  const auto card = side == Side::Left ? static_cast<unsigned short>(1) : static_cast<unsigned short>(0);
  const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
  const auto pulseScale = std::abs(pulsePerUnit(side, axis));
  // pulsePerUnit is pulses-per-mm for translation, pulses-per-degree for
  // rotation. UI velocity is um/s or deg/s respectively, so the translation
  // form needs a /1000 to convert mm->um.
  const auto velocityScale = rotation ? pulseScale : pulseScale / 1000.0;
  const auto maxVelocityPulse = (std::max)(1.0, maxVelocityUiPerSec * velocityScale);
  double startVelocityPulse;
  if (startVelocityUiPerSec > 0) {
    startVelocityPulse = (std::max)(1.0, startVelocityUiPerSec * velocityScale);
  } else {
    startVelocityPulse = (std::max)(1.0, maxVelocityPulse * 0.2);
  }
  // The LTDMC firmware refuses Min_Vel == Max_Vel on dmc_set_profile (returns
  // a parameter error and the next dmc_pmove silently uses leftover profile
  // state). Force a small headroom so the SDK always gets a real ramp.
  if (startVelocityPulse >= maxVelocityPulse) {
    startVelocityPulse = (std::max)(1.0, maxVelocityPulse * 0.5);
  }
  // dmc_set_profile takes Tacc/Tdec in seconds (time to ramp from start->max).
  // If the caller did not pass a positive value, default to a safe 50ms ramp.
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
  estopActive_ = false;
}

void LTDMCDriver::configureStageAxes(Side side) {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetPulseOutmode || !dmcSetElMode || !dmcSetHomeMode || !dmcSetHomePinLogic) {
    throw std::runtime_error("required LTDMC homing configuration exports missing");
  }
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
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
  for (const int yawIndex : {5, 11}) {
    if (std::abs(targetUi[yawIndex]) > 7.5) {
      throw std::runtime_error("yaw target exceeds +/-7.5 degree safety limit");
    }
  }
}

}  // namespace appstation::hal
