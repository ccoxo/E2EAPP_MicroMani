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
