#pragma once

#include <array>
#include <cstdint>
#include <mutex>
#include <string>

namespace appstation::hal {

struct Omega7State {
  bool connected{};
  bool calibrated{};
  int openId{};
  int deviceId{-1};
  std::string serial;
  std::string systemName;
  bool leftHanded{};
  bool handednessKnown{};
  std::array<double, 6> pose{};
  bool clutchPressed{};
  bool gripperPressed{};
  double gripperGap{};
  bool gripperGapAvailable{};
  bool lastReadOk{};
  std::int64_t readTimestampMs{};
  std::string lastReadError;
};

class Omega7Driver {
 public:
  bool initialize(int leftOpenId, int rightOpenId, bool swapHands);
  bool ok() const;
  std::string lastError() const;
  std::array<Omega7State, 2> readState();
  void setGravityCompensation(bool leftEnabled, bool rightEnabled);
  void zeroForceFeedback(int openId);

 private:
  mutable std::mutex mutex_;
  bool initialized_{false};
  std::string lastError_;
  std::array<Omega7State, 2> state_{};
};

}  // namespace appstation::hal
