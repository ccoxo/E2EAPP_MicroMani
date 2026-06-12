#include "JodellGripperDriver.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

// worker 与父进程使用 tab 分隔的一行协议，因此这里提供最小拆分函数。
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

// 响应字段不能包含制表符或换行，否则父进程无法可靠解析。
std::string sanitizeField(std::string value) {
  for (char& ch : value) {
    if (ch == '\t' || ch == '\r' || ch == '\n') {
      ch = ' ';
    }
  }
  return value;
}

// worker 参数规模很小，使用直接扫描避免引入额外命令行解析依赖。
std::string argValue(int argc, char** argv, const std::string& key, const std::string& fallback = "") {
  for (int i = 1; i + 1 < argc; ++i) {
    if (std::string(argv[i]) == key) {
      return argv[i + 1];
    }
  }
  return fallback;
}

// 解析失败时使用 fallback，让父进程仍能用默认串口参数启动 worker。
int intArg(int argc, char** argv, const std::string& key, int fallback) {
  const auto value = argValue(argc, argv, key);
  if (value.empty()) {
    return fallback;
  }
  char* end = nullptr;
  const long parsed = std::strtol(value.c_str(), &end, 10);
  return end == value.c_str() ? fallback : static_cast<int>(parsed);
}

double doubleArg(int argc, char** argv, const std::string& key, double fallback) {
  const auto value = argValue(argc, argv, key);
  if (value.empty()) {
    return fallback;
  }
  char* end = nullptr;
  const double parsed = std::strtod(value.c_str(), &end);
  return end == value.c_str() ? fallback : parsed;
}

// 所有响应固定为：状态、位置、消息。父进程逐行读取并按 tab 切分。
void writeResponse(bool ok, double positionMm, const std::string& message) {
  std::cout << (ok ? "OK" : "ERR")
      << '\t' << positionMm
      << '\t' << sanitizeField(message)
      << '\n';
  std::cout.flush();
}

}  // namespace

int main(int argc, char** argv) {
  // 每个 worker 只服务单侧夹爪，避免同一进程内频繁切换串口。
  const std::string sideArg = argValue(argc, argv, "--side", "left");
  const auto side = sideArg == "right" ? appstation::hal::Side::Right : appstation::hal::Side::Left;
  const int sideIndex = side == appstation::hal::Side::Left ? 0 : 1;

  appstation::hal::JodellGripperConfig config;
  config.processWorkersEnabled = false;
  config.ports[sideIndex] = argValue(argc, argv, "--port", sideIndex == 0 ? "COM8" : "COM9");
  config.slaveIds[sideIndex] = intArg(argc, argv, "--slave", sideIndex == 0 ? 10 : 9);
  config.baudrate = intArg(argc, argv, "--baudrate", 115200);
  config.strokeMm = doubleArg(argc, argv, "--stroke-mm", 26.0);
  config.dllPath = argValue(argc, argv, "--dll", config.dllPath);

  appstation::hal::JodellGripperDriver driver;
  driver.configure(config);

  // 父进程通过 stdin 发送 READ、COMMAND、EXIT；worker 保持常驻以复用 DLL/串口状态。
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line == "EXIT") {
      break;
    }
    if (line == "READ") {
      std::string message;
      const bool ok = driver.readPositionMm(side, &message);
      const auto positions = driver.positionMm();
      writeResponse(ok, positions[sideIndex], message);
      continue;
    }
    const auto parts = splitTabs(line);
    if (parts.size() == 4 && parts[0] == "COMMAND") {
      // COMMAND 参数已经由父进程生成，这里只做范围夹紧，避免异常输入传给 vendor DLL。
      const double targetMm = std::strtod(parts[1].c_str(), nullptr);
      const int speed = std::clamp(static_cast<int>(std::strtol(parts[2].c_str(), nullptr, 10)), 1, 255);
      const int torque = std::clamp(static_cast<int>(std::strtol(parts[3].c_str(), nullptr, 10)), 1, 255);
      std::string message;
      const bool ok = driver.commandTarget(side, targetMm, speed, torque, &message, false);
      const auto positions = driver.positionMm();
      writeResponse(ok, positions[sideIndex], message);
      continue;
    }
    writeResponse(false, -1.0, "unsupported command");
  }
  return 0;
}
