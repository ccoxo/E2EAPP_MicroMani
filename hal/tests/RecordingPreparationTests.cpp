// 离线内存测试；不启动设备、采样线程或 vendor SDK。
#include "OfflineMotion.h"
#include "NativeTeleopController.h"
#include "HalJson.h"
#include <iostream>
#include <stdexcept>
using namespace appstation::hal;

static void require(bool value, const char* message) {
  if (!value) throw std::runtime_error(message);
}
namespace appstation::hal {
struct NativeTeleopTelemetryTestAccess {
  static void run(NativeTeleopController& c) {
    c.config_.recordingPreparing = true;
    c.config_.gripperTeleopEnabled = true;
    c.config_.gripperParticipating = {true, false};
    c.config_.gripperSourceHand = {"PhysicalRight", "PhysicalLeft"};
    c.config_.gripper.strokeMm = 26.0;
    c.config_.gripperGapMaxMm = {25.0, 25.0};
    c.logicalConnected_ = {true, true};
    c.running_.store(true);
    // 只设置队列状态供断言，不运行串口 worker。
    c.gripperWorkerRunning_.store(true);
    c.referenceValid_ = {true, true};
    c.referencePose_[0][0] = -1.;
    c.continuousPulseCarry_[0][0] = 999.;
    std::array<Omega7State, 2> hands{};
    for (auto& hand : hands) {
      hand.connected = hand.lastReadOk = true;
      hand.clutchPressed = true; // 按住 clutch 也不能越过准备期限制。
      hand.gripperGapAvailable = true;
      hand.gripperGap = .025;
      hand.pose[0] = .03;
    }
    for (int i = 0; i < 20; ++i) {
      hands[0].pose[0] += .001;
      hands[1].gripperGap = i % 2 ? .025 : .012;
      c.processLeaderState(hands, .01);
    }
    require(!c.referenceValid_[0] && !c.referenceValid_[1], "preparation retained old hand references");
    require(c.continuousPulseCarry_[0][0] == 0., "preparation accumulated hand movement");
    require(!c.hasLastAction_, "preparation emitted a motion action");
    require(!c.pendingGripperCommands_[0].pending, "preparation enqueued a gripper command");
    require(c.gripperTargetsMm_[0] == 26., "preparation lost gripper input preview");
    require(c.gripperWorkerRunning_.load(), "preparation disabled feedback sampling");
    c.config_.recordingPreparing = false;
    c.processLeaderState(hands, .01);
    require(c.referenceValid_[0], "release did not capture a new hand reference");
    require(c.referencePose_[0][0] == hands[0].pose[0], "release reused preparation movement");
    require(!c.hasLastAction_, "first released sample caused a jump");
    require(c.pendingGripperCommands_[0].pending, "release did not enable gripper mapping");
    c.running_.store(false);
    c.gripperWorkerRunning_.store(false);
    c.pendingGripperCommands_ = {};
  }
};
}
int main() {
  try {
    require(jsonNativeTeleopConfig("{}").recordingPreparing == false, "normal teleop default changed");
    require(jsonNativeTeleopConfig("{\"recordingPreparing\":true}").recordingPreparing, "hold flag was not decoded");
    LTDMCDriver motion;
    MotionExecutorTestAccess::initialize(motion);
    MotionExecutor executor(motion);
    Omega7Driver omega;
    JodellGripperDriver gripper;
    NativeTeleopController controller(motion, executor, omega, gripper);
    NativeTeleopTelemetryTestAccess::run(controller);
    std::cout << "RecordingPreparationTests passed: no output during preparation; feedback remains; release rebases input\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << "\n";
    return 1;
  }
}
