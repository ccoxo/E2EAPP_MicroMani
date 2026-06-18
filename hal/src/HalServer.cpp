#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>

#include "HalCommandDispatcher.h"
#include "HalDdsControlServer.h"
#include "HalHttpServer.h"
#include "HalJson.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "MotionControlThread.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"
#include "TeleopFollowerTargetSubscriber.h"
#include "TeleopHardwareTargetExecutor.h"
#include "TeleopLeaderPublisher.h"
#include "TeleopMappingNode.h"

int main() {
  using namespace appstation::hal;

  const auto started = std::chrono::steady_clock::now();

  LTDMCDriver motion;
  Omega7Driver omega;
  JodellGripperDriver gripper;

  const bool motionOk = motion.initialize();
  const int halPort = envIntValue("APPSTATION_HAL_PORT", 8091);
  const int leftOpenId = envIntValue("APPSTATION_OMEGA7_LEFT_OPEN_ID", 0);
  const int rightOpenId = envIntValue("APPSTATION_OMEGA7_RIGHT_OPEN_ID", 1);
  const bool swapHands = envBoolValue("APPSTATION_OMEGA7_SWAP_HANDS", false);
  omega.initialize(leftOpenId, rightOpenId, swapHands);

  NativeTeleopController nativeTeleop(motion, omega, gripper);
  MotionControlThread motionThread(motion);
  if (motionOk) {
    // 运动卡初始化成功后才启动后台刷新线程，避免无硬件环境反复访问 vendor SDK。
    motionThread.start(1000);
  }

  HalCommandDispatcher commandDispatcher(motion, omega, nativeTeleop, started);
  HalDdsControlServer ddsControl(commandDispatcher, motion, omega, nativeTeleop, started);
  ddsControl.start();

  TeleopLeaderPublisher leaderPublisher;
  TeleopMappingNode teleopMapping(nativeTeleop);
  TeleopHardwareTargetExecutor teleopExecutor(motion);
  TeleopFollowerTargetSubscriber followerSubscriber(teleopExecutor);

  const char* rawTeleopExecutor = std::getenv("APPSTATION_TELEOP_EXECUTOR");
  const std::string teleopExecutorMode =
      rawTeleopExecutor && *rawTeleopExecutor ? lowercase(rawTeleopExecutor) : "dds_follower";
  const bool useDdsTeleop =
      teleopExecutorMode == "dds_follower"
      && leaderPublisher.enabled()
      && teleopMapping.enabled()
      && followerSubscriber.enabled();
  if (useDdsTeleop) {
    // DDS follower 模式把主手状态、映射计算、硬件目标执行拆成三个边界，便于分布式部署。
    nativeTeleop.setLeaderStatePublisher([&leaderPublisher](const std::array<Omega7State, 2>& hands) {
      leaderPublisher.publishJson(jsonOmegaState(hands));
    });
    nativeTeleop.setHardwareTargetPublisher([&teleopMapping](const TeleopHardwareTarget& target) {
      teleopMapping.publishHardwareTarget(target);
    });
    teleopMapping.start();
    followerSubscriber.start();
  }

  const int result = runHalHttpServer(
      halPort,
      commandDispatcher);

  // 当前 HTTP server 是阻塞入口；只有退出时才按依赖顺序停止后台线程和 DDS listener。
  nativeTeleop.stop();
  ddsControl.stop();
  teleopMapping.stop();
  followerSubscriber.stop();
  motionThread.stop();
  return result;
}
