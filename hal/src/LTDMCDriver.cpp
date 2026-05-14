#include "LTDMCDriver.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <sstream>

#ifdef _WIN32
#include <windows.h>
#endif

namespace appstation::hal {

namespace {
std::int64_t unixTimeMs() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

#ifdef _WIN32
using DmcBoardInit = short(__stdcall*)();
using DmcGetPosition = long(__stdcall*)(unsigned short, unsigned short);
using DmcStop = short(__stdcall*)(unsigned short, unsigned short, unsigned short);
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

HMODULE ltdmcModule = nullptr;
DmcBoardInit dmcBoardInit = nullptr;
DmcGetPosition dmcGetPosition = nullptr;
DmcStop dmcStop = nullptr;
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

int stageAxisCount(appstation::hal::Side side) {
  return side == appstation::hal::Side::Left ? 6 : 9;
}

bool usesSevonPin(appstation::hal::Side side, appstation::hal::SemanticAxis axis) {
  return physicalAxis(side, axis) < 8;
}

double clipTeleopTargetToLimit(double targetUi, const AxisLimit& limit) {
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

void updateTeleopTargetBestEffort(unsigned short card, unsigned short axisNo, long targetPulse) {
  // Match ICF teleop: target refresh return codes must not break the 10 ms command loop.
  const auto retUpdate = dmcUpdateTargetPosition(card, axisNo, targetPulse, 1);
  (void)retUpdate;
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
    return false;
  }
  dmcBoardInit = reinterpret_cast<DmcBoardInit>(GetProcAddress(ltdmcModule, "dmc_board_init"));
  dmcGetPosition = reinterpret_cast<DmcGetPosition>(GetProcAddress(ltdmcModule, "dmc_get_position"));
  dmcStop = reinterpret_cast<DmcStop>(GetProcAddress(ltdmcModule, "dmc_stop"));
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
      for (auto& value : teleopTargetActive_) {
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
  state.readTimestampMs = unixTimeMs();
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
      if (dmcReadSevonPin && usesSevonPin(side, axis)) {
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
    for (const auto side : {Side::Left, Side::Right}) {
      const auto card = cardForSide(side);
      for (int axisNo = 0; axisNo < stageAxisCount(side); ++axisNo) {
        dmcStop(card, static_cast<unsigned short>(axisNo), 0);
      }
    }
  }
#endif
  estopActive_ = true;
  for (auto& active : teleopTargetActive_) {
    active = false;
  }
}

std::string LTDMCDriver::enableSide(Side side, bool enabled) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
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
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (!enabled && dmcCheckDone(card, axisNo) == 0) {
      if (failed > 0) {
        failures << "; ";
      }
      failures << dmcAxisFailureMessage("disable servo while axis moving", -1, card, axisNo);
      enabled_[stateIndex(side, axis)] = true;
      ++failed;
      continue;
    }
    if (!usesSevonPin(side, axis)) {
      enabled_[stateIndex(side, axis)] = enabled;
      ++succeeded;
      continue;
    }
    const auto ret = dmcWriteSevonPin(card, axisNo, enabled ? 1 : 0);
    if (ret != 0) {
      if (failed > 0) {
        failures << "; ";
      }
      failures << dmcAxisFailureMessage("dmc_write_sevon_pin", ret, card, axisNo);
      enabled_[stateIndex(side, axis)] = false;
      ++failed;
      continue;
    }
    enabled_[stateIndex(side, axis)] = enabled;
    ++succeeded;
  }
#else
  (void)side;
  (void)enabled;
  succeeded = 6;
#endif
  estopActive_ = false;
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    teleopTargetActive_[stateIndex(side, static_cast<SemanticAxis>(axisIndex))] = false;
  }
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
  for (auto& active : teleopTargetActive_) {
    active = false;
  }
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
  estopActive_ = false;
}

void LTDMCDriver::updateTeleopTargetUi(
    Side side,
    const std::array<double, 6>& deltaUi,
    double translationStepPulse,
    double rotationStepPulse,
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
  if (translationVelocityUiPerSec <= 0 || rotationVelocityUiPerSec <= 0) {
    throw std::runtime_error("teleop velocity must be positive");
  }
  if (translationStepPulse <= 0 || rotationStepPulse <= 0) {
    throw std::runtime_error("teleop pulse step limit must be positive");
  }
  const double tacc = accTimeSec > 0 ? accTimeSec : 0.05;
  const double tdec = decTimeSec > 0 ? decTimeSec : 0.05;
  const auto card = cardForSide(side);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcSetProfile || !dmcCheckDone || !dmcUpdateTargetPosition) {
    throw std::runtime_error("required LTDMC teleop exports missing: set_profile/check_done/update_target_position");
  }
#endif
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    const auto delta = deltaUi[axisIndex];
    const auto rotation = isRotation(axis);
    const auto index = stateIndex(side, axis);
    if (!enabledAxes[axisIndex]) {
      teleopTargetActive_[index] = false;
      continue;
    }
    const auto stepLimitPulse = rotation ? rotationStepPulse : translationStepPulse;
    const auto requestedDeltaPulse = static_cast<long>(std::llround(uiToPulse(delta, side, axis)));
    const auto deltaPulse = clampPulseStep(requestedDeltaPulse, stepLimitPulse);
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (deltaPulse == 0) {
      if (syncZeroDeltaTarget) {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
        if (dmcGetPosition) {
          pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
        }
        const auto updateTargetPulse = static_cast<long>(std::llround(pulse_[index]));
        updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse);
#endif
        teleopTargetPulse_[index] = pulse_[index];
        teleopTargetActive_[index] = true;
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
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    const bool moving = dmcCheckDone(card, axisNo) == 0;
    if (!teleopTargetActive_[index] && dmcGetPosition) {
      pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));
    }
#else
    const bool moving = false;
#endif
    const auto basePulse = teleopTargetActive_[index] ? teleopTargetPulse_[index] : pulse_[index];
    const auto limit = limits[axisIndex];
    const auto unclippedTargetPulse = basePulse + static_cast<double>(deltaPulse);
    const auto unclippedTargetUi = pulseToUi(unclippedTargetPulse, side, axis);
    const auto targetUi = clipTeleopTargetToLimit(unclippedTargetUi, limit);
    const auto targetPulse = uiToPulse(targetUi, side, axis);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    if (!moving) {
      applyMotionProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, tacc, tdec, deltaPulse);
    }
    const auto updateTargetPulse = static_cast<long>(std::llround(targetPulse));
    updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse);
#else
    (void)card;
    (void)axisNo;
    (void)moving;
    (void)maxVelocityPulse;
    (void)startVelocityPulse;
    (void)tacc;
    (void)tdec;
#endif
    teleopTargetPulse_[index] = targetPulse;
    teleopTargetActive_[index] = true;
    pulse_[index] = targetPulse;
  }
  estopActive_ = false;
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
  if (enabled_[stateIndex(side, axis)]) {
    return true;
  }
  if (usesSevonPin(side, axis)) {
    return false;
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto candidate = static_cast<SemanticAxis>(axisIndex);
    if (usesSevonPin(side, candidate) && enabled_[stateIndex(side, candidate)]) {
      return true;
    }
  }
  return false;
}

}  // namespace appstation::hal
