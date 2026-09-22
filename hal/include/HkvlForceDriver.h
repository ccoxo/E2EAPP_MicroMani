/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明HkvlForceDriver 的接口与状态结构；管理双侧 HKVL 串口读取、协议解析、去皮、滤波与采样回调。
 * 先看：HkvlSerialConfig → HkvlDriverSample → HkvlSideSnapshot → HkvlDriverSnapshot。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

#include "HkvlForceProtocol.h"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include "WorkerExceptionBoundary.h"

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

struct HkvlTareSideResult {
  std::array<double, 6> bias{};
  HkvlSampleStatistics before{};
  HkvlSampleStatistics after{};
};

struct HkvlTareResult {
  std::array<HkvlTareSideResult, 2> sides{};
  std::int64_t completedAtUnixMs{0};
};

class HkvlForceDriver {
 public:
  using SampleCallback = std::function<void(const HkvlDriverSample&)>;
  using FailureCallback = std::function<void(int, const char*)>;
  // 注入源提供 raw 值，仍经过与串口相同的去皮、滤波和安全回调流程。
  using ReadLoop = std::function<void(int, const SampleCallback&, const std::atomic_bool&)>;
  // 字节注入与真实串口共用分帧和接收批次边界，用于离线验证缓存/分包。
  using BytesCallback = std::function<void(const std::uint8_t*, std::size_t)>;
  using ByteReadLoop = std::function<void(int, const BytesCallback&, const std::atomic_bool&)>;
  using TareProgressCallback = std::function<void(const std::string&, int)>;
  // 在安全锁下执行偏置提交；返回 false 时保持旧偏置并取消本次采集。
  using TareCommitCallback = std::function<bool(const std::function<void()>&)>;

  explicit HkvlForceDriver(ReadLoop readLoop = {}, WorkerLauncher launcher = {}, ByteReadLoop byteReadLoop = {});
  ~HkvlForceDriver();

  HkvlForceDriver(const HkvlForceDriver&) = delete;
  HkvlForceDriver& operator=(const HkvlForceDriver&) = delete;

  void start(const HkvlSerialConfig& config, SampleCallback callback, FailureCallback failure = {});
  void requestStop() noexcept;
  void stop();
  bool running() const;
  HkvlTareResult tare(
      int side,
      int sampleCount = kHkvlTareMinSamples,
      std::chrono::milliseconds timeout = std::chrono::milliseconds(kHkvlTareWindowTimeoutMs),
      TareCommitCallback commitIfAllowed = {},
      TareProgressCallback progress = {});
  HkvlDriverSnapshot snapshot(double nowMonotonicMs) const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
