#include "JodellGripperDriver.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <sstream>
#include <thread>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace appstation::hal {

namespace {

constexpr const char* kDefaultLeftPort = "COM8";
constexpr const char* kDefaultRightPort = "COM9";
constexpr auto kPortSwitchSettleMs = std::chrono::milliseconds(50);

template <typename Fn>
Fn loadSymbol(void* module, const char* plain, const char* decorated) {
#ifdef _WIN32
  auto* handle = static_cast<HMODULE>(module);
  auto* proc = GetProcAddress(handle, plain);
  if (!proc && decorated) {
    proc = GetProcAddress(handle, decorated);
  }
  return reinterpret_cast<Fn>(proc);
#else
  (void)module;
  (void)plain;
  (void)decorated;
  return nullptr;
#endif
}

std::string portLabel(int port) {
  std::ostringstream out;
  out << "COM" << port;
  return out.str();
}

}  // namespace

JodellGripperDriver::JodellGripperDriver() = default;

JodellGripperDriver::~JodellGripperDriver() {
#ifdef _WIN32
  std::scoped_lock lock(mutex_);
  closeUnlocked();
#endif
}

void JodellGripperDriver::configure(const JodellGripperConfig& config) {
  std::scoped_lock lock(mutex_);
  config_ = config;
}

bool JodellGripperDriver::commandTarget(
    Side side,
    double targetMm,
    int speed,
    int torque,
    std::string* message) {
  std::scoped_lock lock(mutex_);
  if (!config_.enabled) {
    if (message) {
      *message = "native gripper teleop disabled";
    }
    return false;
  }
  std::string loadMessage;
  if (!ensureLoadedUnlocked(&loadMessage)) {
    lastError_ = loadMessage;
    if (message) {
      *message = loadMessage;
    }
    return false;
  }

  const int index = sideIndex(side);
  const int port = portNumber(config_.ports[index]);
  const int slave = config_.slaveIds[index];
  if (!selectPortUnlocked(port, message)) {
    return false;
  }

#ifdef _WIN32
  const int retEnable = clawEnable_(slave, 1);
  if (retEnable != 0 && retEnable != 1) {
    std::ostringstream out;
    out << "clawEnable failed " << portLabel(port) << ", slave=" << slave << ", ret=" << retEnable;
    lastError_ = out.str();
    if (message) {
      *message = lastError_;
    }
    return false;
  }
  const double stroke = std::max(0.001, config_.strokeMm);
  const double bounded = std::clamp(targetMm, 0.0, stroke);
  const int raw = static_cast<int>(std::lround((stroke - bounded) / stroke * 255.0));
  const int safeSpeed = std::clamp(speed, 1, 255);
  const int safeTorque = std::clamp(torque, 1, 255);
  const int retRun = runWithParam_(slave, raw, safeSpeed, safeTorque);
  targetMm_[index] = bounded;
  std::ostringstream out;
  out << "runWithParam " << portLabel(port) << ", slave=" << slave << ", pos=" << raw
      << ", speed=" << safeSpeed << ", torque=" << safeTorque << ", ret=" << retRun;
  if (retRun != 0 && retRun != 1) {
    lastError_ = out.str();
    if (message) {
      *message = lastError_;
    }
    return false;
  }
  if (message) {
    *message = out.str();
  }
  return true;
#else
  targetMm_[index] = std::clamp(targetMm, 0.0, std::max(0.001, config_.strokeMm));
  if (message) {
    *message = "APPSTATION_ENABLE_VENDOR_SDKS is OFF; Jodell gripper command skipped";
  }
  return false;
#endif
}

std::array<double, 2> JodellGripperDriver::targetMm() const {
  std::scoped_lock lock(mutex_);
  return targetMm_;
}

std::string JodellGripperDriver::lastError() const {
  std::scoped_lock lock(mutex_);
  return lastError_;
}

bool JodellGripperDriver::ensureLoadedUnlocked(std::string* message) {
#ifdef _WIN32
  if (module_) {
    return true;
  }
  HMODULE module = nullptr;
  if (!config_.dllPath.empty()) {
    module = LoadLibraryA(config_.dllPath.c_str());
  }
  if (!module) {
    module = LoadLibraryA("jodellTool.dll");
  }
  if (!module) {
    if (message) {
      *message = "jodellTool.dll not found";
    }
    return false;
  }
  module_ = module;
  serialOperation_ = loadSymbol<decltype(serialOperation_)>(
      module_, "serialOperation", "?serialOperation@@YAHHH_N@Z");
  clawEnable_ = loadSymbol<decltype(clawEnable_)>(module_, "clawEnable", "?clawEnable@@YAHH_N@Z");
  runWithParam_ = loadSymbol<decltype(runWithParam_)>(module_, "runWithParam", "?runWithParam@@YAHHHHH@Z");
  getClawCurrentLocation_ = loadSymbol<decltype(getClawCurrentLocation_)>(
      module_, "getClawCurrentLocation", "?getClawCurrentLocation@@YAHH@Z");
  if (!serialOperation_ || !clawEnable_ || !runWithParam_ || !getClawCurrentLocation_) {
    closeUnlocked();
    if (message) {
      *message = "required Jodell exports missing: serialOperation/clawEnable/runWithParam/getClawCurrentLocation";
    }
    return false;
  }
  return true;
#else
  if (message) {
    *message = "Jodell gripper driver is Windows-only";
  }
  return false;
#endif
}

bool JodellGripperDriver::selectPortUnlocked(int port, std::string* message) {
#ifdef _WIN32
  if (!serialOperation_) {
    if (message) {
      *message = "serialOperation is not loaded";
    }
    return false;
  }
  if (activePort_ == port) {
    return true;
  }
  if (activePort_ > 0) {
    (void)serialOperation_(activePort_, config_.baudrate, 0);
    activePort_ = -1;
    std::this_thread::sleep_for(kPortSwitchSettleMs);
  }
  int ret = -999;
  for (int attempt = 0; attempt < 5; ++attempt) {
    ret = serialOperation_(port, config_.baudrate, 1);
    if (ret == 0 || ret == 1) {
      activePort_ = port;
      return true;
    }
    (void)serialOperation_(port, config_.baudrate, 0);
    std::this_thread::sleep_for(kPortSwitchSettleMs);
  }
  if (ret != 0 && ret != 1) {
    std::ostringstream out;
    out << "serialOperation open failed " << portLabel(port) << ", ret=" << ret;
    lastError_ = out.str();
    if (message) {
      *message = lastError_;
    }
    return false;
  }
  return false;
#else
  (void)port;
  if (message) {
    *message = "serialOperation unavailable outside Windows";
  }
  return false;
#endif
}

int JodellGripperDriver::portNumber(const std::string& value) const {
  std::string digits;
  for (const char ch : value) {
    if (ch >= '0' && ch <= '9') {
      digits.push_back(ch);
    }
  }
  if (digits.empty()) {
    return 0;
  }
  return std::stoi(digits);
}

int JodellGripperDriver::sideIndex(Side side) const {
  return side == Side::Left ? 0 : 1;
}

#ifdef _WIN32
void JodellGripperDriver::closeUnlocked() {
  if (serialOperation_ && activePort_ > 0) {
    (void)serialOperation_(activePort_, config_.baudrate, 0);
  }
  activePort_ = -1;
  if (module_) {
    FreeLibrary(static_cast<HMODULE>(module_));
  }
  module_ = nullptr;
  serialOperation_ = nullptr;
  clawEnable_ = nullptr;
  runWithParam_ = nullptr;
  getClawCurrentLocation_ = nullptr;
}
#endif

}  // namespace appstation::hal
