#include "HkvlForceProtocol.h"

#include <cmath>
#include <cstring>
#include <limits>
#include <sstream>

namespace appstation::hal {
namespace {

constexpr std::array<const char*, 6> kChannels{{"Fx", "Fy", "Fz", "Mx", "My", "Mz"}};
constexpr std::array<double, 6> kMaxTareStandardDeviation{{
    0.05, 0.05, 0.05, 0.0025, 0.0025, 0.0025,
}};
constexpr std::array<double, 6> kMaxTarePeakToPeak{{
    0.30, 0.30, 0.30, 0.015, 0.015, 0.015,
}};
constexpr std::array<double, 6> kMaxTareResidualMean{{
    0.10, 0.10, 0.10, 0.005, 0.005, 0.005,
}};

std::string channelBlocker(
    const char* phase,
    std::size_t channel,
    const char* metric,
    double value,
    double limit) {
  std::ostringstream out;
  out << phase << " " << kChannels[channel] << " " << metric << " " << value
      << " exceeds " << limit;
  return out.str();
}

}  // namespace

void HkvlSampleAccumulator::reset() {
  sampleCount_ = 0;
  sum_ = {};
  sumSquares_ = {};
  minimum_.fill((std::numeric_limits<double>::max)());
  maximum_.fill((std::numeric_limits<double>::lowest)());
}

void HkvlSampleAccumulator::add(const std::array<double, 6>& values) {
  if (sampleCount_ == 0) {
    minimum_ = values;
    maximum_ = values;
  }
  ++sampleCount_;
  for (std::size_t channel = 0; channel < values.size(); ++channel) {
    sum_[channel] += values[channel];
    sumSquares_[channel] += values[channel] * values[channel];
    minimum_[channel] = (std::min)(minimum_[channel], values[channel]);
    maximum_[channel] = (std::max)(maximum_[channel], values[channel]);
  }
}

int HkvlSampleAccumulator::sampleCount() const {
  return sampleCount_;
}

HkvlSampleStatistics HkvlSampleAccumulator::statistics() const {
  HkvlSampleStatistics result;
  result.sampleCount = sampleCount_;
  if (sampleCount_ <= 0) {
    return result;
  }
  const double count = static_cast<double>(sampleCount_);
  for (std::size_t channel = 0; channel < result.mean.size(); ++channel) {
    result.mean[channel] = sum_[channel] / count;
    const double variance = (std::max)(
        0.0,
        sumSquares_[channel] / count - result.mean[channel] * result.mean[channel]);
    result.standardDeviation[channel] = std::sqrt(variance);
    result.peakToPeak[channel] = maximum_[channel] - minimum_[channel];
  }
  return result;
}

std::string hkvlTareStabilityBlocker(const HkvlSampleStatistics& statistics) {
  if (statistics.sampleCount <= 0) {
    return "tare stability window has no samples";
  }
  for (std::size_t channel = 0; channel < statistics.mean.size(); ++channel) {
    if (statistics.standardDeviation[channel] > kMaxTareStandardDeviation[channel]) {
      return channelBlocker(
          "tare stability",
          channel,
          "standard deviation",
          statistics.standardDeviation[channel],
          kMaxTareStandardDeviation[channel]);
    }
    if (statistics.peakToPeak[channel] > kMaxTarePeakToPeak[channel]) {
      return channelBlocker(
          "tare stability",
          channel,
          "peak-to-peak",
          statistics.peakToPeak[channel],
          kMaxTarePeakToPeak[channel]);
    }
  }
  return {};
}

std::string hkvlTareResidualBlocker(const HkvlSampleStatistics& statistics) {
  const auto stabilityBlocker = hkvlTareStabilityBlocker(statistics);
  if (!stabilityBlocker.empty()) {
    return "post-" + stabilityBlocker;
  }
  for (std::size_t channel = 0; channel < statistics.mean.size(); ++channel) {
    if (std::abs(statistics.mean[channel]) > kMaxTareResidualMean[channel]) {
      return channelBlocker(
          "post-tare residual",
          channel,
          "mean",
          std::abs(statistics.mean[channel]),
          kMaxTareResidualMean[channel]);
    }
  }
  return {};
}

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
