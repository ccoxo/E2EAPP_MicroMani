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
  enum class TarePhase {
    idle,
    stability,
    validation,
  };

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
    TarePhase tarePhase{TarePhase::idle};
    int tareRemaining{0};
    HkvlSampleAccumulator tareWindow;
  };

  HkvlSerialConfig config;
  SampleCallback callback;
  std::array<SideState, 2> sides;
  std::array<std::thread, 2> workers;
  std::atomic<bool> running{false};
  std::mutex tareMutex;

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
    state.tarePhase = TarePhase::idle;
    state.tareRemaining = 0;
    state.tareWindow.reset();
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

      if (state.tarePhase == TarePhase::stability) {
        state.tareWindow.add(frame.values);
        --state.tareRemaining;
        if (state.tareRemaining <= 0) {
          state.tarePhase = TarePhase::idle;
          state.tareCondition.notify_all();
        }
      }

      for (std::size_t axis = 0; axis < state.tared.size(); ++axis) {
        state.tared[axis] = frame.values[axis] - state.tareBias[axis];
      }

      if (state.tarePhase == TarePhase::validation) {
        state.tareWindow.add(state.tared);
        --state.tareRemaining;
        if (state.tareRemaining <= 0) {
          state.tarePhase = TarePhase::idle;
          state.tareCondition.notify_all();
        }
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
      CloseHandle(handle);
    }
#else
    updateConnection(side, false, "HKVL serial driver requires Windows");
#endif
    updateConnection(side, false, running ? "serial reader stopped unexpectedly" : "");
  }
};

HkvlForceDriver::HkvlForceDriver()
    : impl_(std::make_unique<Impl>()) {}

HkvlForceDriver::~HkvlForceDriver() {
  stop();
}

void HkvlForceDriver::start(
    const HkvlSerialConfig& config,
    SampleCallback callback) {
  stop();
  impl_->config = config;
  impl_->callback = std::move(callback);
  impl_->resetSide(0, config.leftPort);
  impl_->resetSide(1, config.rightPort);
  impl_->running.store(true, std::memory_order_release);
  for (int side = 0; side < 2; ++side) {
    impl_->workers[side] = std::thread([this, side]() { impl_->runSide(side); });
  }
}

void HkvlForceDriver::stop() {
  impl_->running.store(false, std::memory_order_release);
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
    TareProgressCallback progress) {
  std::scoped_lock operationLock(impl_->tareMutex);
  if (!running()) {
    throw std::runtime_error("HKVL force driver is not running");
  }
  if (side < -1 || side >= 2 || sampleCount <= 0) {
    throw std::invalid_argument("invalid HKVL tare request");
  }
  const int first = side < 0 ? 0 : side;
  const int last = side < 0 ? 1 : side;

  auto cancelCollection = [&]() {
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      std::scoped_lock lock(state.mutex);
      state.tarePhase = Impl::TarePhase::idle;
      state.tareRemaining = 0;
    }
  };

  auto collect = [&](Impl::TarePhase phase) {
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      std::scoped_lock lock(state.mutex);
      if (!state.connected || !state.hasSample) {
        throw std::runtime_error(state.port + " is not ready");
      }
      state.tareWindow.reset();
      state.tareRemaining = sampleCount;
      state.tarePhase = phase;
    }

    const auto deadline = std::chrono::steady_clock::now() + timeout;
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      std::unique_lock lock(state.mutex);
      if (!state.tareCondition.wait_until(
              lock,
              deadline,
              [&state]() {
                return state.tarePhase == Impl::TarePhase::idle
                    || !state.connected;
              })) {
        state.tarePhase = Impl::TarePhase::idle;
        throw std::runtime_error(state.port + " tare sampling timed out");
      }
      if (!state.connected) {
        state.tarePhase = Impl::TarePhase::idle;
        throw std::runtime_error(state.port + " disconnected during tare");
      }
    }

    std::array<HkvlSampleStatistics, 2> statistics{};
    for (int index = first; index <= last; ++index) {
      const auto& state = impl_->sides[index];
      std::scoped_lock lock(state.mutex);
      statistics[index] = state.tareWindow.statistics();
    }
    return statistics;
  };

  std::array<std::array<double, 6>, 2> previousBias{};
  bool biasApplied = false;
  auto restoreBias = [&]() {
    if (!biasApplied) {
      return;
    }
    std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
    for (int index = first; index <= last; ++index) {
      auto& state = impl_->sides[index];
      state.tareBias = previousBias[index];
      state.filterInitialized = false;
      state.previousFilterMonotonicMs = 0.0;
    }
  };

  try {
    if (progress) {
      progress("checking_stability", 10);
    }
    const auto before = collect(Impl::TarePhase::stability);
    for (int index = first; index <= last; ++index) {
      const auto blocker = hkvlTareStabilityBlocker(before[index]);
      if (!blocker.empty()) {
        throw std::runtime_error(
            std::string(index == 0 ? "left " : "right ") + blocker);
      }
    }

    if (progress) {
      progress("taring", 50);
    }
    {
      std::scoped_lock lock(impl_->sides[0].mutex, impl_->sides[1].mutex);
      for (int index = first; index <= last; ++index) {
        auto& state = impl_->sides[index];
        previousBias[index] = state.tareBias;
        state.tareBias = before[index].mean;
        state.filterInitialized = false;
        state.previousFilterMonotonicMs = 0.0;
      }
      biasApplied = true;
    }

    if (progress) {
      progress("validating", 70);
    }
    const auto after = collect(Impl::TarePhase::validation);
    for (int index = first; index <= last; ++index) {
      const auto blocker = hkvlTareResidualBlocker(after[index]);
      if (!blocker.empty()) {
        throw std::runtime_error(
            std::string(index == 0 ? "left " : "right ") + blocker);
      }
    }

    HkvlTareResult result;
    for (int index = first; index <= last; ++index) {
      result.sides[index].bias = before[index].mean;
      result.sides[index].before = before[index];
      result.sides[index].after = after[index];
    }
    result.completedAtUnixMs = unixMilliseconds();
    return result;
  } catch (...) {
    cancelCollection();
    restoreBias();
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
