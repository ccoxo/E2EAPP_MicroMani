/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明HkvlForceProtocol 的接口与状态结构；解析 HKVL 字节帧与 CRC，维护帧同步和错误统计。
 * 先看：HkvlForceFrame → HkvlForceParserStats → HkvlForceParser。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace appstation::hal {

constexpr std::size_t kHkvlForceFrameSize = 28;
constexpr int kHkvlTareMinSamples = 200;
constexpr int kHkvlTareMaxSamples = 1000;
constexpr int kHkvlTareWindowTimeoutMs = 2000;
// 200 帧在 1 kHz 下约跨越 199 ms；保留调度裕量，但拒绝压缩回放窗口。
constexpr double kHkvlTareMinReceiveSpanMs = 100.0;
// 超过标称 50 ms 的整批积压不能作为本次自检的实时证据。
constexpr std::size_t kHkvlTareMaxBatchFrames = 50;

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
  std::array<double, 6> mean_{};
  std::array<double, 6> squaredDeviations_{};
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
  void discardBufferedBytes();
  void reset();
  const HkvlForceParserStats& stats() const;

 private:
  std::vector<std::uint8_t> buffer_;
  HkvlForceParserStats stats_{};
};

}  // namespace appstation::hal
