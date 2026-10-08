// 离线夹爪进程替身：不加载 vendor DLL，不打开串口。
#include <windows.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include "NativeTeleopController.h"
#include "OfflineMotion.h"

using namespace appstation::hal;
using namespace std::chrono_literals;
namespace {
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
std::string arg(int argc, char** argv, const std::string& key) {
  for (int i = 1; i + 1 < argc; ++i) if (argv[i] == key) return argv[i + 1];
  return "";
}
std::string key;
std::filesystem::path logPath;
std::string eventName(const char* suffix) { return "Local\\" + key + suffix; }
struct Gate {
  HANDLE entered = CreateEventA(nullptr, TRUE, FALSE, eventName("_entered").c_str());
  HANDLE release = CreateEventA(nullptr, TRUE, FALSE, eventName("_release").c_str());
  ~Gate() { SetEvent(release); CloseHandle(entered); CloseHandle(release); }
};
int fakeWorker(int argc, char** argv) {
  const auto port = arg(argc, argv, "--port");
  key = arg(argc, argv, "--dll");
  const auto log = std::filesystem::temp_directory_path() / (key + ".log");
  std::string line;
  while (std::getline(std::cin, line) && line != "EXIT") {
    if (line == "READ" && port == "BLOCK") {
      HANDLE entered = OpenEventA(EVENT_MODIFY_STATE, FALSE, eventName("_entered").c_str());
      HANDLE release = OpenEventA(SYNCHRONIZE, FALSE, eventName("_release").c_str());
      if (entered) SetEvent(entered);
      if (release) WaitForSingleObject(release, 5000);
      if (entered) CloseHandle(entered);
      if (release) CloseHandle(release);
    }
    if (line.rfind("COMMAND", 0) == 0) {
      { std::ofstream out(log, std::ios::app); out << port << ":" << line << "\n"; }
      if (port == "TIMEOUT") Sleep(1000);
    }
    std::cout << "OK\t12\tpid=" << GetCurrentProcessId() << std::endl;
  }
  return 0;
}
std::string readLog() {
  std::ifstream f(logPath);
  return std::string(std::istreambuf_iterator<char>(f), {});
}
template<class F> void await(F check, const char* message) {
  const auto end = std::chrono::steady_clock::now() + 400ms;
  while (!check() && std::chrono::steady_clock::now() < end) std::this_thread::sleep_for(2ms);
  require(check(), message);
}
void reset(const char* suffix) {
  key = "MicroManiGripperTest_" + std::to_string(GetCurrentProcessId()) + suffix;
  logPath = std::filesystem::temp_directory_path() / (key + ".log");
  std::filesystem::remove(logPath);
}
JodellGripperConfig config(const char* exe) {
  JodellGripperConfig c;
  c.workerExePath = exe;
  c.dllPath = key;
  c.ports = {"FAST", "BLOCK"};
  c.workerCommandTimeoutMs = 1500;
  return c;
}
void testOtherSideResponsive(const char* exe) {
  reset("_isolation");
  Gate gate;
  LTDMCDriver motion;
  MotionExecutor executor(motion);
  Omega7Driver omega;
  JodellGripperDriver driver;
  NativeTeleopController teleop(motion, executor, omega, driver);
  teleop.prepareReplayGripper(config(exe), motion.commandEpoch(), {true, true});
  require(WaitForSingleObject(gate.entered, 2000) == WAIT_OBJECT_0, "right READ not entered");
  require(teleop.commandGripperTarget(Side::Left, 18, 10, 1), "left enqueue rejected");
  await([] { return readLog().find("FAST:COMMAND\t18\t") != std::string::npos; },
      "blocked right READ delayed left command beyond 400ms");
  SetEvent(gate.release);
  teleop.stop();
  std::filesystem::remove(logPath);
  std::cout << "PASS: blocked right READ does not delay left command\n";
}
void testLatestAfterRead(const char* exe, bool stop) {
  reset(stop ? "_stop" : "_latest");
  Gate gate;
  auto c = config(exe);
  c.ports = {"BLOCK", "FAST"};
  LTDMCDriver motion;
  MotionExecutor executor(motion);
  Omega7Driver omega;
  JodellGripperDriver driver;
  NativeTeleopController teleop(motion, executor, omega, driver);
  teleop.prepareReplayGripper(c, motion.commandEpoch(), {true, false});
  require(WaitForSingleObject(gate.entered, 2000) == WAIT_OBJECT_0, "left READ not entered");
  require(teleop.commandGripperTarget(Side::Left, 3, 10, 1), "first enqueue rejected");
  require(teleop.commandGripperTarget(Side::Left, 20, 10, 1), "latest enqueue rejected");
  if (stop) {
    motion.emergencyStop();
    teleop.requestEmergencyStop();
  }
  SetEvent(gate.release);
  if (!stop) {
    await([] { return readLog().find("BLOCK:COMMAND\t20\t") != std::string::npos; },
        "latest target not executed after READ");
  }
  teleop.stop();
  const auto log = readLog();
  require(log.find("COMMAND\t3\t") == std::string::npos, "obsolete queued target executed");
  if (stop) require(log.find("COMMAND") == std::string::npos, "stop allowed pending command");
  std::filesystem::remove(logPath);
  std::cout << (stop ? "PASS: emergency stop rejects pending commands\n" : "PASS: only newest target executes after blocked READ\n");
}
void testTimeoutDiscardsChannel(const char* exe) {
  reset("_timeout");
  JodellGripperDriver driver;
  auto c = config(exe);
  c.ports = {"TIMEOUT", "FAST"};
  c.workerCommandTimeoutMs = 150;
  driver.configure(c);
  std::string oldLeft, right, message, newLeft, sameRight;
  require(driver.readPositionMm(Side::Left, &oldLeft), "left initial READ failed");
  require(driver.readPositionMm(Side::Right, &right), "right initial READ failed");
  require(!driver.commandTarget(Side::Left, 8, 10, 1, &message), "slow command did not timeout");
  require(message.find("timeout") != std::string::npos, "timeout diagnostic missing");
  require(driver.readPositionMm(Side::Left, &newLeft), "left fresh channel READ failed");
  require(oldLeft != newLeft, "timeout reused old worker/channel");
  require(driver.readPositionMm(Side::Right, &sameRight), "right READ failed");
  require(right == sameRight, "left timeout restarted healthy right worker");
  require(readLog().find("COMMAND\t8\t") != std::string::npos, "fault command not exercised");
  std::filesystem::remove(logPath);
  std::cout << "PASS: timeout replaces only failed channel and cannot consume stale reply\n";
}
}
int main(int argc, char** argv) {
  if (argc > 1) return fakeWorker(argc, argv);
  try {
    testOtherSideResponsive(argv[0]);
    testLatestAfterRead(argv[0], false);
    testLatestAfterRead(argv[0], true);
    testTimeoutDiscardsChannel(argv[0]);
    std::cout << "GripperIsolationTests passed\n";
    return 0;
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
