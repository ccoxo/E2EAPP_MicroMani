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
    TareCommitCallback tareCommit;
    int tareRemaining{0};
    int tareRequested{0};
    std::array<double, 6> tareSum{};
  };

  HkvlSerialConfig config;
  SampleCallback callback;
  FailureCallback failureCallback;
  ReadLoop readLoop;
  WorkerLauncher launcher;
  std::array<SideState, 2> sides;
  std::array<std::thread, 2> workers;
  std::mutex tareMutex;
  std::recursive_mutex lifecycleMutex;
  std::atomic<bool> running{false};
  std::atomic_bool failed{false};

  void requestStop() noexcept {
    running.store(false, std::memory_order_release);
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
    state.tareCommit = {};
    state.tareRemaining = 0;
    state.tareRequested = 0;
    state.tareSum = {};
  }

  void updateConnection(int side, bool connected, const std::string& error) {
    auto& state = sides[side];
    std::scoped_lock lock(state.mutex);
    state.connected = connected;
    state.error = error;
    if (!connected) {
      state.tareCondition.notify_all();
    }
  }

  void processBytes(int side, const std::uint8_t* bytes, std::size_t size) {
    auto& state = sides[side];
    const auto frames = state.parser.feed(bytes, size);
    {
      std::scoped_lock lock(state.mutex);
      state.parserStats = state.parser.stats();
    }
    for (const auto& frame : frames) {
      processFrame(side, frame);
    }
  }

  void processFrame(int side, const HkvlForceFrame& frame) {
    auto& state = sides[side];
    HkvlDriverSample sample;
    sample.side = side;
    sample.monotonicMs = steadyMilliseconds();
    sample.unixMs = unixMilliseconds();

    {
      std::scoped_lock lock(state.mutex);
      state.connected = true;
      state.hasSample = true;
      state.error.clear();
      state.raw = frame.values;

      if (state.tarePending) {
        for (std::size_t axis = 0; axis < state.tareSum.size(); ++axis) {
          state.tareSum[axis] += frame.values[axis];
        }
        --state.tareRemaining;
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
            (sample.monotonicMs - state.previousFilterMonotonicMs) / 1000.0);
        const double rc = 1.0 / (2.0 * 3.14159265358979323846 * config.lowpassCutoffHz);
        const double alpha = std::clamp(dtSec / (rc + dtSec), 0.0, 1.0);
        for (std::size_t axis = 0; axis < state.filtered.size(); ++axis) {
          state.filtered[axis] += alpha * (state.tared[axis] - state.filtered[axis]);
        }
      }
      state.previousFilterMonotonicMs = sample.monotonicMs;
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
    // 最后一帧仍使用旧偏置接受力安全检查，通过后才允许提交新零点。
    {
      std::scoped_lock lock(state.mutex);
      if (state.tarePending && state.tareRemaining <= 0) {
        const auto commit = [&]() {
          for (std::size_t axis = 0; axis < state.tareBias.size(); ++axis) {
            state.tareBias[axis] = state.tareSum[axis] / static_cast<double>(state.tareRequested);
          }
          state.filterInitialized = false;
          state.previousFilterMonotonicMs = 0.0;
        };
        if (state.tareCommit) {
          state.tareCancelled = !state.tareCommit(commit);
        } else {
          commit();
        }
        state.tarePending = false;
        state.tareCondition.notify_all();
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

HkvlForceDriver::HkvlForceDriver(ReadLoop readLoop, WorkerLauncher launcher)
    : impl_(std::make_unique<Impl>()) {
  impl_->readLoop = std::move(readLoop);
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
          if (impl_->readLoop) impl_->readLoop(side, impl_->callback, impl_->running);
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

void HkvlForceDriver::tare(
    int side,
    int sampleCount,
    std::chrono::milliseconds timeout,
    TareCommitCallback commitIfAllowed) {
  std::scoped_lock tareOperation(impl_->tareMutex);
  if (!running()) {
    throw std::runtime_error("HKVL force driver is not running");
  }
  if (side < -1 || side >= 2 || sampleCount <= 0) {
    throw std::invalid_argument("invalid HKVL tare request");
  }
  const int first = side < 0 ? 0 : side;
  const int last = side < 0 ? 1 : side;
  {
    std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
    // 双侧先全部预检，避免一侧断连时另一侧留下已启动的去皮窗口。
    for (int index = first; index <= last; ++index) {
      if (!impl_->sides[index].connected) {
        throw std::runtime_error(impl_->sides[index].port + " is not connected");
      }
    }
    if (commitIfAllowed && !commitIfAllowed([]() {})) {
      throw std::runtime_error("HKVL tare cancelled by emergency stop");
    }
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      state.tarePending = true;
      state.tareCancelled = false;
      state.tareCommit = commitIfAllowed;
      state.tareRemaining = sampleCount;
      state.tareRequested = sampleCount;
      state.tareSum = {};
    }
  }

  const auto deadline = std::chrono::steady_clock::now() + timeout;
  try {
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      std::unique_lock lock(state.mutex);
      while (state.tarePending && state.connected && running()) {
        // 即使串口不再来帧，也定期取消被急停失效的窗口。
        if (commitIfAllowed && !commitIfAllowed([]() {})) {
          throw std::runtime_error("HKVL tare cancelled by emergency stop");
        }
        const auto now = std::chrono::steady_clock::now();
        if (now >= deadline) throw std::runtime_error(state.port + " tare timed out");
        state.tareCondition.wait_until(lock, std::min(deadline, now + std::chrono::milliseconds(10)));
      }
      if (!running()) throw std::runtime_error("HKVL reader stopped during tare");
      if (state.tareCancelled) throw std::runtime_error("HKVL tare cancelled by emergency stop");
      if (!state.connected) throw std::runtime_error(state.port + " disconnected during tare");
    }
  } catch (...) {
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      std::scoped_lock lock(state.mutex);
      state.tarePending = false;
      state.tareCancelled = true;
      state.tareCommit = {};
      state.tareCondition.notify_all();
    }
    throw;
  }
}

HkvlDriverSnapshot HkvlForceDriver::snapshot(
    double nowMonotonicMs) const {
  HkvlDriverSnapshot snapshot;
  for (int side = 0; side < 2; ++side) {
    const auto& state = impl_->sides[side];
    std::scoped_lock lock(state.mutex);
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
