#include "ForceComplianceController.h"
#include "ForceControlRuntime.h"
#include "ForceSafetyLatch.h"
#include "HalJson.h"
#include "HkvlForceProtocol.h"
#include "LTDMCDriver.h"

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using appstation::hal::ForceComplianceConfig;
using appstation::hal::ForceComplianceController;
using appstation::hal::ForceControlRuntime;
using appstation::hal::ForceRuntimeConfig;
using appstation::hal::ForceSafetyConfig;
using appstation::hal::ForceSafetyLatch;
using appstation::hal::HkvlForceFrame;
using appstation::hal::HkvlForceParser;
using appstation::hal::LTDMCDriver;

void require(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

bool near(double actual, double expected, double tolerance = 1e-5) {
  return std::abs(actual - expected) <= tolerance;
}

std::vector<std::uint8_t> parseHexLine(const std::string& line) {
  std::istringstream input(line);
  std::vector<std::uint8_t> bytes;
  std::string token;
  while (input >> token) {
    bytes.push_back(static_cast<std::uint8_t>(std::stoul(token, nullptr, 16)));
  }
  return bytes;
}

std::vector<std::vector<std::uint8_t>> fixtureFrames() {
  std::ifstream input("..\\tests\\fixtures\\hkvl_active_v1_frames.hex");
  require(input.good(), "fixture file is missing");
  std::vector<std::vector<std::uint8_t>> frames;
  std::string line;
  while (std::getline(input, line)) {
    if (line.empty() || line.front() == '#') {
      continue;
    }
    frames.push_back(parseHexLine(line));
  }
  require(frames.size() == 2, "fixture must contain two frames");
  return frames;
}

std::vector<std::uint8_t> frameForValues(const std::array<float, 6>& values) {
  std::vector<std::uint8_t> frame(28, 0);
  frame[0] = 0x53;
  frame[1] = 0x54;
  for (std::size_t i = 0; i < values.size(); ++i) {
    std::memcpy(frame.data() + 2 + i * sizeof(float), &values[i], sizeof(float));
  }
  const auto crc = appstation::hal::hkvlModbusCrc(frame.data(), 26);
  frame[26] = static_cast<std::uint8_t>(crc & 0xFF);
  frame[27] = static_cast<std::uint8_t>(crc >> 8);
  return frame;
}

void testParser() {
  const auto fixtures = fixtureFrames();
  HkvlForceParser parser;

  auto frames = parser.feed(fixtures[0]);
  require(frames.size() == 1, "complete frame must parse");
  require(near(frames[0].values[0], 2.30159), "Fx decode mismatch");
  require(near(frames[0].values[5], -0.028107), "Mz decode mismatch");

  parser.reset();
  frames = parser.feed(fixtures[0].data(), 7);
  require(frames.empty(), "split prefix must wait for the remainder");
  frames = parser.feed(fixtures[0].data() + 7, fixtures[0].size() - 7);
  require(frames.size() == 1, "split frame must reassemble");

  parser.reset();
  auto joined = fixtures[0];
  joined.insert(joined.end(), fixtures[1].begin(), fixtures[1].end());
  frames = parser.feed(joined);
  require(frames.size() == 2, "concatenated frames must both parse");

  parser.reset();
  auto garbage = std::vector<std::uint8_t>{0x00, 0x53, 0x00};
  garbage.insert(garbage.end(), fixtures[0].begin(), fixtures[0].end());
  frames = parser.feed(garbage);
  require(frames.size() == 1, "parser must recover after leading garbage");
  require(parser.stats().resyncBytes == 3, "leading garbage resync count mismatch");

  parser.reset();
  auto badThenGood = fixtures[0];
  badThenGood[8] ^= 0x01;
  badThenGood.insert(badThenGood.end(), fixtures[1].begin(), fixtures[1].end());
  frames = parser.feed(badThenGood);
  require(frames.size() == 1, "bad CRC frame must be discarded");
  require(parser.stats().crcErrors == 1, "bad CRC must increment crcErrors");

  parser.reset();
  const auto nonFinite = frameForValues(
      {std::numeric_limits<float>::quiet_NaN(), 0.0F, 0.0F, 0.0F, 0.0F, 0.0F});
  frames = parser.feed(nonFinite);
  require(frames.empty(), "NaN frame must be invalid");
  require(parser.stats().nonFiniteFrames == 1, "NaN frame must increment nonFiniteFrames");
}

ForceSafetyConfig safetyConfig() {
  ForceSafetyConfig config;
  config.warn = {2.0, 2.0, 3.0, 0.02, 0.02, 0.02};
  config.stop = {4.0, 4.0, 5.0, 0.04, 0.04, 0.04};
  config.watchdogMs = 50.0;
  config.acknowledgeStableMs = 500.0;
  return config;
}

void testOfficialHkvlSafetyDefaults() {
  const ForceSafetyConfig config;
  require(
      config.stop == std::array<double, 6>{30.0, 30.0, 30.0, 1.0, 1.0, 1.0},
      "HKVL-36A stop defaults must match the published full-scale ratings");
}

void testOfficialHkvlHardwareSidePorts() {
  const appstation::hal::HkvlSerialConfig config;
  require(
      config.leftPort == "COM15" && config.rightPort == "COM14",
      "HKVL-36A ports must follow the hardware-side Card1/Card0 binding");
}

void testOfficialHkvlMotionAlignedAxisSigns() {
  const ForceRuntimeConfig config;
  require(
      config.axisSign[0]
          == std::array<double, 6>{-1.0, 1.0, -1.0, 1.0, -1.0, -1.0},
      "hardware-left COM15/Card1 force signs must follow effective motion directions");
  require(
      config.axisSign[1]
          == std::array<double, 6>{-1.0, -1.0, -1.0, 1.0, 1.0, 1.0},
      "hardware-right COM14/Card0 force signs must follow effective motion directions");
}

void testSafetyLatch() {
  const std::array<double, 6> unloaded{};

  ForceSafetyLatch threeFrame(safetyConfig(), 0.0);
  threeFrame.onSample(0, {4.0, 0, 0, 0, 0, 0}, 1.0);
  threeFrame.onSample(0, {4.0, 0, 0, 0, 0, 0}, 2.0);
  require(!threeFrame.latched(), "100 percent must not trip before three samples");
  const auto trip = threeFrame.onSample(0, {4.0, 0, 0, 0, 0, 0}, 3.0);
  require(trip.has_value() && threeFrame.latched(), "100 percent must trip on the third sample");
  require(trip->side == 0 && trip->channel == 0, "trip source must identify side and channel");

  ForceSafetyLatch immediate(safetyConfig(), 0.0);
  require(
      immediate.onSample(1, {0, 0, 6.0, 0, 0, 0}, 1.0).has_value(),
      "120 percent must trip in one sample");

  ForceSafetyLatch watchdog(safetyConfig(), 0.0);
  watchdog.onSample(0, unloaded, 1.0);
  watchdog.onSample(1, unloaded, 1.0);
  require(!watchdog.checkWatchdog(50.9).has_value(), "watchdog must not trip before timeout");
  const auto watchdogTrip = watchdog.checkWatchdog(51.1);
  require(watchdogTrip.has_value(), "watchdog must trip after timeout");
  require(watchdogTrip->reason == "watchdog_timeout", "watchdog reason mismatch");

  auto stableConfig = safetyConfig();
  stableConfig.watchdogMs = 1000.0;
  ForceSafetyLatch stable(stableConfig, 0.0);
  stable.onSample(0, {4.8, 0, 0, 0, 0, 0}, 1.0);
  stable.onSample(0, unloaded, 2.0);
  stable.onSample(1, unloaded, 2.0);
  stable.onSample(0, unloaded, 501.0);
  stable.onSample(1, unloaded, 501.0);
  std::string blocker;
  require(!stable.canAcknowledge(501.0, &blocker), "stable window must be a full 500 ms");
  stable.onSample(0, unloaded, 502.0);
  stable.onSample(1, unloaded, 502.0);
  require(stable.canAcknowledge(502.0, &blocker), "healthy unloaded samples must unlock acknowledgement");
  stable.acknowledge(502.0);
  require(!stable.latched(), "acknowledge must only clear the safety latch");
}

ForceComplianceConfig complianceConfig() {
  ForceComplianceConfig config;
  config.enabled = true;
  for (auto& side : config.sides) {
    side.mappingConfirmed = true;
    side.matrix = {1.0, 0.0, 0.0, 1.0};
    side.deadbandN = {1.0, 1.0};
    side.gainUmPerNs = {100.0, 100.0};
    side.maxStepUm = {5.0, 5.0};
    side.maxOffsetUm = {8.0, 8.0};
  }
  return config;
}

void testCompliance() {
  ForceComplianceController controller;
  controller.configure(complianceConfig());

  auto result = controller.correction(0, {3.0, 0, 4.0, 0, 0, 0}, true, false, 1000);
  require(result.correctionUm == std::array<double, 2>{0.0, 0.0}, "first target dt must be zero");

  result = controller.correction(0, {3.0, 0, 4.0, 0, 0, 0}, true, false, 1010);
  require(near(result.dtSec, 0.010), "target dt mismatch");
  require(near(result.correctionUm[0], 2.0), "X deadband/gain mismatch");
  require(near(result.correctionUm[1], 3.0), "Z deadband/gain mismatch");
  controller.commit(0, result.correctionUm, result.correctionUm);

  result = controller.correction(0, {20.0, 0, 20.0, 0, 0, 0}, true, false, 1110);
  require(near(result.dtSec, 0.020), "dt must clamp to 20 ms");
  require(result.correctionUm == std::array<double, 2>{5.0, 5.0}, "single-frame limit mismatch");
  controller.commit(0, result.correctionUm, result.correctionUm);

  result = controller.correction(0, {20.0, 0, 20.0, 0, 0, 0}, true, false, 1130);
  require(result.correctionUm == std::array<double, 2>{1.0, 0.0}, "session offset limit mismatch");

  auto disabled = complianceConfig();
  disabled.sides[0].mappingConfirmed = false;
  controller.configure(disabled);
  require(
      controller.correction(0, {20.0, 0, 20.0, 0, 0, 0}, true, false, 2000).correctionUm
          == std::array<double, 2>{0.0, 0.0},
      "unconfirmed mapping must disable compliance");

  controller.configure(complianceConfig());
  controller.correction(0, {3.0, 0, 3.0, 0, 0, 0}, true, false, 3000);
  require(
      controller.correction(0, {3.0, 0, 3.0, 0, 0, 0}, false, false, 3010).correctionUm
          == std::array<double, 2>{0.0, 0.0},
      "stale sample must disable compliance");
  require(
      controller.correction(0, {3.0, 0, 3.0, 0, 0, 0}, true, true, 3020).correctionUm
          == std::array<double, 2>{0.0, 0.0},
      "safety latch must disable compliance");
  controller.reset();
  require(controller.cumulativeOffset(0) == std::array<double, 2>{0.0, 0.0}, "reset must clear session offset");
}

void testForceRuntime() {
  int emergencyStops = 0;
  int acknowledgements = 0;
  ForceControlRuntime runtime(
      [&emergencyStops]() { ++emergencyStops; },
      [&acknowledgements]() { ++acknowledgements; });

  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  config.serial.protocol = "hkvl_active_v1";
  config.serial.leftPort = "COM15";
  config.serial.rightPort = "COM14";
  config.serial.baudrate = 1000000;
  config.serial.expectedSampleHz = 1000;
  config.safety = safetyConfig();
  config.safety.watchdogMs = 1000.0;
  runtime.configure(config, 0.0);
  require(runtime.safetyLatched(), "HKVL configuration must begin in a safety latch");
  require(emergencyStops == 1, "HKVL configuration must invoke the global emergency stop");

  const std::array<double, 6> unloaded{};
  runtime.acceptSample(0, unloaded, unloaded, 1.0, 1001);
  runtime.acceptSample(1, unloaded, unloaded, 1.0, 1001);
  runtime.acceptSample(0, {4.8, 0, 0, 0, 0, 0}, {4.8, 0, 0, 0, 0, 0}, 2.0, 1002);
  require(emergencyStops == 1, "runtime must invoke global emergency stop once");
  runtime.acceptSample(0, {5.0, 0, 0, 0, 0, 0}, {5.0, 0, 0, 0, 0, 0}, 3.0, 1003);
  require(emergencyStops == 1, "latched runtime must not repeat emergency stop callbacks");

  runtime.acceptSample(0, unloaded, unloaded, 4.0, 1004);
  runtime.acceptSample(1, unloaded, unloaded, 4.0, 1004);
  runtime.acceptSample(0, unloaded, unloaded, 504.0, 1504);
  runtime.acceptSample(1, unloaded, unloaded, 504.0, 1504);
  runtime.acknowledgeEmergencyStop(504.0);
  require(acknowledgements == 1, "acknowledge callback must run exactly once");
  require(!runtime.safetyLatched(), "acknowledge must clear force safety latch");

  runtime.acceptSample(0, {4.8, 0, 0, 0, 0, 0}, {4.8, 0, 0, 0, 0, 0}, 505.0, 1505);
  require(emergencyStops == 2, "force trip after acknowledgement must invoke emergency stop");
  runtime.acceptSample(0, unloaded, unloaded, 506.0, 1506);
  runtime.acceptSample(1, unloaded, unloaded, 506.0, 1506);
  runtime.acceptSample(0, unloaded, unloaded, 1006.0, 2006);
  runtime.acceptSample(1, unloaded, unloaded, 1006.0, 2006);
  runtime.acknowledgeEmergencyStop(1006.0);
  require(acknowledgements == 2, "force trip acknowledgement must run exactly once");

  const auto json = runtime.forceStateJson(1006.0);
  require(json.find("\"source\":\"hkvl_serial\"") != std::string::npos, "force state source missing");
  require(json.find("\"dangerIndex\":0") != std::string::npos, "force state danger index missing");
  require(json.find("\"latched\":false") != std::string::npos, "force state latch missing");

  config.serial.rightPort = "COM15";
  bool rejected = false;
  try {
    runtime.configure(config, 600.0);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "identical serial ports must be rejected");

  config.serial.rightPort = "COM14";
  config.serial.baudrate = 115200;
  rejected = false;
  try {
    runtime.configure(config, 600.0);
  } catch (const std::invalid_argument&) {
    rejected = true;
  }
  require(rejected, "HKVL-36A baudrate must be fixed at 1 Mbps");
}

void testForceRuntimeAlignsAllSixChannelsBeforeStandardConsumption() {
  ForceControlRuntime runtime([]() {}, []() {});
  ForceRuntimeConfig config;
  config.source = "hkvl_serial";
  config.safety = safetyConfig();
  config.safety.watchdogMs = 1000.0;
  config.compliance = complianceConfig();
  runtime.configure(config, 0.0);

  runtime.acceptSample(
      0,
      {1.0, 2.0, 3.0, 0.1, 0.2, 0.3},
      {10.0, 20.0, 30.0, 0.4, 0.5, 0.6},
      1.0,
      1001);
  auto json = runtime.forceStateJson(1.0);
  require(
      json.find("\"left\":[-10,20,-30,0.4,-0.5,-0.6]") != std::string::npos,
      "filtered force state must align all six hardware-left channels");
  require(
      json.find("\"rawLeft\":[-1,2,-3,0.1,-0.2,-0.3]") != std::string::npos,
      "tared unfiltered force state must align all six hardware-left channels");
  require(
      json.find("\"axisSign\":[-1,1,-1,1,-1,-1]") != std::string::npos,
      "force state must publish the applied hardware-left signs");
  require(
      json.find("\"sensorRawLeft\":[0,0,0,0,0,0]") != std::string::npos
          && json.find("\"sensorRawRight\":[0,0,0,0,0,0]") != std::string::npos,
      "force state must keep sensor-native raw diagnostics separate");
  require(
      json.find("\"sensorTareBias\":[0,0,0,0,0,0]") != std::string::npos,
      "force state must identify the sensor-native Tare bias");

  const std::array<double, 6> unloaded{};
  runtime.acceptSample(0, unloaded, unloaded, 2.0, 1002);
  runtime.acceptSample(1, unloaded, unloaded, 2.0, 1002);
  runtime.acceptSample(0, unloaded, unloaded, 502.0, 1502);
  runtime.acceptSample(1, unloaded, unloaded, 502.0, 1502);
  runtime.acknowledgeEmergencyStop(502.0);

  runtime.acceptSample(
      0,
      {3.0, 0.0, 4.0, 0.0, 0.0, 0.0},
      {3.0, 0.0, 4.0, 0.0, 0.0, 0.0},
      503.0,
      1503);
  auto correction = runtime.complianceCorrection(0, 1000);
  require(
      correction.correctionUm == std::array<double, 2>{0.0, 0.0},
      "first aligned compliance target must retain dt=0");
  correction = runtime.complianceCorrection(0, 1010);
  require(
      near(correction.correctionUm[0], -2.0)
          && near(correction.correctionUm[1], -3.0),
      "identity compliance matrix must consume motion-aligned Fx/Fz without another sign");

  runtime.acceptSample(
      0,
      {4.8, 0.0, 0.0, 0.0, 0.0, 0.0},
      {4.8, 0.0, 0.0, 0.0, 0.0, 0.0},
      504.0,
      1504);
  json = runtime.forceStateJson(504.0);
  require(
      json.find("\"value\":-4.8") != std::string::npos,
      "safety trip value must use the motion-aligned sign");
}

void testNidaqRuntimeDoesNotLatchForceSafetyForManualEstop() {
  ForceControlRuntime runtime([]() {}, []() {});
  ForceRuntimeConfig config;
  config.source = "nidaq";
  runtime.configure(config, 0.0);

  runtime.recordExternalEmergencyStop("manual emergency stop", 1.0);

  require(
      !runtime.safetyLatched(),
      "NI-DAQ mode must not report a force safety latch for manual estop");
}

void testMotionAcknowledge() {
  LTDMCDriver motion;
  motion.emergencyStop();
  require(motion.estopActive(), "emergency stop must latch");
  motion.acknowledgeEmergencyStop();
  require(!motion.estopActive(), "acknowledge must clear the latch");
}

void testForceConfigJson() {
  ForceRuntimeConfig fallback;
  const auto config = appstation::hal::jsonForceRuntimeConfig(
      R"({
        "source":"hkvl_serial",
        "protocol":"hkvl_active_v1",
        "leftPort":"COM15",
        "rightPort":"COM14",
        "baudrate":1000000,
        "expectedSampleHz":1000,
        "leftAxisSign":[-1,1,-1,1,-1,-1],
        "rightAxisSign":[-1,-1,-1,1,1,1],
        "fxyWarnN":2,
        "fxyStopN":4,
        "fzWarnN":3,
        "fzStopN":5,
        "momentWarnNm":0.02,
        "momentStopNm":0.04,
        "watchdogMs":50,
        "complianceEnabled":false,
        "leftComplianceMatrix":[1,0,0,1],
        "rightComplianceMatrix":[-1,0,0,1]
      })",
      fallback);
  require(config.source == "hkvl_serial", "force source JSON mismatch");
  require(config.serial.leftPort == "COM15" && config.serial.rightPort == "COM14", "force port JSON mismatch");
  require(
      config.axisSign[0] == std::array<double, 6>{-1.0, 1.0, -1.0, 1.0, -1.0, -1.0}
          && config.axisSign[1] == std::array<double, 6>{-1.0, -1.0, -1.0, 1.0, 1.0, 1.0},
      "force axis sign JSON mismatch");
  require(config.safety.stop[2] == 5.0 && config.safety.stop[5] == 0.04, "force threshold JSON mismatch");
  require(config.compliance.sides[1].matrix[0] == -1.0, "force matrix JSON mismatch");
}

}  // namespace

int main() {
  try {
    testParser();
    testOfficialHkvlSafetyDefaults();
    testOfficialHkvlHardwareSidePorts();
    testOfficialHkvlMotionAlignedAxisSigns();
    testSafetyLatch();
    testCompliance();
    testForceRuntime();
    testForceRuntimeAlignsAllSixChannelsBeforeStandardConsumption();
    testNidaqRuntimeDoesNotLatchForceSafetyForManualEstop();
    testMotionAcknowledge();
    testForceConfigJson();
    std::cout << "ForceCoreTests passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "ForceCoreTests failed: " << error.what() << "\n";
    return 1;
  }
}
