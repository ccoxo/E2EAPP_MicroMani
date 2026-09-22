/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：解析 HKVL 字节帧与 CRC，维护帧同步和错误统计。
 * 先看：HkvlForceParser::feed → HkvlForceParser::reset → HkvlForceParser::stats。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "HkvlForceProtocol.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <sstream>
#include <stdexcept>

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

std::string channelBlocker(const char* phase, std::size_t channel,
    const char* metric, double value, double limit) {
  std::ostringstream out;
  out << phase << " " << kChannels[channel] << " " << metric << " " << value
      << " exceeds " << limit;
  return out.str();
}

}  // namespace

void HkvlSampleAccumulator::reset() {
  sampleCount_ = 0;
  mean_ = {};
  squaredDeviations_ = {};
  minimum_ = {};
  maximum_ = {};
}

void HkvlSampleAccumulator::add(const std::array<double, 6>& values) {
  for (const auto value : values) {
    if (!std::isfinite(value)) throw std::invalid_argument("HKVL sample must be finite");
  }
  if (sampleCount_ == 0) {
    minimum_ = values;
    maximum_ = values;
  }
  ++sampleCount_;
  for (std::size_t channel = 0; channel < values.size(); ++channel) {
    // Welford 累积避免较大静态偏置下平方差相减丢失微小振动。
    const double delta = values[channel] - mean_[channel];
    mean_[channel] += delta / static_cast<double>(sampleCount_);
    squaredDeviations_[channel] += delta * (values[channel] - mean_[channel]);
    minimum_[channel] = (std::min)(minimum_[channel], values[channel]);
    maximum_[channel] = (std::max)(maximum_[channel], values[channel]);
  }
}

int HkvlSampleAccumulator::sampleCount() const { return sampleCount_; }

HkvlSampleStatistics HkvlSampleAccumulator::statistics() const {
  HkvlSampleStatistics result;
  result.sampleCount = sampleCount_;
  if (sampleCount_ <= 0) return result;
  result.mean = mean_;
  for (std::size_t channel = 0; channel < mean_.size(); ++channel) {
    result.standardDeviation[channel] = std::sqrt((std::max)(
        0.0, squaredDeviations_[channel] / static_cast<double>(sampleCount_)));
    result.peakToPeak[channel] = maximum_[channel] - minimum_[channel];
  }
  return result;
}

std::string hkvlTareStabilityBlocker(const HkvlSampleStatistics& statistics) {
  if (statistics.sampleCount < kHkvlTareMinSamples) return "tare stability window requires at least 200 samples";
  for (std::size_t channel = 0; channel < statistics.mean.size(); ++channel) {
    if (!std::isfinite(statistics.mean[channel])
        || !std::isfinite(statistics.standardDeviation[channel])
        || !std::isfinite(statistics.peakToPeak[channel])) {
      return std::string("tare stability ") + kChannels[channel] + " statistics are not finite";
    }
    if (statistics.standardDeviation[channel] > kMaxTareStandardDeviation[channel]) {
      return channelBlocker("tare stability", channel, "standard deviation",
          statistics.standardDeviation[channel], kMaxTareStandardDeviation[channel]);
    }
    if (statistics.peakToPeak[channel] > kMaxTarePeakToPeak[channel]) {
      return channelBlocker("tare stability", channel, "peak-to-peak",
          statistics.peakToPeak[channel], kMaxTarePeakToPeak[channel]);
    }
  }
  return {};
}

std::string hkvlTareResidualBlocker(const HkvlSampleStatistics& statistics) {
  const auto blocker = hkvlTareStabilityBlocker(statistics);
  if (!blocker.empty()) return "post-" + blocker;
  for (std::size_t channel = 0; channel < statistics.mean.size(); ++channel) {
    if (std::abs(statistics.mean[channel]) > kMaxTareResidualMean[channel]) {
      return channelBlocker("post-tare residual", channel, "mean",
          std::abs(statistics.mean[channel]), kMaxTareResidualMean[channel]);
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

void HkvlForceParser::discardBufferedBytes() {
  buffer_.clear();
}

const HkvlForceParserStats& HkvlForceParser::stats() const {
  return stats_;
}

}  // namespace appstation::hal
