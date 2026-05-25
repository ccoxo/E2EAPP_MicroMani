#pragma once

#include <array>
#include <mutex>
#include <string>

#include "HalTypes.h"

namespace appstation::hal {

struct JodellGripperConfig {
  bool enabled{true};
  std::array<std::string, 2> ports{"COM8", "COM9"};
  std::array<int, 2> slaveIds{10, 9};
  int baudrate{115200};
  double strokeMm{26.0};
  int speed{255};
  int torque{192};
  std::string dllPath{"F:/E2EAPP_MicroMani/backend/vendor/jodell/jodellTool.dll"};
};

class JodellGripperDriver {
 public:
  JodellGripperDriver();
  ~JodellGripperDriver();

  void configure(const JodellGripperConfig& config);
  bool commandTarget(
      Side side,
      double targetMm,
      int speed,
      int torque,
      std::string* message = nullptr,
      bool readPosition = true);
  bool readPositionMm(Side side, std::string* message = nullptr);
  std::array<double, 2> targetMm() const;
  std::array<double, 2> positionMm() const;
  std::array<double, 2> positionMmSnapshot(std::array<double, 2> fallback) const;
  std::string lastError() const;

 private:
  bool ensureLoadedUnlocked(std::string* message);
  bool selectPortUnlocked(int port, std::string* message);
  int portNumber(const std::string& value) const;
  int sideIndex(Side side) const;

  mutable std::mutex mutex_;
  JodellGripperConfig config_{};
  std::array<double, 2> targetMm_{{0.0, 0.0}};
  std::array<double, 2> positionMm_{{-1.0, -1.0}};
  std::string lastError_;

#ifdef _WIN32
  void closeUnlocked();
  void* module_{nullptr};
  int(__stdcall* serialOperation_)(int, int, int) = nullptr;
  int(__stdcall* clawEnable_)(int, int) = nullptr;
  int(__stdcall* runWithParam_)(int, int, int, int) = nullptr;
  int(__stdcall* getClawCurrentLocation_)(int) = nullptr;
  int activePort_{-1};
#endif
};

}  // namespace appstation::hal
