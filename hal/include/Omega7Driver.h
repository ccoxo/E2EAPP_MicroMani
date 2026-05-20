#pragma once

#include <array>
#include <cstddef>
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
  bool ensureReady();
  bool ok() const;
  std::string lastError() const;
  std::array<Omega7State, 2> readState();
  std::array<bool, 2> forceOutputEnabled() const;
  void setGravityCompensation(bool leftEnabled, bool rightEnabled);
  void zeroForceFeedback(int openId);

 private:
  void applyForceOutputUnlocked(std::size_t index, bool enabled);
  int writeZeroForceUnlocked(const Omega7State& item);

  mutable std::mutex mutex_;
  bool initialized_{false};
  int leftOpenId_{0};
  int rightOpenId_{1};
  bool swapHands_{false};
  std::string lastError_;
  std::array<Omega7State, 2> state_{};
  std::array<bool, 2> forceOutputEnabled_{{true, true}};
};

}  // namespace appstation::hal
