#include "JodellGripperDriver.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <sstream>
#include <thread>
#include <vector>

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

#ifdef _WIN32
std::string sanitizeProtocolField(std::string value) {
  for (char& ch : value) {
    if (ch == '\t' || ch == '\r' || ch == '\n') {
      ch = ' ';
    }
  }
  return value;
}

std::vector<std::string> splitTabs(const std::string& value) {
  std::vector<std::string> parts;
  size_t start = 0;
  while (start <= value.size()) {
    const auto next = value.find('\t', start);
    if (next == std::string::npos) {
      parts.push_back(value.substr(start));
      break;
    }
    parts.push_back(value.substr(start, next - start));
    start = next + 1;
  }
  return parts;
}

std::string quoteWindowsArg(const std::string& value) {
  std::string out = "\"";
  for (char ch : value) {
    if (ch == '\\' || ch == '"') {
      out.push_back('\\');
    }
    out.push_back(ch);
  }
  out.push_back('"');
  return out;
}

std::string defaultWorkerExePath() {
  const char* workerOverride = std::getenv("APPSTATION_JODELL_WORKER_EXE");
  if (workerOverride != nullptr && workerOverride[0] != '\0') {
    return workerOverride;
  }
  std::array<char, MAX_PATH> path{};
  const DWORD length = GetModuleFileNameA(nullptr, path.data(), static_cast<DWORD>(path.size()));
  if (length == 0 || length >= path.size()) {
    return "JodellGripperWorker.exe";
  }
  std::string value(path.data(), length);
  const auto slash = value.find_last_of("\\/");
  if (slash == std::string::npos) {
    return "JodellGripperWorker.exe";
  }
  return value.substr(0, slash + 1) + "JodellGripperWorker.exe";
}

bool writeAll(HANDLE handle, const std::string& value) {
  size_t offset = 0;
  while (offset < value.size()) {
    DWORD written = 0;
    const DWORD chunk = static_cast<DWORD>(std::min<size_t>(value.size() - offset, 4096));
    if (!WriteFile(handle, value.data() + offset, chunk, &written, nullptr) || written == 0) {
      return false;
    }
    offset += written;
  }
  return true;
}

bool readLineWithTimeout(HANDLE handle, double timeoutMs, std::string* line) {
  line->clear();
  const auto deadline = std::chrono::steady_clock::now()
      + std::chrono::milliseconds(static_cast<int>(std::max(1.0, timeoutMs)));
  while (std::chrono::steady_clock::now() < deadline) {
    DWORD available = 0;
    if (!PeekNamedPipe(handle, nullptr, 0, nullptr, &available, nullptr)) {
      return false;
    }
    if (available == 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
      continue;
    }
    char ch = '\0';
    DWORD read = 0;
    if (!ReadFile(handle, &ch, 1, &read, nullptr) || read == 0) {
      return false;
    }
    if (ch == '\n') {
      return true;
    }
    if (ch != '\r') {
      line->push_back(ch);
    }
  }
  return false;
}
#endif

}  // namespace

JodellGripperDriver::JodellGripperDriver() = default;

JodellGripperDriver::~JodellGripperDriver() {
#ifdef _WIN32
  std::scoped_lock lock(mutex_);
  closeProcessWorkersUnlocked();
  closeUnlocked();
#endif
}

void JodellGripperDriver::configure(const JodellGripperConfig& config) {
  std::scoped_lock lock(mutex_);
#ifdef _WIN32
  closeProcessWorkersUnlocked();
  closeUnlocked();
#endif
  config_ = config;
}

bool JodellGripperDriver::commandTarget(
    Side side,
    double targetMm,
    int speed,
    int torque,
    std::string* message,
    bool readPosition) {
  std::scoped_lock lock(mutex_);
  if (!config_.enabled) {
    if (message) {
      *message = "native gripper teleop disabled";
    }
    return false;
  }

  const int index = sideIndex(side);
  const int port = portNumber(config_.ports[index]);
  const int slave = config_.slaveIds[index];
  const double stroke = std::max(0.001, config_.strokeMm);
  const double bounded = std::clamp(targetMm, 0.0, stroke);
  const int safeSpeed = std::clamp(speed, 1, 255);
  const int safeTorque = std::clamp(torque, 1, 255);
  if (config_.processWorkersEnabled) {
#ifdef _WIN32
    if (!ensureProcessWorkerUnlocked(index, message)) {
      return false;
    }
    std::ostringstream command;
    command << "COMMAND\t" << bounded << "\t" << safeSpeed << "\t" << safeTorque;
    std::string workerMessage;
    const bool ok = commandProcessWorkerUnlocked(index, command.str(), nullptr, &workerMessage);
    targetMm_[index] = bounded;
    if (!ok) {
      lastError_ = workerMessage;
    }
    if (message) {
      *message = workerMessage;
    }
    return ok;
#else
    (void)port;
    (void)slave;
    if (message) {
      *message = "Jodell isolated worker unavailable outside Windows";
    }
    return false;
#endif
  }
  std::string loadMessage;
  if (!ensureLoadedUnlocked(&loadMessage)) {
    lastError_ = loadMessage;
    if (message) {
      *message = loadMessage;
    }
    return false;
  }
  if (!ensurePortOpenUnlocked(index, port, message)) {
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
  const int raw = static_cast<int>(std::lround((stroke - bounded) / stroke * 255.0));
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
  if (readPosition) {
    const int currentRaw = getClawCurrentLocation_(slave);
    if (currentRaw >= 0) {
      positionMm_[index] = stroke * (1.0 - std::clamp(currentRaw, 0, 255) / 255.0);
      out << ", current=" << currentRaw << ", positionMm=" << positionMm_[index];
    }
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

bool JodellGripperDriver::readPositionMm(Side side, std::string* message) {
  std::scoped_lock lock(mutex_);
  if (!config_.enabled) {
    if (message) {
      *message = "native gripper teleop disabled";
    }
    return false;
  }

  const int index = sideIndex(side);
  const int port = portNumber(config_.ports[index]);
  const int slave = config_.slaveIds[index];
  if (config_.processWorkersEnabled) {
#ifdef _WIN32
    if (!ensureProcessWorkerUnlocked(index, message)) {
      return false;
    }
    double positionMm = -1.0;
    std::string workerMessage;
    const bool ok = commandProcessWorkerUnlocked(index, "READ", &positionMm, &workerMessage);
    if (!ok) {
      lastError_ = workerMessage;
      if (message) {
        *message = workerMessage;
      }
      return false;
    }
    positionMm_[index] = positionMm;
    if (message) {
      *message = workerMessage;
    }
    return true;
#else
    (void)port;
    (void)slave;
    if (message) {
      *message = "Jodell isolated worker unavailable outside Windows";
    }
    return false;
#endif
  }
  std::string loadMessage;
  if (!ensureLoadedUnlocked(&loadMessage)) {
    lastError_ = loadMessage;
    if (message) {
      *message = loadMessage;
    }
    return false;
  }
  if (!ensurePortOpenUnlocked(index, port, message)) {
    return false;
  }

#ifdef _WIN32
  const int currentRaw = getClawCurrentLocation_(slave);
  if (currentRaw < 0) {
    std::ostringstream out;
    out << "getClawCurrentLocation failed " << portLabel(port) << ", slave=" << slave << ", ret=" << currentRaw;
    lastError_ = out.str();
    if (message) {
      *message = lastError_;
    }
    return false;
  }
  const double stroke = std::max(0.001, config_.strokeMm);
  positionMm_[index] = stroke * (1.0 - std::clamp(currentRaw, 0, 255) / 255.0);
  if (message) {
    std::ostringstream out;
    out << "position " << portLabel(port) << ", slave=" << slave
        << ", current=" << currentRaw << ", positionMm=" << positionMm_[index];
    *message = out.str();
  }
  return true;
#else
  if (message) {
    *message = "Jodell gripper position unavailable outside Windows";
  }
  return false;
#endif
}

std::array<double, 2> JodellGripperDriver::targetMm() const {
  std::scoped_lock lock(mutex_);
  return targetMm_;
}

std::array<double, 2> JodellGripperDriver::positionMm() const {
  std::scoped_lock lock(mutex_);
  return positionMm_;
}

std::array<double, 2> JodellGripperDriver::positionMmSnapshot(std::array<double, 2> fallback) const {
  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) {
    return fallback;
  }
  return positionMm_;
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

bool JodellGripperDriver::ensurePortOpenUnlocked(int index, int port, std::string* message) {
#ifdef _WIN32
  if (!serialOperation_) {
    if (message) {
      *message = "serialOperation is not loaded";
    }
    return false;
  }
  if (index < 0 || index >= static_cast<int>(activePorts_.size())) {
    if (message) {
      *message = "invalid native gripper side index";
    }
    return false;
  }
  bool closedOtherPort = false;
  for (int& activePort : activePorts_) {
    if (activePort > 0 && activePort != port) {
      (void)serialOperation_(activePort, config_.baudrate, 0);
      activePort = -1;
      closedOtherPort = true;
    }
  }
  if (closedOtherPort) {
    std::this_thread::sleep_for(kPortSwitchSettleMs);
  }
  if (activePorts_[index] == port) {
    return true;
  }
  int ret = -999;
  for (int attempt = 0; attempt < 5; ++attempt) {
    ret = serialOperation_(port, config_.baudrate, 1);
    if (ret == 0 || ret == 1) {
      activePorts_[index] = port;
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
  (void)index;
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
bool JodellGripperDriver::ensureProcessWorkerUnlocked(int index, std::string* message) {
  auto& worker = workerProcesses_[index];
  if (worker.process != nullptr) {
    DWORD exitCode = 0;
    if (GetExitCodeProcess(static_cast<HANDLE>(worker.process), &exitCode) && exitCode == STILL_ACTIVE) {
      return true;
    }
    closeProcessWorkersUnlocked();
  }

  SECURITY_ATTRIBUTES security{};
  security.nLength = sizeof(SECURITY_ATTRIBUTES);
  security.bInheritHandle = TRUE;

  HANDLE childStdInRead = nullptr;
  HANDLE parentStdInWrite = nullptr;
  HANDLE parentStdOutRead = nullptr;
  HANDLE childStdOutWrite = nullptr;
  if (!CreatePipe(&childStdInRead, &parentStdInWrite, &security, 0)
      || !CreatePipe(&parentStdOutRead, &childStdOutWrite, &security, 0)) {
    if (message) {
      *message = "failed to create Jodell worker pipes";
    }
    return false;
  }
  SetHandleInformation(parentStdInWrite, HANDLE_FLAG_INHERIT, 0);
  SetHandleInformation(parentStdOutRead, HANDLE_FLAG_INHERIT, 0);

  const std::string side = index == 0 ? "left" : "right";
  const std::string workerExe = config_.workerExePath.empty() ? defaultWorkerExePath() : config_.workerExePath;
  std::ostringstream command;
  command << quoteWindowsArg(workerExe)
      << " --side " << side
      << " --port " << quoteWindowsArg(config_.ports[index])
      << " --slave " << config_.slaveIds[index]
      << " --baudrate " << config_.baudrate
      << " --stroke-mm " << config_.strokeMm
      << " --dll " << quoteWindowsArg(config_.dllPath);
  std::string commandLine = command.str();

  STARTUPINFOA startup{};
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESTDHANDLES;
  startup.hStdInput = childStdInRead;
  startup.hStdOutput = childStdOutWrite;
  startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
  PROCESS_INFORMATION processInfo{};
  const BOOL created = CreateProcessA(
      nullptr,
      commandLine.data(),
      nullptr,
      nullptr,
      TRUE,
      CREATE_NO_WINDOW,
      nullptr,
      nullptr,
      &startup,
      &processInfo);
  CloseHandle(childStdInRead);
  CloseHandle(childStdOutWrite);
  if (!created) {
    CloseHandle(parentStdInWrite);
    CloseHandle(parentStdOutRead);
    if (message) {
      std::ostringstream out;
      out << "failed to start JodellGripperWorker.exe error=" << GetLastError();
      *message = out.str();
    }
    return false;
  }
  CloseHandle(processInfo.hThread);
  worker.process = processInfo.hProcess;
  worker.stdinWrite = parentStdInWrite;
  worker.stdoutRead = parentStdOutRead;
  return true;
}

bool JodellGripperDriver::commandProcessWorkerUnlocked(
    int index,
    const std::string& command,
    double* positionMm,
    std::string* message) {
  auto& worker = workerProcesses_[index];
  if (worker.process == nullptr || worker.stdinWrite == nullptr || worker.stdoutRead == nullptr) {
    if (message) {
      *message = "Jodell worker is not running";
    }
    return false;
  }
  if (!writeAll(static_cast<HANDLE>(worker.stdinWrite), command + "\n")) {
    if (message) {
      *message = "failed to write Jodell worker command";
    }
    return false;
  }
  std::string line;
  if (!readLineWithTimeout(static_cast<HANDLE>(worker.stdoutRead), config_.workerCommandTimeoutMs, &line)) {
    if (message) {
      *message = "Jodell worker response timeout";
    }
    return false;
  }
  const auto parts = splitTabs(line);
  if (parts.size() < 3) {
    if (message) {
      *message = "invalid Jodell worker response: " + sanitizeProtocolField(line);
    }
    return false;
  }
  if (positionMm) {
    char* end = nullptr;
    const double value = std::strtod(parts[1].c_str(), &end);
    *positionMm = end == parts[1].c_str() ? -1.0 : value;
  }
  if (message) {
    *message = parts[2];
  }
  return parts[0] == "OK";
}

void JodellGripperDriver::closeProcessWorkersUnlocked() {
  for (auto& worker : workerProcesses_) {
    if (worker.stdinWrite) {
      (void)writeAll(static_cast<HANDLE>(worker.stdinWrite), "EXIT\n");
    }
    if (worker.process) {
      const DWORD waitResult = WaitForSingleObject(static_cast<HANDLE>(worker.process), 500);
      if (waitResult == WAIT_TIMEOUT) {
        TerminateProcess(static_cast<HANDLE>(worker.process), 1);
        WaitForSingleObject(static_cast<HANDLE>(worker.process), 500);
      }
    }
    if (worker.stdinWrite) {
      CloseHandle(static_cast<HANDLE>(worker.stdinWrite));
    }
    if (worker.stdoutRead) {
      CloseHandle(static_cast<HANDLE>(worker.stdoutRead));
    }
    if (worker.process) {
      CloseHandle(static_cast<HANDLE>(worker.process));
    }
    worker = ProcessWorkerHandle{};
  }
}

void JodellGripperDriver::closeUnlocked() {
  if (serialOperation_) {
    for (size_t i = 0; i < activePorts_.size(); ++i) {
      const int port = activePorts_[i];
      if (port <= 0) {
        continue;
      }
      bool alreadyClosed = false;
      for (size_t j = 0; j < i; ++j) {
        alreadyClosed = alreadyClosed || activePorts_[j] == port;
      }
      if (!alreadyClosed) {
        (void)serialOperation_(port, config_.baudrate, 0);
      }
    }
  }
  activePorts_ = {{-1, -1}};
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
