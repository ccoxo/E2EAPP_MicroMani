#include "HkvlForceProtocol.h"

#include <cmath>
#include <cstring>

namespace appstation::hal {

std::uint16_t hkvlModbusCrc(const std::uint8_t* data, std::size_t size) {
  std::uint16_t crc = 0xFFFF;
  for (std::size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; ++bit) {
      crc = (crc & 1) != 0
          ? static_cast<std::uint16_t>((crc >> 1) ^ 0xA001)
          : static_cast<std::uint16_t>(crc >> 1);
    }
  }
  return crc;
}

std::vector<HkvlForceFrame> HkvlForceParser::feed(
    const std::uint8_t* data,
    std::size_t size) {
  buffer_.insert(buffer_.end(), data, data + size);
  std::vector<HkvlForceFrame> frames;

  while (buffer_.size() >= 2) {
    if (buffer_[0] != 0x53 || buffer_[1] != 0x54) {
      buffer_.erase(buffer_.begin());
      ++stats_.resyncBytes;
      continue;
    }
    if (buffer_.size() < kHkvlForceFrameSize) {
      break;
    }

    const auto computed = hkvlModbusCrc(buffer_.data(), 26);
    const auto received = static_cast<std::uint16_t>(
        buffer_[26] | (static_cast<std::uint16_t>(buffer_[27]) << 8));
    if (computed != received) {
      ++stats_.crcErrors;
      ++stats_.resyncBytes;
      buffer_.erase(buffer_.begin());
      continue;
    }

    HkvlForceFrame frame;
    bool finite = true;
    for (std::size_t axis = 0; axis < frame.values.size(); ++axis) {
      float value = 0.0F;
      std::memcpy(&value, buffer_.data() + 2 + axis * sizeof(float), sizeof(float));
      finite = finite && std::isfinite(value);
      frame.values[axis] = static_cast<double>(value);
    }
    buffer_.erase(buffer_.begin(), buffer_.begin() + kHkvlForceFrameSize);
    if (!finite) {
      ++stats_.nonFiniteFrames;
      continue;
    }
    ++stats_.validFrames;
    frames.push_back(frame);
  }
  return frames;
}

std::vector<HkvlForceFrame> HkvlForceParser::feed(
    const std::vector<std::uint8_t>& data) {
  return feed(data.data(), data.size());
}

void HkvlForceParser::reset() {
  buffer_.clear();
  stats_ = {};
}

const HkvlForceParserStats& HkvlForceParser::stats() const {
  return stats_;
}

}  // namespace appstation::hal
