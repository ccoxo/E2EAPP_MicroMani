#pragma once

#include <array>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

namespace appstation::hal {

struct HkvlSerialConfig {
  std::string protocol{"hkvl_active_v1"};
  std::string leftPort{"COM15"};
  std::string rightPort{"COM14"};
  int baudrate{1000000};
  int expectedSampleHz{1000};
  bool lowpassEnabled{true};
  double lowpassCutoffHz{10.0};
};

struct HkvlDriverSample {
  int side{0};
  std::array<double, 6> raw{};
  std::array<double, 6> tared{};
  std::array<double, 6> filtered{};
  double monotonicMs{0.0};
  std::int64_t unixMs{0};
};

struct HkvlSideSnapshot {
  std::string port;
  bool connected{false};
  bool hasSample{false};
  std::array<double, 6> raw{};
  std::array<double, 6> tared{};
  std::array<double, 6> filtered{};
  std::array<double, 6> tareBias{};
  double sampleAgeMs{0.0};
  double sampleHz{0.0};
  std::uint64_t validFrames{0};
  std::uint64_t crcErrors{0};
  std::uint64_t nonFiniteFrames{0};
  std::uint64_t resyncBytes{0};
  std::string error;
};

struct HkvlDriverSnapshot {
  std::array<HkvlSideSnapshot, 2> sides{};
};

class HkvlForceDriver {
 public:
  using SampleCallback = std::function<void(const HkvlDriverSample&)>;

  HkvlForceDriver();
  ~HkvlForceDriver();

  HkvlForceDriver(const HkvlForceDriver&) = delete;
  HkvlForceDriver& operator=(const HkvlForceDriver&) = delete;

  void start(const HkvlSerialConfig& config, SampleCallback callback);
  void stop();
  bool running() const;
  void tare(
      int side,
      int sampleCount = 200,
      std::chrono::milliseconds timeout = std::chrono::milliseconds(2000));
  HkvlDriverSnapshot snapshot(double nowMonotonicMs) const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
