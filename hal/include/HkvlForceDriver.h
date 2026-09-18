/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明HkvlForceDriver 的接口与状态结构；管理双侧 HKVL 串口读取、协议解析、去皮、滤波与采样回调。
 * 先看：HkvlSerialConfig → HkvlDriverSample → HkvlSideSnapshot → HkvlDriverSnapshot。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

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

class HkvlForceDriver {
 public:
  using SampleCallback = std::function<void(const HkvlDriverSample&)>;
  using FailureCallback = std::function<void(int, const char*)>;
  using ReadLoop = std::function<void(int, const SampleCallback&, const std::atomic_bool&)>;
  // 在安全锁下执行偏置提交；返回 false 时保持旧偏置并取消本次采集。
  using TareCommitCallback = std::function<bool(const std::function<void()>&)>;

  explicit HkvlForceDriver(ReadLoop readLoop = {}, WorkerLauncher launcher = {});
  ~HkvlForceDriver();

  HkvlForceDriver(const HkvlForceDriver&) = delete;
  HkvlForceDriver& operator=(const HkvlForceDriver&) = delete;

  void start(const HkvlSerialConfig& config, SampleCallback callback, FailureCallback failure = {});
  void requestStop() noexcept;
  void stop();
  bool running() const;
  void tare(
      int side,
      int sampleCount = 200,
      std::chrono::milliseconds timeout = std::chrono::milliseconds(2000),
      TareCommitCallback commitIfAllowed = {});
  HkvlDriverSnapshot snapshot(double nowMonotonicMs) const;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace appstation::hal
