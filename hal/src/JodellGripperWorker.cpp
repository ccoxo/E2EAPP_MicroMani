#include "JodellGripperDriver.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

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

std::string sanitizeField(std::string value) {
  for (char& ch : value) {
    if (ch == '\t' || ch == '\r' || ch == '\n') {
      ch = ' ';
    }
  }
  return value;
}

std::string argValue(int argc, char** argv, const std::string& key, const std::string& fallback = "") {
  for (int i = 1; i + 1 < argc; ++i) {
    if (std::string(argv[i]) == key) {
      return argv[i + 1];
    }
  }
  return fallback;
}

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

void writeResponse(bool ok, double positionMm, const std::string& message) {
  std::cout << (ok ? "OK" : "ERR")
      << '\t' << positionMm
      << '\t' << sanitizeField(message)
      << '\n';
  std::cout.flush();
}

}  // namespace

int main(int argc, char** argv) {
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
