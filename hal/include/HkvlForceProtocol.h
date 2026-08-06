#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
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
