#include "Omega7Driver.h"

#ifdef _WIN32
#include <windows.h>
#endif

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cmath>
#include <sstream>
#include <utility>
#include <vector>

namespace appstation::hal {

namespace {
std::int64_t unixTimeMs() {
  // 状态时间戳使用墙钟毫秒，方便前端和后端日志直接对齐。
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

#ifdef _WIN32
// Force Dimension SDK 通过 DLL 导出 C 接口。这里声明函数指针类型，
// 后面用 GetProcAddress 动态绑定，避免编译期强依赖具体 SDK import lib。
using DhdGetDeviceCount = int(__stdcall*)();
using DhdGetAvailableCount = int(__stdcall*)();
using DhdOpenID = int(__stdcall*)(char);
using DhdGetSerialNumber = int(__stdcall*)(unsigned short*, char);
using DhdGetSystemName = const char* (__stdcall*)(char);
using DhdIsLeftHanded = bool(__stdcall*)(char);
using DhdGetButton = int(__stdcall*)(int, char);
using DhdGetPositionAndOrientationDeg = int(__stdcall*)(double*, double*, double*, double*, double*, double*, char);
using DhdGetGripperGap = int(__stdcall*)(double*, char);
using DhdGetGripperAngleDeg = int(__stdcall*)(double*, char);
using DhdGetGripperEncoder = int(__stdcall*)(int*, char);
using DhdGripperEncoderToGap = int(__stdcall*)(int, double*, char);
using DhdEnableExpertMode = int(__stdcall*)();
using DhdEnableForce = int(__stdcall*)(unsigned char, char);
using DhdSetGravityCompensation = int(__stdcall*)(int, char);
using DhdSetStandardGravity = int(__stdcall*)(double, char);
using DhdSetForceAndTorqueAndGripperForce =
    int(__stdcall*)(double, double, double, double, double, double, double, char);
using DhdErrorGetLastStr = const char* (__stdcall*)();

HMODULE dhdModule = nullptr;
DhdGetDeviceCount dhdGetDeviceCount = nullptr;
DhdGetAvailableCount dhdGetAvailableCount = nullptr;
DhdOpenID dhdOpenID = nullptr;
DhdGetSerialNumber dhdGetSerialNumber = nullptr;
DhdGetSystemName dhdGetSystemName = nullptr;
DhdIsLeftHanded dhdIsLeftHanded = nullptr;
DhdGetButton dhdGetButton = nullptr;
DhdGetPositionAndOrientationDeg dhdGetPositionAndOrientationDeg = nullptr;
DhdGetGripperGap dhdGetGripperGap = nullptr;
DhdGetGripperAngleDeg dhdGetGripperAngleDeg = nullptr;
DhdGetGripperEncoder dhdGetGripperEncoder = nullptr;
DhdGripperEncoderToGap dhdGripperEncoderToGap = nullptr;
DhdEnableExpertMode dhdEnableExpertMode = nullptr;
DhdEnableForce dhdEnableForce = nullptr;
DhdSetGravityCompensation dhdSetGravityCompensation = nullptr;
DhdSetStandardGravity dhdSetStandardGravity = nullptr;
DhdSetForceAndTorqueAndGripperForce dhdSetForceAndTorqueAndGripperForce = nullptr;
DhdErrorGetLastStr dhdErrorGetLastStr = nullptr;

constexpr double kStandardGravity = 9.81;
constexpr double kOmega7GripperOpenDeg = 30.0;
constexpr double kOmega7GripperOpenMm = 25.0;
// 有些 SDK/设备在开口读取失败时会返回接近 0 的噪声值，低于该阈值不作为可信开口。
constexpr double kOmega7GripperGapMinReliableMm = 0.001;

// SDK 调用失败时统一取最近错误。dhdErrorGetLastStr 本身也是可选导出，
// 因此调用前需要判空，避免错误路径再次崩溃。
std::string sdkError() {
  if (!dhdErrorGetLastStr) {
    return "no SDK error string available";
  }
  const char* message = dhdErrorGetLastStr();
  return message ? std::string(message) : std::string("unknown SDK error");
}
#endif

double normalizeGravityScale(double value, double fallback) {
  if (!std::isfinite(value)) {
    return fallback;
  }
  return std::clamp(value, 0.0, 1.0);
}
}  // namespace

bool Omega7Driver::initialize(int leftOpenId, int rightOpenId, bool swapHands) {
  std::scoped_lock lock(mutex_);
  leftOpenId_ = leftOpenId;
  rightOpenId_ = rightOpenId;
  swapHands_ = swapHands;
  const auto forceOutputEnabled = forceOutputEnabled_;
  const auto gravityScale = gravityScale_;
  // initialize 可能被重复调用；先清空旧状态，后续任一步失败都保持未初始化。
  initialized_ = false;
  lastError_.clear();
  state_ = {};
  forceOutputEnabled_ = forceOutputEnabled;
  gravityScale_ = gravityScale;
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  // 优先加载 64 位 SDK DLL，失败时再尝试旧名称 dhd.dll。
  // HalServer 运行目录或 PATH 里必须能找到该 DLL。
  if (!dhdModule) {
    dhdModule = LoadLibraryA("dhd64.dll");
    if (!dhdModule) {
      dhdModule = LoadLibraryA("dhd.dll");
    }
  }
  if (!dhdModule) {
    lastError_ = "dhd64.dll/dhd.dll not found";
    return false;
  }
  // 动态绑定本文件实际使用到的 SDK 函数。只有基础枚举、打开设备、
  // 读位姿是硬需求；序列号、按钮、夹爪开口等能力允许缺失。
  dhdGetDeviceCount = reinterpret_cast<DhdGetDeviceCount>(GetProcAddress(dhdModule, "dhdGetDeviceCount"));
  dhdGetAvailableCount = reinterpret_cast<DhdGetAvailableCount>(GetProcAddress(dhdModule, "dhdGetAvailableCount"));
  dhdOpenID = reinterpret_cast<DhdOpenID>(GetProcAddress(dhdModule, "dhdOpenID"));
  dhdGetSerialNumber = reinterpret_cast<DhdGetSerialNumber>(GetProcAddress(dhdModule, "dhdGetSerialNumber"));
  dhdGetSystemName = reinterpret_cast<DhdGetSystemName>(GetProcAddress(dhdModule, "dhdGetSystemName"));
  dhdIsLeftHanded = reinterpret_cast<DhdIsLeftHanded>(GetProcAddress(dhdModule, "dhdIsLeftHanded"));
  dhdGetButton = reinterpret_cast<DhdGetButton>(GetProcAddress(dhdModule, "dhdGetButton"));
  dhdGetPositionAndOrientationDeg = reinterpret_cast<DhdGetPositionAndOrientationDeg>(
      GetProcAddress(dhdModule, "dhdGetPositionAndOrientationDeg"));
  dhdGetGripperGap = reinterpret_cast<DhdGetGripperGap>(GetProcAddress(dhdModule, "dhdGetGripperGap"));
  dhdGetGripperAngleDeg = reinterpret_cast<DhdGetGripperAngleDeg>(
      GetProcAddress(dhdModule, "dhdGetGripperAngleDeg"));
  dhdGetGripperEncoder = reinterpret_cast<DhdGetGripperEncoder>(
      GetProcAddress(dhdModule, "dhdGetGripperEncoder"));
  dhdGripperEncoderToGap = reinterpret_cast<DhdGripperEncoderToGap>(
      GetProcAddress(dhdModule, "dhdGripperEncoderToGap"));
  dhdEnableExpertMode = reinterpret_cast<DhdEnableExpertMode>(GetProcAddress(dhdModule, "dhdEnableExpertMode"));
  dhdEnableForce = reinterpret_cast<DhdEnableForce>(GetProcAddress(dhdModule, "dhdEnableForce"));
  dhdSetGravityCompensation = reinterpret_cast<DhdSetGravityCompensation>(
      GetProcAddress(dhdModule, "dhdSetGravityCompensation"));
  dhdSetStandardGravity = reinterpret_cast<DhdSetStandardGravity>(GetProcAddress(dhdModule, "dhdSetStandardGravity"));
  dhdSetForceAndTorqueAndGripperForce = reinterpret_cast<DhdSetForceAndTorqueAndGripperForce>(
      GetProcAddress(dhdModule, "dhdSetForceAndTorqueAndGripperForce"));
  dhdErrorGetLastStr = reinterpret_cast<DhdErrorGetLastStr>(GetProcAddress(dhdModule, "dhdErrorGetLastStr"));
  if (!dhdGetDeviceCount || !dhdOpenID || !dhdGetPositionAndOrientationDeg) {
    lastError_ = "required Force Dimension exports missing";
    return false;
  }

  // deviceCount 是 SDK 能枚举到的设备总数；availableCount 只是辅助诊断，
  // 某些 SDK 版本可能不导出该函数，所以缺失时退回 deviceCount。
  const int deviceCount = dhdGetDeviceCount();
  const int availableCount = dhdGetAvailableCount ? dhdGetAvailableCount() : deviceCount;
  if (deviceCount <= 0) {
    std::ostringstream error;
    error << "no Omega.7 detected; deviceCount=" << deviceCount << ", availableCount=" << availableCount
          << ", sdkError=" << sdkError();
    lastError_ = error.str();
    return false;
  }

  std::vector<Omega7State> openedDevices;
  std::ostringstream warnings;
  auto openDevice = [&](int requestedOpenId, const char* label) {
    if (requestedOpenId < 0) {
      return;
    }
    // dhdOpenID 真正打开 Omega.7 设备句柄。成功后返回 SDK 内部 deviceId，
    // 后续读取位姿、按钮、夹爪开口都使用这个 deviceId。
    const int deviceId = dhdOpenID(static_cast<char>(requestedOpenId));
    if (deviceId < 0) {
      warnings << label << " openId=" << requestedOpenId << " failed: " << sdkError() << "; ";
      return;
    }
    Omega7State item{};
    item.connected = true;
    item.openId = requestedOpenId;
    item.deviceId = deviceId;
    item.serial = "open-id-" + std::to_string(requestedOpenId);
    // 序列号、设备名、左右手属性用于前端展示和排障；获取失败不影响连接。
    if (dhdGetSerialNumber) {
      unsigned short serial = 0;
      if (dhdGetSerialNumber(&serial, static_cast<char>(deviceId)) >= 0) {
        item.serial = std::to_string(serial);
      }
    }
    if (dhdGetSystemName) {
      const char* name = dhdGetSystemName(static_cast<char>(deviceId));
      item.systemName = name ? std::string(name) : std::string();
    }
    if (dhdIsLeftHanded) {
      item.leftHanded = dhdIsLeftHanded(static_cast<char>(deviceId));
      item.handednessKnown = true;
    }
    openedDevices.push_back(item);
  };

  openDevice(leftOpenId, "left");
  // 如果只枚举到一台设备，避免用同一个 openId 重复打开右手。
  if (deviceCount > 1 && rightOpenId != leftOpenId) {
    openDevice(rightOpenId, "right");
  }

  auto takeDeviceByHandedness = [&](bool wantLeftHanded) {
    // 优先按 SDK 上报的左右手属性分配逻辑左右，比 openId 更贴近主手物理属性。
    for (auto it = openedDevices.begin(); it != openedDevices.end(); ++it) {
      if (it->handednessKnown && it->leftHanded == wantLeftHanded) {
        const auto item = *it;
        openedDevices.erase(it);
        return item;
      }
    }
    return Omega7State{};
  };

  auto takeDeviceByOpenId = [&](int openId) {
    // 部分 SDK 不支持左右手属性时，退回到配置中的 openId 顺序。
    for (auto it = openedDevices.begin(); it != openedDevices.end(); ++it) {
      if (it->openId == openId) {
        const auto item = *it;
        openedDevices.erase(it);
        return item;
      }
    }
    return Omega7State{};
  };

  state_[0] = takeDeviceByHandedness(true);
  state_[1] = takeDeviceByHandedness(false);
  if (!state_[0].connected) {
    state_[0] = takeDeviceByOpenId(leftOpenId);
  }
  if (!state_[1].connected) {
    state_[1] = takeDeviceByOpenId(rightOpenId);
  }
  if (!state_[0].connected && !openedDevices.empty()) {
    state_[0] = openedDevices.front();
    openedDevices.erase(openedDevices.begin());
  }
  if (!state_[1].connected && !openedDevices.empty()) {
    state_[1] = openedDevices.front();
    openedDevices.erase(openedDevices.begin());
  }
  if (swapHands) {
    // 最后再交换逻辑左右，保留前面的自动识别和 openId 兜底结果。
    std::swap(state_[0], state_[1]);
  }
  applyForceOutputUnlocked(0, forceOutputEnabled_[0]);
  applyForceOutputUnlocked(1, forceOutputEnabled_[1]);

  // 至少打开一台主手就认为 Omega7Driver 可用。只打开一台时保留 warning，
  // 让 /health 能提示“部分连接”而不是直接判整个模块不可用。
  const int openedCount = (state_[0].connected ? 1 : 0) + (state_[1].connected ? 1 : 0);
  initialized_ = openedCount > 0;
  if (!initialized_) {
    std::ostringstream error;
    error << "failed to open Omega.7 devices; deviceCount=" << deviceCount << ", availableCount=" << availableCount
          << ", " << warnings.str();
    lastError_ = error.str();
    return false;
  }
  if (openedCount < 2) {
    std::ostringstream message;
    message << "partial Omega.7 connection; opened=" << openedCount << ", deviceCount=" << deviceCount
            << ", availableCount=" << availableCount << ", " << warnings.str();
    lastError_ = message.str();
  }
  return true;
#else
  // 非 Windows 或未开启 vendor SDK 编译开关时，不会触碰真实硬件。
  lastError_ = "APPSTATION_ENABLE_VENDOR_SDKS is OFF; Omega.7 real calls disabled";
  return false;
#endif
}

bool Omega7Driver::ensureReady() {
  int leftOpenId = 0;
  int rightOpenId = 1;
  bool swapHands = false;
  {
    std::scoped_lock lock(mutex_);
    if (initialized_) {
      return true;
    }
    leftOpenId = leftOpenId_;
    rightOpenId = rightOpenId_;
    swapHands = swapHands_;
  }
  return initialize(leftOpenId, rightOpenId, swapHands);
}

bool Omega7Driver::ok() const {
  std::scoped_lock lock(mutex_);
  return initialized_;
}

std::string Omega7Driver::lastError() const {
  std::scoped_lock lock(mutex_);
  return lastError_;
}

std::array<Omega7State, 2> Omega7Driver::readState() {
  std::scoped_lock lock(mutex_);
  const auto readTimestampMs = unixTimeMs();
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  // 逐台读取已打开设备的实时状态。这里不重新打开设备，只复用 initialize
  // 保存下来的 deviceId，因此 readState 应该是轻量的周期性轮询。
  for (std::size_t index = 0; index < state_.size(); ++index) {
    auto& item = state_[index];
    if (!item.connected || item.deviceId < 0) {
      continue;
    }
    double x = 0, y = 0, z = 0, roll = 0, pitch = 0, yaw = 0;
    // pose 前三维是位置，后三维是姿态角，姿态单位由 SDK 函数名确定为 degree。
    if (dhdGetPositionAndOrientationDeg(&x, &y, &z, &roll, &pitch, &yaw, static_cast<char>(item.deviceId)) >= 0) {
      item.pose = {x, y, z, roll, pitch, yaw};
      item.lastReadOk = true;
      item.lastReadError.clear();
    } else {
      item.lastReadOk = false;
      item.lastReadError = sdkError();
    }
    // 当前约定：按钮 0 是 clutch，按钮 1 是 gripper。后端 teleop mapper
    // 会根据 clutch 配置决定是否允许把主手位姿变化映射到从臂动作。
    if (dhdGetButton) {
      item.clutchPressed = dhdGetButton(0, static_cast<char>(item.deviceId)) > 0;
      item.gripperPressed = dhdGetButton(1, static_cast<char>(item.deviceId)) > 0;
    }
    // gripperGap 是 Omega.7 主手自身夹持开口；下游可用它映射从端夹爪命令。
    double selectedGapMm = 0.0;
    bool haveGap = false;
    if (dhdGetGripperGap) {
      // 首选 SDK 直接给出的开口距离，单位米。
      double gapM = 0.0;
      if (dhdGetGripperGap(&gapM, static_cast<char>(item.deviceId)) >= 0 && std::isfinite(gapM)) {
        const double directGapMm = (std::max)(0.0, gapM * 1000.0);
        if (directGapMm > kOmega7GripperGapMinReliableMm) {
          selectedGapMm = directGapMm;
          haveGap = true;
        }
      }
    }
    if (!haveGap && dhdGetGripperEncoder && dhdGripperEncoderToGap) {
      // 直接开口不可用时，用编码器读数经 SDK 换算成开口距离。
      int encoder = 0;
      double encoderGapM = 0.0;
      if (dhdGetGripperEncoder(&encoder, static_cast<char>(item.deviceId)) >= 0
          && dhdGripperEncoderToGap(encoder, &encoderGapM, static_cast<char>(item.deviceId)) >= 0
          && std::isfinite(encoderGapM)) {
        const double encoderGapMm = (std::max)(0.0, encoderGapM * 1000.0);
        if (encoderGapMm > kOmega7GripperGapMinReliableMm) {
          selectedGapMm = encoderGapMm;
          haveGap = true;
        }
      }
    }
    if (!haveGap && dhdGetGripperAngleDeg) {
      // 最后才用夹爪角度线性估算开口，精度较低但可作为 teleop 兜底输入。
      double angleDeg = 0.0;
      if (dhdGetGripperAngleDeg(&angleDeg, static_cast<char>(item.deviceId)) >= 0 && std::isfinite(angleDeg)) {
        selectedGapMm = std::clamp(std::abs(angleDeg) / kOmega7GripperOpenDeg, 0.0, 1.0)
            * kOmega7GripperOpenMm;
        haveGap = true;
      }
    }
    item.gripperGap = selectedGapMm / 1000.0;
    item.gripperGapAvailable = haveGap;
    if (forceOutputEnabled_[index]) {
      writeZeroForceUnlocked(item);
    }
  }
#endif
  // 目前没有接入 SDK 校准状态查询，保持 false，避免前端误以为已经完成校准。
  for (auto& item : state_) {
    item.calibrated = false;
    item.readTimestampMs = readTimestampMs;
  }
  return state_;
}

void Omega7Driver::setGravityCompensation(bool leftEnabled, bool rightEnabled, double leftScale, double rightScale) {
  std::scoped_lock lock(mutex_);
  gravityScale_[0] = normalizeGravityScale(leftScale, gravityScale_[0]);
  gravityScale_[1] = normalizeGravityScale(rightScale, gravityScale_[1]);
  // 两侧独立控制，允许只给已连接或需要的主手开启力输出。
  applyForceOutputUnlocked(0, leftEnabled);
  applyForceOutputUnlocked(0, leftEnabled);
  applyForceOutputUnlocked(1, rightEnabled);
}

std::array<bool, 2> Omega7Driver::forceOutputEnabled() const {
  std::scoped_lock lock(mutex_);
  return forceOutputEnabled_;
}

void Omega7Driver::zeroForceFeedback(int openId) {
  std::scoped_lock lock(mutex_);
  // openId < 0 表示对所有已连接设备写零力；否则只处理指定启动编号的设备。
  for (const auto& item : state_) {
    if (item.connected && item.deviceId >= 0 && (openId < 0 || item.openId == openId)) {
      writeZeroForceUnlocked(item);
    }
  }
}

void Omega7Driver::applyForceOutputUnlocked(std::size_t index, bool enabled) {
  if (index >= state_.size()) {
    return;
  }
  forceOutputEnabled_[index] = enabled;
  const auto& item = state_[index];
  if (!item.connected || item.deviceId < 0) {
    return;
  }
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  const auto deviceId = static_cast<char>(item.deviceId);
  if (dhdEnableExpertMode) {
    // Force Dimension SDK 的力输出通常要求先进入 expert mode；失败不阻断后续尝试。
    (void)dhdEnableExpertMode();
  }
  if (dhdSetStandardGravity) {
    (void)dhdSetStandardGravity(kStandardGravity * gravityScale_[index], deviceId);
  }
  if (dhdSetGravityCompensation) {
    (void)dhdSetGravityCompensation(enabled ? 1 : 0, deviceId);
  }
  if (dhdEnableForce) {
    const int ret = dhdEnableForce(enabled ? 1 : 0, deviceId);
    if (ret < 0) {
      lastError_ = std::string("dhdEnableForce failed: ") + sdkError();
    }
  }
  if (enabled) {
    writeZeroForceUnlocked(item);
  }
#else
  (void)enabled;
#endif
}

int Omega7Driver::writeZeroForceUnlocked(const Omega7State& item) {
#if defined(_WIN32) && defined(APPSTATION_ENABLE_VENDOR_SDKS)
  if (!dhdSetForceAndTorqueAndGripperForce || !item.connected || item.deviceId < 0) {
    return -1;
  }
  // 即使启用重力补偿，也周期性写 0 力/0 力矩，避免残留主动力反馈。
  return dhdSetForceAndTorqueAndGripperForce(
      0.0,
      0.0,
      0.0,
      0.0,
      0.0,
      0.0,
      0.0,
      static_cast<char>(item.deviceId));
#else
  (void)item;
  return -1;
#endif
}

}  // namespace appstation::hal
