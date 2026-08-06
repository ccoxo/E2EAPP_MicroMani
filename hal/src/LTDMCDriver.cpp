#include "LTDMCDriver.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <utility>

#ifdef _WIN32
#include <windows.h>
#endif

namespace appstation::hal {

namespace {
// 这些默认掩码只表示“本次请求覆盖全部 6 个语义轴”，不代表硬件实际都已使能。
constexpr std::array<bool, 6> kAllAxesEnabled{true, true, true, true, true, true};
constexpr unsigned short kHomeDirection = 0;

std::int64_t unixTimeMs() {
  // 对外状态使用墙钟毫秒，便于和后端、前端日志时间线对齐。
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

#ifdef _WIN32
// LTDMC SDK 通过 DLL 导出 C 接口。这里用函数指针动态绑定，避免构建时强依赖 import lib。
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
using DmcGetStopReason = short(__stdcall*)(unsigned short, unsigned short, long*);

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
DmcGetStopReason dmcGetStopReason = nullptr;
#endif

// 以下错误消息保留 card、axis、pulse 等现场排障信息，避免只看到 vendor 返回码。
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
// 回工作原点时允许 100 pulse 以内视为已经到位，避免微小误差导致无意义运动。
constexpr long kWorkOriginSettledPulseTolerance = 100;

void startWorkOriginMoveOrThrow(
    unsigned short card,
    unsigned short axisNo,
    long targetPulse,
    long deltaPulse,
    long currentPulse) {
  // 目标足够接近时直接跳过，让回原点流程对已经到位的轴保持幂等。
  if (std::abs(deltaPulse) <= kWorkOriginSettledPulseTolerance) {
    return;
  }
  // 优先用绝对运动到目标脉冲；部分现场配置不支持时再退回相对运动。
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
  // teleop 错误需要同时记录逻辑目标、软限位和轴运动状态，方便复盘是哪一层拒绝。
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
  // 现场接线约定：左侧机构接 1 号控制卡，右侧机构接 0 号控制卡。
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

std::string boolArray6LogValue(const std::array<bool, 6>& values) {
  std::ostringstream out;
  out << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << (values[i] ? "true" : "false");
  }
  out << "]";
  return out.str();
}

void logHardwareHomeDiagnostic(
    const char* event,
    appstation::hal::Side side,
    appstation::hal::SemanticAxis axis,
    unsigned short card,
    unsigned short physicalAxisNo,
    unsigned short homeDir,
    long beforePulse,
    double beforeUi,
    const std::array<bool, 6>& enabledAxes,
    long afterPulse,
    double afterUi,
    short ret) {
  std::cout << "[HAL] INFO component=MOTION"
            << " event=" << event
            << " side=" << sideName(side)
            << " card=" << card
            << " semanticAxis=" << axisName(axis)
            << " physicalAxis=" << physicalAxisNo
            << " homeDir=" << homeDir
            << " beforePulse=" << beforePulse
            << " beforeUi=" << beforeUi
            << " enabledAxes=" << boolArray6LogValue(enabledAxes)
            << " afterPulse=" << afterPulse
            << " afterUi=" << afterUi
            << " deltaPulse=" << (afterPulse - beforePulse)
            << " deltaUi=" << (afterUi - beforeUi)
            << " ret=" << ret
            << " sample=command_returned"
            << std::endl;
}

int stageAxisCount(appstation::hal::Side side) {
  // 左侧控制卡只接 0-5 轴；右侧现场接线包含 0-8 轴，其中语义轴只映射部分物理轴。
  return side == appstation::hal::Side::Left ? 6 : 9;
}

bool usesSevonPin(appstation::hal::Side side, appstation::hal::SemanticAxis axis) {
  // 物理轴号超出该侧实际轴数时，不能访问伺服 IO。
  return physicalAxis(side, axis) < stageAxisCount(side);
}

bool hasReadableSevonFeedback(appstation::hal::Side side, appstation::hal::SemanticAxis axis) {
  // 右侧伺服反馈在现场环境不可稳定读取，因此以软件命令状态作为反馈。
  if (side == appstation::hal::Side::Right) {
    return false;
  }
  return usesSevonPin(side, axis);
}

bool ignoreUnsupportedSevonWriteFailure(
    appstation::hal::Side side,
    appstation::hal::SemanticAxis axis,
    short ret) {
  // 右侧部分轴写 sevon 会返回 2 表示不支持，但实际运动链路仍可按软件状态继续。
  return side == appstation::hal::Side::Right && usesSevonPin(side, axis) && ret == 2;
}

double clipTeleopTargetToLimit(double baseUi, double targetUi, const AxisLimit& limit) {
  // base 已经在限位外时，只允许朝限位区间方向回退，禁止继续向外推。
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
  // 平移 UI 单位是 um/s，而 pulsePerUnit 是 pulse/mm；旋转 UI 单位直接是 deg/s。
  const auto pulseScale = std::abs(pulsePerUnit(side, axis));
  const auto velocityScale = isRotation(axis) ? pulseScale : pulseScale / 1000.0;
  return (std::max)(1.0, velocityUiPerSec * velocityScale);
}

long clampPulseStep(long deltaPulse, double stepLimitPulse) {
  // 单帧限幅保护 teleop 高频环路，防止主手突跳生成过大的目标窗口。
  const auto limit = static_cast<long>(std::llround(stepLimitPulse));
  if (limit <= 0 || std::abs(deltaPulse) <= limit) {
    return deltaPulse;
  }
  return deltaPulse > 0 ? limit : -limit;
}

int signOfPulseDelta(double value) {
  if (value > 0.5) {
    return 1;
  }
  if (value < -0.5) {
    return -1;
  }
  return 0;
}

long maxTeleopTargetLeadPulse(
    long deltaPulse,
    double maxVelocityPulse,
    double accTimeSec,
    double decTimeSec,
    double stepLimitPulse) {
  // 目标提前量不能无限领先实际位置，否则 update_target_position 会错过控制卡可更新窗口。
  const auto requested = static_cast<long>(std::abs(deltaPulse));
  const double leadTimeSec = (std::max)(0.01, (std::max)(accTimeSec, decTimeSec));
  const auto velocityLead = (std::max)(
      1L,
      static_cast<long>(std::llround((std::max)(1.0, maxVelocityPulse) * leadTimeSec)));
  const auto stepLimit = static_cast<long>(std::llround(stepLimitPulse));
  const auto boundedVelocityLead = stepLimit > 0 ? (std::min)(velocityLead, stepLimit) : velocityLead;
  return (std::max)(1L, (std::max)(requested, boundedVelocityLead));
}

double clampTeleopTargetLead(double actualPulse, double targetPulse, long targetLeadPulse) {
  const auto lead = static_cast<double>((std::max)(1L, targetLeadPulse));
  return std::clamp(targetPulse, actualPulse - lead, actualPulse + lead);
}

bool teleopTargetUpdateMissedWindow(int updateReturn) {
  // 3011/3019 是现场观测到的目标刷新窗口错误，处理策略是重新发起运动段。
  return updateReturn == 3011 || updateReturn == 3019;
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
  // 所有运动前都显式设置 profile，避免控制卡沿用上一条不同速度的运动参数。
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
  // 匹配 ICF teleop：目标刷新返回码不应直接打断 10ms 控制环，而是进入诊断结果。
  const auto retUpdate = dmcUpdateTargetPosition(card, axisNo, targetPulse, 1);
  return retUpdate;
}

struct AxisHoldResult {
  double pulse{0.0};
  int updateReturn{0};
};

AxisHoldResult stopTeleopAxisAtCurrentBestEffort(unsigned short card, unsigned short axisNo) {
  // 停轴后立即把目标更新到当前位置，避免控制卡仍保留旧目标造成下一帧突跳。
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
  // 回原点需要等待多轴完成；统一超时避免某个轴异常时接口永久阻塞。
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
  // HalServer 运行目录或 PATH 中必须能找到 LTDMC.dll；所有导出在启动时一次性绑定。
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
  dmcGetStopReason = reinterpret_cast<DmcGetStopReason>(GetProcAddress(ltdmcModule, "dmc_get_stop_reason"));
  // 初始化只要求核心运动读写导出存在；诊断 IO 导出允许缺失并在 JSON 中返回 null。
  if (!dmcBoardInit || !dmcGetPosition || !dmcSetProfile || !dmcPMove || !dmcCheckDone) {
    lastError_ = "required LTDMC exports missing: board_init/get_position/set_profile/pmove/check_done";
    initialized_ = false;
    publishStateSnapshotLocked();
    return false;
  }
  const auto boards = dmcBoardInit();
  // 现场左右从端分别占用两张控制卡，少于 2 张时不允许进入真实运动模式。
  initialized_ = boards >= 2;
  if (!initialized_) {
    lastError_ = "dmc_board_init found fewer than 2 boards";
  } else {
    try {
      configureStageAxes(Side::Left);
      configureStageAxes(Side::Right);
      // 初始化后不默认使能任何轴，必须由后端/操作者显式 enable。
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
  // /health 不能因为运动线程持锁而阻塞；抢不到锁时返回最近发布的缓存。
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
  if (estopActive()) {
    throw std::runtime_error("emergency stop active; acknowledge safety before returning to work origin");
  }
}

bool LTDMCDriver::estopActive() const {
  return estopActive_.load(std::memory_order_acquire);
}

MotionState LTDMCDriver::readState() {
  // 高频状态读取使用 try_lock，避免和运动命令争锁造成控制路径抖动。
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
      // 无可靠伺服反馈的轴使用 commandedEnabled_，防止 UI 把不可读误判为未使能。
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
  // 缓存返回时更新时间戳和急停状态，表示“响应时间”仍是当前时刻。
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
  // 调用方已持有 mutex_；这里把内部 pulse/enabled 状态转换成对外 MotionState。
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
  // 快照缓存使用独立锁，避免健康检查/状态读取阻塞真实 SDK 调用。
  std::scoped_lock snapshotLock(snapshotMutex_);
  cachedState_ = state;
  cachedInitialized_ = initialized_;
  cachedLastError_ = lastError_;
}

void LTDMCDriver::emergencyStop() {
  // sequence 用于区分多次急停/解除路径，避免旧操作完成后误清新急停。
  estopSequence_.fetch_add(1, std::memory_order_acq_rel);
  estopActive_.store(true, std::memory_order_release);
  stopAllAxesBestEffort();
  disableAllAxesBestEffort();

  // 急停路径不能等待主控制锁；抢不到锁时硬件已尽力停止，软件缓存下次再刷新。
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

void LTDMCDriver::acknowledgeEmergencyStop() {
  // Invalidate older in-flight operations before clearing the current latch.
  estopSequence_.fetch_add(1, std::memory_order_acq_rel);
  estopActive_.store(false, std::memory_order_release);
  std::unique_lock<std::mutex> stateLock(mutex_, std::try_to_lock);
  if (stateLock.owns_lock()) {
    publishStateSnapshotLocked();
  }
}

void LTDMCDriver::stopAllAxesBestEffort() noexcept {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  // 先尝试整卡急停，再逐轴 stop，尽可能覆盖不同 LTDMC 固件行为。
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
  // 急停后尽力关闭所有实际轴的伺服输出；该函数禁止抛异常。
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
  // 只有同一轮恢复动作可以清急停；期间如果又触发急停，sequence 会变化。
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
  if (enabled) {
    throwIfEstopActive();
  }
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
      // 轴运动中禁止关闭伺服，否则可能让机构失控或丢步。
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
      // 逻辑存在但物理无 sevon pin 的轴只更新软件状态。
      const auto index = stateIndex(side, axis);
      enabled_[index] = axisEnabled;
      commandedEnabled_[index] = axisEnabled;
      ++succeeded;
      continue;
    }
    const auto ret = dmcWriteSevonPin(card, axisNo, axisEnabled ? 1 : 0);
    if (ignoreUnsupportedSevonWriteFailure(side, axis, ret)) {
      // 现场右侧返回“不支持写入”时仍保留软件期望状态，后续运动使能按该状态判断。
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
  // 伺服状态变化后旧 teleop 目标不再可信，下一帧必须重新建立目标。
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

void LTDMCDriver::homeSide(Side side, const std::array<bool, 6>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  throwIfEstopActive();
  const auto estopSequenceAtStart = estopSequence_.load(std::memory_order_acquire);
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dmcHomeMove || !dmcSetPulseOutmode || !dmcSetElMode || !dmcSetHomeMode || !dmcSetHomePinLogic) {
    throw std::runtime_error("required LTDMC home exports missing");
  }
  // 回机械原点前重新配置脉冲、限位和原点模式，保证控制卡处于预期状态。
  configureStageAxes(side);
  const auto card = cardForSide(side);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    if (dmcCheckDone(card, axisNo) == 0) {
      // homing 不与现有运动叠加，必须等轴空闲。
      throw std::runtime_error(dmcAxisFailureMessage("axis busy before dmc_home_move", -1, card, axisNo));
    }
  }
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    const auto beforePulse = dmcGetPosition ? dmcGetPosition(card, axisNo) : static_cast<long>(pulse_[stateIndex(side, axis)]);
    const auto beforeUi = pulseToUi(static_cast<double>(beforePulse), side, axis);
    logHardwareHomeDiagnostic(
        "home_start",
        side,
        axis,
        card,
        axisNo,
        kHomeDirection,
        beforePulse,
        beforeUi,
        enabledAxes,
        beforePulse,
        beforeUi,
        0);
    const auto ret = dmcHomeMove(card, axisNo);
    const auto afterPulse = dmcGetPosition ? dmcGetPosition(card, axisNo) : static_cast<long>(pulse_[stateIndex(side, axis)]);
    const auto afterUi = pulseToUi(static_cast<double>(afterPulse), side, axis);
    logHardwareHomeDiagnostic(
        "home_done",
        side,
        axis,
        card,
        axisNo,
        kHomeDirection,
        beforePulse,
        beforeUi,
        enabledAxes,
        afterPulse,
        afterUi,
        ret);
    if (ret != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_home_move", ret, card, axisNo));
    }
  }
#endif
  clearEstopIfUnchanged(estopSequenceAtStart);
  // 机械回零会改变坐标参考，全部 teleop 目标都必须失效。
  for (auto& active : teleopTargetActive_) {
    active = false;
  }
  publishStateSnapshotLocked();
}

void LTDMCDriver::homeAll(
    const std::array<double, 12>& workOriginPulse,
    const std::array<std::array<bool, 6>, 2>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  ensureMotionReturnAllowed();
  throwIfEstopActive();
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
  // 先收集所有参与回工作原点的轴，统一检查伺服和忙碌状态。
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      if (!enabledAxes[sideIndex][axisIndex]) {
        continue;
      }
      if (!axisMotionEnabled(side, axis)) {
        throw std::runtime_error("motion axis is not servo-enabled; enable required axes before returning to work origin");
      }
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      homeAxes[homeAxisCount++] = {card, axisNo};
    }
  }
  waitForAxesDone(homeAxes, homeAxisCount, "home_all pre-move", 3000);
  // 每轴使用相同保守 profile，按目标脉冲绝对移动到工作原点。
  for (int sideIndex = 0; sideIndex < 2; ++sideIndex) {
    const auto side = sideIndex == 0 ? Side::Left : Side::Right;
    const auto card = cardForSide(side);
    for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      if (!enabledAxes[sideIndex][axisIndex]) {
        continue;
      }
      const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
      const auto index = stateIndex(side, axis);
      const auto targetPulse = static_cast<long>(std::llround(workOriginPulse[index]));
      const auto currentPulse = dmcGetPosition(card, axisNo);
      const auto deltaPulse = targetPulse - currentPulse;
      // 平移轴和旋转轴的 UI 速度单位不同，velocityToPulsePerSec 负责换算到 pulse/s。
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
  // 运动完成后重新读取真实位置，避免缓存只停留在理论目标值。
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
      const auto axis = static_cast<SemanticAxis>(axisIndex);
      if (!enabledAxes[sideIndex][axisIndex]) {
        continue;
      }
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

void LTDMCDriver::homeOriginSide(
    Side side,
    const std::array<double, 6>& workOriginPulse,
    const std::array<bool, 6>& enabledAxes) {
  std::scoped_lock lock(mutex_);
  ensureInitialized();
  ensureMotionReturnAllowed();
  throwIfEstopActive();
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
  // 单侧回工作原点与 homeAll 逻辑一致，但只处理调用方指定侧。
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!enabledAxes[axisIndex]) {
      continue;
    }
    if (!axisMotionEnabled(side, axis)) {
      throw std::runtime_error("motion axis is not servo-enabled; enable required axes before returning to work origin");
    }
    const auto axisNo = static_cast<unsigned short>(physicalAxis(side, axis));
    homeAxes[homeAxisCount++] = {card, axisNo};
  }
  waitForAxesDone(homeAxes, homeAxisCount, "home_origin_side pre-move", 3000);
  for (int axisIndex = 0; axisIndex < 6; ++axisIndex) {
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!enabledAxes[axisIndex]) {
      continue;
    }
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
    const auto axis = static_cast<SemanticAxis>(axisIndex);
    if (!enabledAxes[axisIndex]) {
      continue;
    }
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
  throwIfEstopActive();
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
    // 真实控制卡无法执行 0 pulse jog，把它作为调用方输入过小的错误暴露出来。
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
  throwIfEstopActive();
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
    // 诊断数组先写入请求和当前目标，后续每个分支再补实际执行结果。
    result.requestedDeltaUi[axisIndex] = delta;
    result.targetPulse[axisIndex] = actualPulse;
    result.targetUi[axisIndex] = pulseToUi(actualPulse, side, axis);
    if (!enabledAxes[axisIndex]) {
      // 被软件掩码禁用的轴不更新目标，防止重新使能后继续沿旧目标运动。
      teleopTargetActive_[index] = false;
      continue;
    }
    const auto stepLimitPulse = rotation ? rotationStepPulse : translationStepPulse;
    const auto pulseDeadband = rotation ? rotationPulseDeadband : translationPulseDeadband;
    const auto requestedDeltaPulse = static_cast<long>(std::llround(uiToPulse(delta, side, axis)));
    const auto deadbandedDeltaPulse =
        std::abs(requestedDeltaPulse) <= static_cast<long>(std::llround(pulseDeadband)) ? 0 : requestedDeltaPulse;
    // deadband 后再做单帧限幅，先去抖再保护步长。
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
      // 如果轴已在动但本地没有目标缓存，先把目标贴回实际位置，避免下一帧凭空续推。
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
        // 主手回到死区时，把运动目标同步到当前位置，相当于“松手即保持”。
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
    const auto activeTargetLead = teleopTargetActive_[index] ? teleopTargetPulse_[index] - actualPulse : 0.0;
    // 方向反转时不能继续基于旧目标续推，否则会先冲向旧目标再反向。
    const bool reversingTargetLead =
        signOfPulseDelta(activeTargetLead) != 0
        && signOfPulseDelta(deltaPulse) != 0
        && signOfPulseDelta(activeTargetLead) != signOfPulseDelta(deltaPulse);
    const auto targetBasePulse = teleopTargetActive_[index] && !reversingTargetLead
        ? teleopTargetPulse_[index] : actualPulse;
    const auto targetLeadPulse = maxTeleopTargetLeadPulse(
        deltaPulse,
        maxVelocityPulse,
        tacc,
        tdec,
        stepLimitPulse);
    const auto unclippedTargetPulse = targetBasePulse + static_cast<double>(deltaPulse);
    const auto unclippedTargetUi = pulseToUi(unclippedTargetPulse, side, axis);
    const auto targetUi = clipTeleopTargetToLimit(baseUi, unclippedTargetUi, limit);
    const auto targetPulse = uiToPulse(targetUi, side, axis);
    const auto leadLimitedTargetPulse = clampTeleopTargetLead(actualPulse, targetPulse, targetLeadPulse);
    // 控制卡目标使用整数脉冲；applied* 记录取整后的实际目标。
    const auto updateTargetPulse = static_cast<long>(std::llround(leadLimitedTargetPulse));
    const auto appliedTargetPulse = static_cast<double>(updateTargetPulse);
    const auto appliedTargetUi = pulseToUi(appliedTargetPulse, side, axis);
    const bool targetHeldAtBase = std::abs(appliedTargetPulse - basePulse) <= 0.5;
    if (std::abs(targetUi - unclippedTargetUi) > 1e-9) {
      result.clipped[axisIndex] = true;
    }
    if (targetHeldAtBase && !moving) {
      // 限位或取整导致目标没有变化且轴未动，保持目标缓存即可，不启动新运动。
      result.appliedDeltaPulse[axisIndex] = 0.0;
      result.appliedDeltaUi[axisIndex] = 0.0;
      result.targetPulse[axisIndex] = actualPulse;
      result.targetUi[axisIndex] = pulseToUi(actualPulse, side, axis);
      teleopTargetPulse_[index] = actualPulse;
      teleopTargetActive_[index] = true;
      continue;
    }
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    if (targetHeldAtBase && moving) {
      // 目标被压回当前位置但轴仍在动，需要主动 stop 并更新保持目标。
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
#endif
    const bool shouldLaunchMove = !moving;
    const auto launchDeltaPulse = static_cast<long>(std::llround(appliedTargetPulse - actualPulse));
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
    result.moveStarted[axisIndex] = shouldLaunchMove;
    result.launchDeltaPulse[axisIndex] = shouldLaunchMove ? static_cast<double>(launchDeltaPulse) : 0.0;
    if (shouldLaunchMove) {
      // 轴空闲时必须先设置 profile，再启动/刷新目标。
      applyMotionProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, tacc, tdec, launchDeltaPulse);
    }
    const auto updateReturn = updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse);
    result.updateReturn[axisIndex] = static_cast<double>(updateReturn);
    if (moving && teleopTargetUpdateMissedWindow(updateReturn)) {
      // 如果刷新窗口错过，读取当前位置后重新启动一个相对运动段追向目标。
      const auto relaunchCurrentPulse = static_cast<double>(dmcGetPosition(card, axisNo));
      const auto relaunchDeltaPulse =
          static_cast<long>(std::llround(appliedTargetPulse - relaunchCurrentPulse));
      if (relaunchDeltaPulse != 0) {
        applyMotionProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, tacc, tdec, relaunchDeltaPulse);
        result.moveStarted[axisIndex] = true;
        result.launchDeltaPulse[axisIndex] = static_cast<double>(relaunchDeltaPulse);
        result.updateReturn[axisIndex] =
            static_cast<double>(updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse));
      }
      actualPulse = relaunchCurrentPulse;
      pulse_[index] = actualPulse;
      result.currentPulse[axisIndex] = actualPulse;
    }
#else
    (void)card;
    (void)axisNo;
    (void)moving;
    (void)maxVelocityPulse;
    (void)startVelocityPulse;
    (void)tacc;
    (void)tdec;
    result.moveStarted[axisIndex] = !moving || !teleopTargetActive_[index];
    result.launchDeltaPulse[axisIndex] =
        result.moveStarted[axisIndex] ? static_cast<double>(launchDeltaPulse) : 0.0;
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
      // stopTeleopSide 是“停止遥操作保持当前位置”，不是急停；停止后仍同步目标到当前位置。
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
    // 5 是现场验证过的脉冲输出模式；限位/原点逻辑也在这里统一设定。
    const auto retPulse = dmcSetPulseOutmode(card, axisNo, 5);
    if (retPulse != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_set_pulse_outmode", retPulse, card, axisNo));
    }
    const auto retEl = dmcSetElMode(card, axisNo, 1, 1, 0);
    if (retEl != 0) {
      throw std::runtime_error(dmcAxisFailureMessage("dmc_set_el_mode", retEl, card, axisNo));
    }
    const auto retHome = dmcSetHomeMode(card, axisNo, kHomeDirection, 1.0, 0, 1);
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

void LTDMCDriver::throwIfEstopActive() const {
  if (estopActive()) {
    throw std::runtime_error("emergency stop active; acknowledge safety before motion commands");
  }
}

bool LTDMCDriver::axisMotionEnabled(Side side, SemanticAxis axis) const {
  // 运动许可读软件缓存，兼容没有可靠 sevon 反馈的轴。
  return enabled_[stateIndex(side, axis)];
}

}  // namespace appstation::hal
