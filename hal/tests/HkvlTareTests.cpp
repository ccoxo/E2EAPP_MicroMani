#include "HkvlForceDriver.h"
#include "HkvlForceProtocol.h"

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <future>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace appstation::hal;
using namespace std::chrono_literals;

namespace {
// Windows 默认 Sleep(1) 可能约 15.6 ms；注入源按单调时钟提供约 1 kHz。
void sampleInterval() {
  const auto until = std::chrono::steady_clock::now() + 1ms;
  while (std::chrono::steady_clock::now() < until) std::this_thread::yield();
}


void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

template <typename Check>
void await(Check check, const char* message) {
  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (!check() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(1ms);
  }
  require(check(), message);
}

template <typename Action>
void rejects(Action action, const char* expected) {
  try { action(); }
  catch (const std::exception& error) {
    require(std::string(error.what()).find(expected) != std::string::npos,
        error.what());
    return;
  }
  throw std::runtime_error("tare unexpectedly succeeded");
}

struct StreamFixture {
  std::atomic_bool emitting{true};
  std::atomic_bool validation{false};
  std::atomic_bool noisyRight{false};
  std::atomic_bool driftRight{false};
  std::atomic_bool silentRightValidation{false};
  std::atomic_bool failRightValidation{false};
  std::atomic_bool allowed{true};
  std::atomic_bool cancelOnValidationSample{false};
  std::atomic_int validationCallbacks{0};
  std::atomic_int cancelAt{0};
  std::atomic<double> baseOffset{0.0};
  HkvlForceDriver driver;

  StreamFixture() : driver([this](int side, const auto& accept, const std::atomic_bool& running) {
    int frame = 0;
    while (running.load()) {
      if (side == 1 && validation.load() && failRightValidation.load()) {
        throw std::runtime_error("injected serial disconnect");
      }
      if (emitting.load() && !(side == 1 && validation.load() && silentRightValidation.load())) {
        HkvlDriverSample sample;
        sample.raw[0] = (side == 0 ? 1.0 : -0.5) + baseOffset.load();
        sample.raw[3] = side == 0 ? 0.01 : -0.02;
        if (side == 1 && noisyRight.load()) sample.raw[0] += (++frame % 2 == 0 ? 0.5 : -0.5);
        if (side == 1 && validation.load() && driftRight.load()) sample.raw[0] += 0.25;
        accept(sample);
      }
      sampleInterval();
    }
  }) { start(); }

  ~StreamFixture() { driver.stop(); }

  void start() {
    driver.start({}, [this](const HkvlDriverSample& sample) {
      if (sample.side == 0 && validation.load()) {
        const int count = ++validationCallbacks;
        if (cancelOnValidationSample.load() && count >= cancelAt.load()) allowed.store(false);
      }
    });
    await([&]() {
      const auto state = driver.snapshot(0);
      return state.sides[0].hasSample && state.sides[1].hasSample;
    }, "injected readers did not enter the real driver sample path");
  }

  HkvlForceDriver::TareCommitCallback guard() {
    return [this](const std::function<void()>& commit) {
      if (!allowed.load()) return false;
      commit();
      return true;
    };
  }

  HkvlForceDriver::TareProgressCallback progress() {
    return [this](const std::string& phase, int) {
      validation.store(phase == "validating");
    };
  }

  HkvlTareResult tare(int side = -1, int samples = kHkvlTareMinSamples,
      std::chrono::milliseconds timeout = 2s) {
    validationCallbacks.store(0);
    return driver.tare(side, samples, timeout, guard(), progress());
  }

  void requireBias(double left, double right) {
    const auto state = driver.snapshot(0);
    require(std::abs(state.sides[0].tareBias[0] - left) < 1e-9,
        "hardware-left bias changed unexpectedly");
    require(std::abs(state.sides[1].tareBias[0] - right) < 1e-9,
        "hardware-right bias changed unexpectedly");
  }
};

void testStatisticsAndThresholds() {
  HkvlSampleAccumulator window;
  require(!hkvlTareStabilityBlocker(window.statistics()).empty(), "empty window accepted");
  std::array<double, 6> value{};
  value[0] = 1000000.01;
  for (int index = 0; index < kHkvlTareMinSamples; ++index) {
    value[0] = index % 2 ? 999999.99 : 1000000.01;
    window.add(value);
  }
  const auto stats = window.statistics();
  require(stats.sampleCount == kHkvlTareMinSamples && std::abs(stats.mean[0] - 1000000.0) < 1e-9,
      "sample mean/count are incorrect");
  require(std::abs(stats.standardDeviation[0] - 0.01) < 1e-7
      && std::abs(stats.peakToPeak[0] - 0.02) < 1e-7,
      "sample statistics lost small vibration on large bias");
  require(hkvlTareStabilityBlocker(stats).empty(), "stable window rejected");
  require(!hkvlTareResidualBlocker(stats).empty(), "nonzero residual accepted");
  auto invalid = stats;
  invalid.standardDeviation[3] = 0.003;
  require(hkvlTareStabilityBlocker(invalid).find("Mx") != std::string::npos,
      "torque standard deviation threshold not enforced");
  invalid = stats;
  invalid.peakToPeak[1] = 0.31;
  require(hkvlTareStabilityBlocker(invalid).find("Fy") != std::string::npos,
      "force peak-to-peak threshold not enforced");
  window.reset();
  value = {};
  value[5] = std::numeric_limits<double>::quiet_NaN();
  rejects([&]() { window.add(value); }, "finite");
  require(window.sampleCount() == 0, "nonfinite sample partially changed statistics");
}

void testSuccessAndSingleSide() {
  StreamFixture fixture;
  const auto result = fixture.tare();
  fixture.requireBias(1.0, -0.5);
  require(result.completedAtUnixMs > 0, "tare completion timestamp missing");
  for (const auto& side : result.sides) {
    require(side.before.sampleCount == kHkvlTareMinSamples && side.after.sampleCount == kHkvlTareMinSamples,
        "tare did not collect both complete windows");
    require(std::abs(side.after.mean[0]) < 1e-9, "candidate residual was not measured");
  }
  fixture.baseOffset.store(0.05);
  const auto single = fixture.tare(0);
  fixture.requireBias(1.05, -0.5);
  require(single.sides[1].before.sampleCount == 0, "single-side tare sampled the other side");
}

void testInstabilityAndResidualPreserveBothBiases() {
  StreamFixture fixture;
  fixture.tare();
  fixture.noisyRight.store(true);
  rejects([&]() { fixture.tare(); }, "right tare stability");
  fixture.requireBias(1.0, -0.5);
  fixture.noisyRight.store(false);
  fixture.driftRight.store(true);
  rejects([&]() { fixture.tare(); }, "right post-tare residual");
  fixture.requireBias(1.0, -0.5);
}

void testSlowSecondSideDoesNotPartiallyCommit() {
  StreamFixture fixture;
  fixture.silentRightValidation.store(true);
  rejects([&]() { fixture.tare(-1, kHkvlTareMinSamples, 1s); }, "timed out");
  fixture.requireBias(0.0, 0.0);
  fixture.silentRightValidation.store(false);
  fixture.tare();
  fixture.requireBias(1.0, -0.5);
}

void testCandidateIsInvisibleUntilBothSidesValidate() {
  StreamFixture fixture;
  std::atomic_bool validating{false};
  fixture.silentRightValidation.store(true);
  auto operation = std::async(std::launch::async, [&]() {
    return fixture.driver.tare(-1, kHkvlTareMinSamples, 2s, fixture.guard(),
        [&](const std::string& phase, int) {
          fixture.validation.store(phase == "validating");
          validating.store(phase == "validating");
        });
  });
  await([&]() { return validating.load() && fixture.validationCallbacks.load() >= kHkvlTareMinSamples; },
      "left side did not complete validation while right was stalled");
  const auto pending = fixture.driver.snapshot(0);
  fixture.requireBias(0.0, 0.0);
  require(pending.sides[0].tared[0] == 1.0 && pending.sides[1].tared[0] == -0.5,
      "candidate zero point leaked into live samples before validation");
  fixture.silentRightValidation.store(false);
  operation.get();
  fixture.requireBias(1.0, -0.5);
}

void testWorkerFailureCancelsValidationWithoutRollbackIntoRestart() {
  StreamFixture fixture;
  fixture.tare();
  fixture.validation.store(false);
  fixture.failRightValidation.store(true);
  rejects([&]() { fixture.tare(); }, "stopped during tare");
  fixture.driver.stop();
  fixture.requireBias(1.0, -0.5);
  require(!fixture.driver.snapshot(0).sides[1].error.empty(), "worker failure diagnostic missing");
  fixture.failRightValidation.store(false);
  fixture.validation.store(false);
  fixture.start();
  fixture.requireBias(0.0, 0.0);
  fixture.tare();
  fixture.requireBias(1.0, -0.5);
}

void testFinalWindowSampleStillRunsSafety() {
  StreamFixture fixture;
  fixture.cancelOnValidationSample.store(true);
  fixture.cancelAt.store(kHkvlTareMinSamples);
  rejects([&]() { fixture.tare(0, kHkvlTareMinSamples); }, "emergency stop");
  require(fixture.validationCallbacks.load() >= kHkvlTareMinSamples, "safety callback did not see validation samples");
  fixture.requireBias(0.0, 0.0);
}

void testStoppedReaderCancelsBlockedTare() {
  StreamFixture fixture;
  fixture.emitting.store(false);
  std::atomic_bool entered{false};
  auto operation = std::async(std::launch::async, [&]() {
    rejects([&]() {
      fixture.driver.tare(-1, 200, 2s, fixture.guard(),
          [&](const std::string&, int) { entered.store(true); });
    }, "stopped during tare");
  });
  await([&]() { return entered.load(); }, "tare did not start");
  fixture.driver.stop();
  require(operation.wait_for(500ms) == std::future_status::ready,
      "stop did not promptly cancel a stalled tare");
  operation.get();
  fixture.requireBias(0.0, 0.0);
}

void testRestartCannotAcceptOrRestoreOldTare() {
  StreamFixture fixture;
  fixture.tare();
  std::atomic_bool entered{false};
  std::atomic_bool release{false};
  auto operation = std::async(std::launch::async, [&]() {
    rejects([&]() {
      fixture.driver.tare(-1, kHkvlTareMinSamples, 2s, fixture.guard(), [&](const std::string& phase, int) {
        if (phase == "validating") {
          entered.store(true);
          while (!release.load()) std::this_thread::sleep_for(1ms);
        }
      });
    }, "stopped during tare");
  });
  await([&]() { return entered.load(); }, "tare never reached validation");
  fixture.driver.stop();
  fixture.baseOffset.store(0.05);
  fixture.start();
  release.store(true);
  operation.get();
  fixture.requireBias(0.0, 0.0);
  fixture.tare();
  fixture.requireBias(1.05, -0.45);
}

void testPreflightAndProgressFailureLeaveBiasUntouched() {
  StreamFixture fixture;
  fixture.allowed.store(false);
  rejects([&]() { fixture.tare(); }, "emergency stop");
  fixture.allowed.store(true);
  rejects([&]() {
    fixture.driver.tare(-1, kHkvlTareMinSamples, 2s, fixture.guard(), [](const std::string& phase, int) {
      if (phase == "validating") throw std::runtime_error("progress failed");
    });
  }, "progress failed");
  fixture.requireBias(0.0, 0.0);
  fixture.tare();
  fixture.requireBias(1.0, -0.5);
}

std::vector<std::uint8_t> frameBytes(float force, int count = 1) {
  std::vector<std::uint8_t> bytes;
  for (int index = 0; index < count; ++index) {
    std::array<std::uint8_t, kHkvlForceFrameSize> frame{};
    frame[0] = 0x53;
    frame[1] = 0x54;
    std::memcpy(frame.data() + 2, &force, sizeof(force));
    const auto crc = hkvlModbusCrc(frame.data(), 26);
    frame[26] = static_cast<std::uint8_t>(crc);
    frame[27] = static_cast<std::uint8_t>(crc >> 8);
    bytes.insert(bytes.end(), frame.begin(), frame.end());
  }
  return bytes;
}

void testSampleCountBounds() {
  StreamFixture fixture;
  for (const auto count : {0, 1, 12, 199, 1001, 5000}) {
    rejects([&]() { fixture.tare(-1, count); }, "invalid HKVL tare request");
  }
  fixture.requireBias(0.0, 0.0);
  const auto maximum = fixture.tare(-1, kHkvlTareMaxSamples);
  require(maximum.sides[0].before.sampleCount == kHkvlTareMaxSamples
      && maximum.sides[1].after.sampleCount == kHkvlTareMaxSamples,
      "maximum supported window did not complete within its budget");
}

void testCompressedAndBackloggedByteBatchesAreRejected() {
  for (const int batchFrames : {50, 51}) {
    const auto bytes = frameBytes(0.5F, batchFrames);
    HkvlForceDriver driver({}, {}, [&](int, const auto& receive, const std::atomic_bool& running) {
      while (running.load()) {
        receive(bytes.data(), bytes.size());
        sampleInterval();
      }
    });
    driver.start({}, {});
    await([&]() { return driver.snapshot(0).sides[0].hasSample && driver.snapshot(0).sides[1].hasSample; },
        "byte readers did not become ready");
    rejects([&]() { driver.tare(-1); }, batchFrames == 50 ? "at least 100 ms" : "receive backlog");
    const auto snapshot = driver.snapshot(0);
    require(snapshot.sides[0].tareBias[0] == 0 && snapshot.sides[1].tareBias[0] == 0,
        "invalid byte window changed a bias");
  }
}

void testQueuedBatchCannotBecomeNewWindowWhileCallbacksAreDelayed() {
  std::atomic_bool oldBatchEntered{false};
  std::atomic_bool releaseOldBatch{false};
  std::atomic_bool collectionChecked{false};
  std::atomic_int oldCallbacks{0};
  double oldTimestamp = 0;
  std::atomic_bool sameTimestamp{true};
  const auto oldBytes = frameBytes(0.5F, 240);
  const auto freshBytes = frameBytes(0.0F);
  HkvlForceDriver driver({}, {}, [&](int side, const auto& receive, const std::atomic_bool& running) {
    if (side == 0) receive(oldBytes.data(), oldBytes.size());
    while (running.load()) {
      receive(freshBytes.data(), freshBytes.size());
      sampleInterval();
    }
  });
  driver.start({}, [&](const HkvlDriverSample& sample) {
    if (sample.side != 0 || sample.raw[0] != 0.5) return;
    if (oldCallbacks.fetch_add(1) == 0) {
      oldTimestamp = sample.monotonicMs;
      oldBatchEntered.store(true);
      while (!releaseOldBatch.load() && driver.running()) std::this_thread::sleep_for(1ms);
    } else {
      if (sample.monotonicMs != oldTimestamp) sameTimestamp.store(false);
      sampleInterval();
    }
  });
  await([&]() { return oldBatchEntered.load() && driver.snapshot(0).sides[1].hasSample; },
      "old byte batch did not reach the callback barrier");
  auto pending = std::async(std::launch::async, [&]() {
    return driver.tare(-1, kHkvlTareMinSamples, 2s, [&](const auto& commit) {
      collectionChecked.store(true);
      commit();
      return true;
    });
  });
  await([&]() { return collectionChecked.load(); }, "tare did not reach collection");
  releaseOldBatch.store(true);
  const auto result = pending.get();
  require(oldCallbacks.load() == 240 && sameTimestamp.load(), "one queued batch acquired multiple timestamps");
  require(result.sides[0].bias[0] == 0 && result.sides[1].bias[0] == 0,
      "pre-window queued bytes contaminated the new bias");
}

void testWindowBoundaryDiscardsPartialOldFrame() {
  std::array<std::atomic_bool, 2> partialSent{};
  std::atomic_bool releaseTail{false};
  std::atomic_bool collectionChecked{false};
  std::atomic_int contaminatedSamples{0};
  const auto oldFrame = frameBytes(10.0F);
  const auto freshFrame = frameBytes(0.0F);
  HkvlForceDriver driver({}, {}, [&](int side, const auto& receive, const std::atomic_bool& running) {
    receive(freshFrame.data(), freshFrame.size());
    receive(oldFrame.data(), 14);
    partialSent[side].store(true);
    while (!releaseTail.load() && running.load()) std::this_thread::sleep_for(1ms);
    receive(oldFrame.data() + 14, oldFrame.size() - 14);
    while (running.load()) {
      receive(freshFrame.data(), freshFrame.size());
      sampleInterval();
    }
  });
  driver.start({}, [&](const HkvlDriverSample& sample) {
    if (sample.raw[0] == 10.0) ++contaminatedSamples;
  });
  await([&]() { return partialSent[0].load() && partialSent[1].load(); }, "old partial frames were not queued");
  auto pending = std::async(std::launch::async, [&]() {
    return driver.tare(-1, kHkvlTareMinSamples, 2s, [&](const auto& commit) {
      collectionChecked.store(true);
      commit();
      return true;
    });
  });
  await([&]() { return collectionChecked.load(); }, "tare did not reach collection");
  releaseTail.store(true);
  const auto result = pending.get();
  require(contaminatedSamples.load() == 0 && result.sides[0].bias[0] == 0 && result.sides[1].bias[0] == 0,
      "old partial frame crossed into the new window");
  require(driver.snapshot(0).sides[0].validFrames >= 401,
      "discarding partial bytes reset cumulative frame diagnostics");
}

}  // namespace

int main() {
  try {
    testStatisticsAndThresholds();
    testSuccessAndSingleSide();
    testInstabilityAndResidualPreserveBothBiases();
    testSlowSecondSideDoesNotPartiallyCommit();
    testCandidateIsInvisibleUntilBothSidesValidate();
    testWorkerFailureCancelsValidationWithoutRollbackIntoRestart();
    testFinalWindowSampleStillRunsSafety();
    testStoppedReaderCancelsBlockedTare();
    testRestartCannotAcceptOrRestoreOldTare();
    testPreflightAndProgressFailureLeaveBiasUntouched();
    testSampleCountBounds();
    testCompressedAndBackloggedByteBatchesAreRejected();
    testQueuedBatchCannotBecomeNewWindowWhileCallbacksAreDelayed();
    testWindowBoundaryDiscardsPartialOldFrame();
    std::cout << "HkvlTareTests passed (14 cases, injected samples and byte batches only)\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "HkvlTareTests failed: " << error.what() << "\n";
    return 1;
  }
}
