#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace appstation::hal {

constexpr std::size_t kHkvlForceFrameSize = 28;

struct HkvlForceFrame {
  std::array<double, 6> values{};
};

struct HkvlForceParserStats {
  std::uint64_t validFrames{0};
  std::uint64_t crcErrors{0};
  std::uint64_t nonFiniteFrames{0};
  std::uint64_t resyncBytes{0};
};

struct HkvlSampleStatistics {
  int sampleCount{0};
  std::array<double, 6> mean{};
  std::array<double, 6> standardDeviation{};
  std::array<double, 6> peakToPeak{};
};

class HkvlSampleAccumulator {
 public:
  void reset();
  void add(const std::array<double, 6>& values);
  int sampleCount() const;
  HkvlSampleStatistics statistics() const;

 private:
  int sampleCount_{0};
  std::array<double, 6> sum_{};
  std::array<double, 6> sumSquares_{};
  std::array<double, 6> minimum_{};
  std::array<double, 6> maximum_{};
};

std::string hkvlTareStabilityBlocker(const HkvlSampleStatistics& statistics);
std::string hkvlTareResidualBlocker(const HkvlSampleStatistics& statistics);

std::uint16_t hkvlModbusCrc(const std::uint8_t* data, std::size_t size);

class HkvlForceParser {
 public:
  std::vector<HkvlForceFrame> feed(const std::uint8_t* data, std::size_t size);
  std::vector<HkvlForceFrame> feed(const std::vector<std::uint8_t>& data);
  void reset();
  const HkvlForceParserStats& stats() const;

 private:
  std::vector<std::uint8_t> buffer_;
  HkvlForceParserStats stats_{};
};

}  // namespace appstation::hal
