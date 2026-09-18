/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：管理双侧 HKVL 串口读取、协议解析、去皮、滤波与采样回调。
 * 先看：HkvlForceDriver → SideState → HkvlForceDriver::start → HkvlForceDriver::stop。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "HkvlForceDriver.h"

#include "HkvlForceProtocol.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace appstation::hal {
namespace {

double steadyMilliseconds() {
  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration<double, std::milli>(now).count();
}

std::int64_t unixMilliseconds() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

}  // namespace

struct HkvlForceDriver::Impl {
  enum class TarePhase { idle, stability, validation };

  struct SideState {
    mutable std::mutex mutex;
    std::condition_variable tareCondition;
    HkvlForceParser parser;
    std::string port;
    bool connected{false};
    bool hasSample{false};
    std::array<double, 6> raw{};
    std::array<double, 6> tared{};
    std::array<double, 6> filtered{};
    std::array<double, 6> tareBias{};
    double lastSampleMonotonicMs{0.0};
    double previousFilterMonotonicMs{0.0};
    bool filterInitialized{false};
    double sampleHz{0.0};
    double rateWindowStartedMs{0.0};
    std::uint64_t rateWindowFrames{0};
    HkvlForceParserStats parserStats{};
    std::string error;
    bool tarePending{false};
    bool tareCancelled{false};
    TarePhase tarePhase{TarePhase::idle};
    std::uint64_t tareGeneration{0};
    std::uint64_t tareWindowId{0};
    int tareRemaining{0};
    double tareWindowStartedMs{0.0};
    double tareFirstReceivedMs{0.0};
    double tareLastReceivedMs{0.0};
    std::string tareError;
    bool discardPartialBeforeNextBatch{false};
    HkvlSampleAccumulator tareWindow;
    std::array<double, 6> candidateBias{};
  };

  HkvlSerialConfig config;
  SampleCallback callback;
  FailureCallback failureCallback;
  ReadLoop readLoop;
  ByteReadLoop byteReadLoop;
  WorkerLauncher launcher;
  std::array<SideState, 2> sides;
  std::array<std::thread, 2> workers;
  std::mutex tareMutex;
  std::recursive_mutex lifecycleMutex;
  std::atomic<bool> running{false};
  std::atomic_bool failed{false};
  std::atomic<std::uint64_t> generation{0};

  void requestStop() noexcept {
    running.store(false, std::memory_order_release);
    generation.fetch_add(1, std::memory_order_acq_rel);
    for (auto& state : sides) state.tareCondition.notify_all();
  }

  void reportFailure(int side, const char* message) noexcept {
    failed.store(true);
    requestStop();
    // 不持有串口状态锁调用安全层，避免与快照/去皮的锁顺序相反。
    try { if (failureCallback) failureCallback(side, message); }
    catch (...) { std::fputs("HKVL failure callback threw an exception\n", stderr); }
    std::fprintf(stderr, "HKVL reader %d stopped: %s\n", side, message);
    for (int index = 0; index < 2; ++index) {
      try {
        auto& state = sides[index];
        std::scoped_lock lock(state.mutex);
        state.connected = false;
        state.tarePending = false;
        state.tareCancelled = true;
        state.error = message;
        state.tareCondition.notify_all();
      } catch (...) { std::fputs("HKVL worker diagnostic update failed\n", stderr); }
    }
  }

  void resetSide(int side, const std::string& port) {
    auto& state = sides[side];
    std::scoped_lock lock(state.mutex);
    state.parser.reset();
    state.port = port;
    state.connected = false;
    state.hasSample = false;
    state.raw = {};
    state.tared = {};
    state.filtered = {};
    state.tareBias = {};
    state.lastSampleMonotonicMs = 0.0;
    state.previousFilterMonotonicMs = 0.0;
    state.filterInitialized = false;
    state.sampleHz = 0.0;
    state.rateWindowStartedMs = steadyMilliseconds();
    state.rateWindowFrames = 0;
    state.parserStats = {};
    state.error.clear();
    state.tarePending = false;
    state.tareCancelled = false;
    state.tarePhase = TarePhase::idle;
    state.tareGeneration = 0;
    ++state.tareWindowId;
    state.tareRemaining = 0;
    state.tareError.clear();
    state.discardPartialBeforeNextBatch = false;
    state.tareWindow.reset();
    state.candidateBias = {};
  }

  void updateConnection(int side, bool connected, const std::string& error) {
    auto& state = sides[side];
    std::scoped_lock lock(state.mutex);
    state.connected = connected;
    state.error = error;
    if (!connected) {
      // 一次断连即使迅速恢复，也不能继续使用断连前的去皮窗口。
      if (state.tarePhase != TarePhase::idle) state.tareCancelled = true;
      state.tareCondition.notify_all();
    }
  }

  void processBytes(int side, const std::uint8_t* bytes, std::size_t size) {
    // 同一次读取的帧共享接收时刻，不能因逐帧回调阻塞而被伪装成新时刻。
    const auto receivedMs = steadyMilliseconds();
    const auto receivedUnixMs = unixMilliseconds();
    auto& state = sides[side];
    {
      std::scoped_lock lock(state.mutex);
      if (state.discardPartialBeforeNextBatch && receivedMs >= state.tareWindowStartedMs) {
        // parser 仍仅由读取线程操作；跨窗口半帧不能混入新窗口，累计诊断保留。
        state.parser.discardBufferedBytes();
        state.discardPartialBeforeNextBatch = false;
      }
    }
    const auto frames = state.parser.feed(bytes, size);
    {
      std::scoped_lock lock(state.mutex);
      state.parserStats = state.parser.stats();
      if (state.tarePending && receivedMs >= state.tareWindowStartedMs
          && frames.size() > kHkvlTareMaxBatchFrames) {
        state.tareError = "HKVL tare receive backlog exceeds 50 frames per batch";
        state.tareCancelled = true;
        state.tareCondition.notify_all();
      }
    }
    for (const auto& frame : frames) {
      processFrame(side, frame, receivedMs, receivedUnixMs);
    }
  }

  void processFrame(int side, const HkvlForceFrame& frame,
      double receivedMs, std::int64_t receivedUnixMs) {
    auto& state = sides[side];
    HkvlDriverSample sample;
    sample.side = side;
    sample.monotonicMs = receivedMs;
    sample.unixMs = receivedUnixMs;
    // 保留既有滤波的处理间隔；接收时间只用于样本新鲜度和自检窗口。
    const auto filterMonotonicMs = steadyMilliseconds();
    std::uint64_t windowId = 0;

    {
      std::scoped_lock lock(state.mutex);
      state.connected = true;
      state.hasSample = true;
      state.error.clear();
      state.raw = frame.values;

      if (state.tarePending && receivedMs >= state.tareWindowStartedMs) {
        windowId = state.tareWindowId;
      }

      for (std::size_t axis = 0; axis < state.tared.size(); ++axis) {
        state.tared[axis] = frame.values[axis] - state.tareBias[axis];
      }

      if (!config.lowpassEnabled || !state.filterInitialized) {
        state.filtered = state.tared;
        state.filterInitialized = true;
      } else {
        const double dtSec = std::max(
            0.0,
            (filterMonotonicMs - state.previousFilterMonotonicMs) / 1000.0);
        const double rc = 1.0 / (2.0 * 3.14159265358979323846 * config.lowpassCutoffHz);
        const double alpha = std::clamp(dtSec / (rc + dtSec), 0.0, 1.0);
        for (std::size_t axis = 0; axis < state.filtered.size(); ++axis) {
          state.filtered[axis] += alpha * (state.tared[axis] - state.filtered[axis]);
        }
      }
      state.previousFilterMonotonicMs = filterMonotonicMs;
      state.lastSampleMonotonicMs = sample.monotonicMs;

      ++state.rateWindowFrames;
      const double rateElapsedMs = sample.monotonicMs - state.rateWindowStartedMs;
      if (rateElapsedMs >= 500.0) {
        state.sampleHz =
            static_cast<double>(state.rateWindowFrames) * 1000.0 / rateElapsedMs;
        state.rateWindowFrames = 0;
        state.rateWindowStartedMs = sample.monotonicMs;
      }

      sample.raw = state.raw;
      sample.tared = state.tared;
      sample.filtered = state.filtered;
    }

    if (callback) {
      callback(sample);
    }
    // 每帧先用旧偏置接受安全检查；最后一帧回调结束后才允许窗口完成。
    {
      std::scoped_lock lock(state.mutex);
      if (state.tarePending && !state.tareCancelled
          && windowId == state.tareWindowId
          && state.tareGeneration == generation.load()
          && running.load()) {
        auto values = frame.values;
        if (state.tarePhase == TarePhase::validation) {
          for (std::size_t axis = 0; axis < values.size(); ++axis) {
            values[axis] -= state.candidateBias[axis];
          }
        }
        if (state.tareWindow.sampleCount() == 0) state.tareFirstReceivedMs = receivedMs;
        state.tareLastReceivedMs = receivedMs;
        state.tareWindow.add(values);
        if (--state.tareRemaining <= 0) {
          state.tarePending = false;
          state.tareCondition.notify_all();
        }
      }
    }
  }

#ifdef _WIN32
  static std::wstring portPath(const std::string& port) {
    const std::string path = "\\\\.\\" + port;
    return std::wstring(path.begin(), path.end());
  }

  HANDLE openPort(const std::string& port) {
    const auto path = portPath(port);
    HANDLE handle = CreateFileW(
        path.c_str(),
        GENERIC_READ,
        0,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (handle == INVALID_HANDLE_VALUE) {
      return INVALID_HANDLE_VALUE;
    }

    DCB dcb{};
    dcb.DCBlength = sizeof(dcb);
    if (!GetCommState(handle, &dcb)) {
      CloseHandle(handle);
      return INVALID_HANDLE_VALUE;
    }
    dcb.BaudRate = static_cast<DWORD>(config.baudrate);
    dcb.ByteSize = 8;
    dcb.Parity = NOPARITY;
    dcb.StopBits = ONESTOPBIT;
    dcb.fBinary = TRUE;
    dcb.fParity = FALSE;
    dcb.fOutxCtsFlow = FALSE;
    dcb.fOutxDsrFlow = FALSE;
    dcb.fDtrControl = DTR_CONTROL_DISABLE;
    dcb.fDsrSensitivity = FALSE;
    dcb.fTXContinueOnXoff = TRUE;
    dcb.fOutX = FALSE;
    dcb.fInX = FALSE;
    dcb.fErrorChar = FALSE;
    dcb.fNull = FALSE;
    dcb.fRtsControl = RTS_CONTROL_DISABLE;
    dcb.fAbortOnError = FALSE;
    if (!SetCommState(handle, &dcb)) {
      CloseHandle(handle);
      return INVALID_HANDLE_VALUE;
    }

    COMMTIMEOUTS timeouts{};
    timeouts.ReadIntervalTimeout = MAXDWORD;
    timeouts.ReadTotalTimeoutMultiplier = 0;
    timeouts.ReadTotalTimeoutConstant = 20;
    if (!SetCommTimeouts(handle, &timeouts)) {
      CloseHandle(handle);
      return INVALID_HANDLE_VALUE;
    }
    (void)SetupComm(handle, 1024 * 1024, 4096);
    return handle;
  }
#endif

  void runSide(int side) {
    const auto port = side == 0 ? config.leftPort : config.rightPort;
#ifdef _WIN32
    while (running.load(std::memory_order_acquire)) {
      HANDLE handle = openPort(port);
      if (handle == INVALID_HANDLE_VALUE) {
        updateConnection(
            side,
            false,
            "open " + port + " failed, win32=" + std::to_string(GetLastError()));
        for (int i = 0; i < 20 && running.load(std::memory_order_acquire); ++i) {
          std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        continue;
      }
      struct ClosePort {
        HANDLE handle;
        ~ClosePort() { CloseHandle(handle); }
      } closePort{handle};
      updateConnection(side, true, "");
      std::array<std::uint8_t, 4096> buffer{};
      while (running.load(std::memory_order_acquire)) {
        DWORD bytesRead = 0;
        if (!ReadFile(
                handle,
                buffer.data(),
                static_cast<DWORD>(buffer.size()),
                &bytesRead,
                nullptr)) {
          updateConnection(
              side,
              false,
              "read " + port + " failed, win32=" + std::to_string(GetLastError()));
          break;
        }
        if (bytesRead > 0) {
          processBytes(side, buffer.data(), bytesRead);
        }
      }
    }
#else
    updateConnection(side, false, "HKVL serial driver requires Windows");
#endif
    if (!failed.load()) updateConnection(side, false, running ? "serial reader stopped unexpectedly" : "");
  }
};

HkvlForceDriver::HkvlForceDriver(ReadLoop readLoop, WorkerLauncher launcher, ByteReadLoop byteReadLoop)
    : impl_(std::make_unique<Impl>()) {
  impl_->readLoop = std::move(readLoop);
  impl_->byteReadLoop = std::move(byteReadLoop);
  impl_->launcher = std::move(launcher);
}

HkvlForceDriver::~HkvlForceDriver() {
  stop();
}

void HkvlForceDriver::start(
    const HkvlSerialConfig& config,
    SampleCallback callback,
    FailureCallback failure) {
  std::scoped_lock lifecycleLock(impl_->lifecycleMutex);
  stop();
  impl_->config = config;
  impl_->callback = std::move(callback);
  impl_->failureCallback = std::move(failure);
  impl_->resetSide(0, config.leftPort);
  impl_->resetSide(1, config.rightPort);
  impl_->running.store(true, std::memory_order_release);
  impl_->failed.store(false);
  int side = 0;
  try {
    for (; side < 2 && impl_->running.load(); ++side) {
      impl_->workers[side] = launchWorker(impl_->launcher, [this, side]() {
        runWorkerBoundary([this, side]() {
          if (impl_->byteReadLoop) {
            impl_->byteReadLoop(side, [this, side](const std::uint8_t* bytes, std::size_t size) {
              impl_->processBytes(side, bytes, size);
            }, impl_->running);
          } else if (impl_->readLoop) {
            impl_->readLoop(side, [this, side](const HkvlDriverSample& sample) {
              impl_->processFrame(side, HkvlForceFrame{sample.raw}, steadyMilliseconds(), unixMilliseconds());
            }, impl_->running);
          }
          else impl_->runSide(side);
          if (impl_->running.load()) throw std::runtime_error("HKVL reader exited unexpectedly");
        }, [this, side](const char* error) { impl_->reportFailure(side, error); });
      });
    }
  } catch (...) {
    impl_->reportFailure(side, "HKVL reader worker could not be created");
    stop();
    throw;
  }
}

void HkvlForceDriver::requestStop() noexcept { impl_->requestStop(); }

void HkvlForceDriver::stop() {
  std::scoped_lock lifecycleLock(impl_->lifecycleMutex);
  impl_->requestStop();
  for (auto& worker : impl_->workers) {
    if (worker.joinable()) {
      worker.join();
    }
  }
}

bool HkvlForceDriver::running() const {
  return impl_->running.load(std::memory_order_acquire);
}

HkvlTareResult HkvlForceDriver::tare(
    int side,
    int sampleCount,
    std::chrono::milliseconds timeout,
    TareCommitCallback commitIfAllowed,
    TareProgressCallback progress) {
  std::scoped_lock tareOperation(impl_->tareMutex);
  if (side < -1 || side >= 2 || sampleCount < kHkvlTareMinSamples
      || sampleCount > kHkvlTareMaxSamples || timeout.count() <= 0
      || timeout.count() > kHkvlTareWindowTimeoutMs) {
    throw std::invalid_argument("invalid HKVL tare request");
  }
  const int first = side < 0 ? 0 : side;
  const int last = side < 0 ? 1 : side;
  std::uint64_t generation;
  {
    std::scoped_lock lifecycleLock(impl_->lifecycleMutex);
    if (!running()) throw std::runtime_error("HKVL force driver is not running");
    generation = impl_->generation.load();
  }
  const auto ensureCurrent = [&]() {
    if (!running() || impl_->generation.load() != generation) {
      throw std::runtime_error("HKVL reader stopped during tare");
    }
    if (commitIfAllowed && !commitIfAllowed([]() {})) {
      throw std::runtime_error("HKVL tare cancelled by emergency stop");
    }
  };
  const auto cancelCollection = [&]() {
    std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      // stop/start 已重置的新会话不属于本次操作，不写回旧偏置或取消新窗口。
      if (state.tareGeneration == generation) {
        state.tarePending = false;
        state.tareCancelled = true;
        state.tarePhase = Impl::TarePhase::idle;
        state.tareCondition.notify_all();
      }
    }
  };

  const auto collect = [&](Impl::TarePhase phase,
      const std::array<std::array<double, 6>, 2>& candidate) {
    {
      std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
      ensureCurrent();
      // 两侧先全部预检，再同时打开采样窗口。
      for (int index = first; index <= last; ++index) {
        const auto& state = impl_->sides[index];
        if (!state.connected || !state.hasSample) {
          throw std::runtime_error(state.port + " is not ready");
        }
      }
      for (int index = first; index <= last; ++index) {
        auto& state = impl_->sides[index];
        state.tarePending = true;
        state.tareCancelled = false;
        state.tarePhase = phase;
        state.tareGeneration = generation;
        ++state.tareWindowId;
        state.tareRemaining = sampleCount;
        state.tareWindowStartedMs = steadyMilliseconds();
        state.tareFirstReceivedMs = 0.0;
        state.tareLastReceivedMs = 0.0;
        state.tareError.clear();
        state.discardPartialBeforeNextBatch = true;
        state.tareWindow.reset();
        state.candidateBias = candidate[index];
      }
    }
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      std::unique_lock lock(state.mutex);
      while (state.tarePending && state.connected && !state.tareCancelled) {
        // 即使串口不再来帧，也定期取消被急停失效的窗口。
        ensureCurrent();
        const auto now = std::chrono::steady_clock::now();
        if (now >= deadline) throw std::runtime_error(state.port + " tare timed out");
        state.tareCondition.wait_until(lock, std::min(deadline, now + std::chrono::milliseconds(10)));
      }
      if (std::chrono::steady_clock::now() >= deadline) {
        throw std::runtime_error(state.port + " tare timed out");
      }
      ensureCurrent();
      if (!state.tareError.empty()) throw std::runtime_error(state.tareError);
      if (!state.connected || state.tareCancelled) {
        throw std::runtime_error(state.port + " disconnected during tare");
      }
    }
    std::array<HkvlSampleStatistics, 2> statistics{};
    std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
    ensureCurrent();
    if (std::chrono::steady_clock::now() >= deadline) throw std::runtime_error("HKVL tare timed out");
    for (int index = first; index <= last; ++index) {
      const auto& state = impl_->sides[index];
      if (!state.tareError.empty()) throw std::runtime_error(state.tareError);
      if (!state.connected || state.tareCancelled) {
        throw std::runtime_error(state.port + " disconnected during tare");
      }
      statistics[index] = state.tareWindow.statistics();
      if (state.tareLastReceivedMs - state.tareFirstReceivedMs < kHkvlTareMinReceiveSpanMs) {
        throw std::runtime_error(state.port + " tare receive window must span at least 100 ms");
      }
    }
    return statistics;
  };

  try {
    if (progress) progress("checking_stability", 10);
    const auto before = collect(Impl::TarePhase::stability, {});
    std::array<std::array<double, 6>, 2> candidate{};
    for (int index = first; index <= last; ++index) {
      const auto blocker = hkvlTareStabilityBlocker(before[index]);
      if (!blocker.empty()) {
        throw std::runtime_error(std::string(index == 0 ? "left " : "right ") + blocker);
      }
      candidate[index] = before[index].mean;
    }
    if (progress) progress("taring", 50);
    if (progress) progress("validating", 70);
    // 残差仅针对候选零点计算；遥测和安全检查始终继续使用已生效的旧零点。
    const auto after = collect(Impl::TarePhase::validation, candidate);
    for (int index = first; index <= last; ++index) {
      const auto blocker = hkvlTareResidualBlocker(after[index]);
      if (!blocker.empty()) {
        throw std::runtime_error(std::string(index == 0 ? "left " : "right ") + blocker);
      }
    }

    HkvlTareResult result;
    for (int index = first; index <= last; ++index) {
      result.sides[index] = HkvlTareSideResult{candidate[index], before[index], after[index]};
    }
    {
      std::scoped_lock lifecycleLock(impl_->lifecycleMutex);
      std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
      ensureCurrent();
      for (int index = first; index <= last; ++index) {
        if (!impl_->sides[index].connected || impl_->sides[index].tareCancelled) {
          throw std::runtime_error(impl_->sides[index].port + " disconnected during tare");
        }
      }
      const auto commit = [&]() {
        // 双侧验证全部成功，且安全代际仍有效时，才原子提交两个偏置。
        for (int index = first; index <= last; ++index) {
          auto& state = impl_->sides[index];
          state.tareBias = candidate[index];
          state.filterInitialized = false;
          state.previousFilterMonotonicMs = 0.0;
          state.tarePhase = Impl::TarePhase::idle;
        }
      };
      if (commitIfAllowed) {
        if (!commitIfAllowed(commit)) throw std::runtime_error("HKVL tare cancelled by emergency stop");
      } else {
        commit();
      }
    }
    result.completedAtUnixMs = unixMilliseconds();
    return result;
  } catch (...) {
    cancelCollection();
    throw;
  }
}

HkvlDriverSnapshot HkvlForceDriver::snapshot(
    double nowMonotonicMs) const {
  HkvlDriverSnapshot snapshot;
  std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
  for (int side = 0; side < 2; ++side) {
    const auto& state = impl_->sides[side];
    auto& output = snapshot.sides[side];
    output.port = state.port;
    output.connected = state.connected;
    output.hasSample = state.hasSample;
    output.raw = state.raw;
    output.tared = state.tared;
    output.filtered = state.filtered;
    output.tareBias = state.tareBias;
    output.sampleAgeMs = state.hasSample
        ? std::max(0.0, nowMonotonicMs - state.lastSampleMonotonicMs)
        : 0.0;
    output.sampleHz = state.sampleHz;
    output.validFrames = state.parserStats.validFrames;
    output.crcErrors = state.parserStats.crcErrors;
    output.nonFiniteFrames = state.parserStats.nonFiniteFrames;
    output.resyncBytes = state.parserStats.resyncBytes;
    output.error = state.error;
  }
  return snapshot;
}

}  // namespace appstation::hal
