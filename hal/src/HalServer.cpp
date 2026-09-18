/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：HAL 进程 main 入口；装配驱动、力安全、DDS 控制面与遥操作数据链路并管理关闭顺序。
 * 先看：main。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>

#include "HalCommandDispatcher.h"
#include "ControlLeaseGuardian.h"
#include "HalDdsControlServer.h"
#include "HalHttpServer.h"
#include "HalJson.h"
#include "ForceControlRuntime.h"
#include "JodellGripperDriver.h"
#include "LTDMCDriver.h"
#include "MotionControlThread.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"
#include "TeleopFollowerTargetSubscriber.h"
#include "TeleopHardwareTargetExecutor.h"
#include "TeleopLeaderPublisher.h"
#include "TeleopMappingNode.h"

int runHalServer() {
  using namespace appstation::hal;

  const auto started = std::chrono::steady_clock::now();

  LTDMCDriver motion;
  // 服务启动即撤销运动权限；有效租约和操作者确认后才能重新使能。
  motion.requireControlLease();
  Omega7Driver omega;
  JodellGripperDriver gripper;

  const bool motionOk = motion.initialize();
  const int halPort = envIntValue("APPSTATION_HAL_PORT", 8091);
  const int leftOpenId = envIntValue("APPSTATION_OMEGA7_LEFT_OPEN_ID", 0);
  const int rightOpenId = envIntValue("APPSTATION_OMEGA7_RIGHT_OPEN_ID", 1);
  const bool swapHands = envBoolValue("APPSTATION_OMEGA7_SWAP_HANDS", false);
  omega.initialize(leftOpenId, rightOpenId, swapHands);

  NativeTeleopController nativeTeleop(motion, omega, gripper);
  ForceControlRuntime forceRuntime(
      [&motion, &nativeTeleop, &omega]() {
        motion.latchEmergencyStop();
        nativeTeleop.latchControlStop();
        omega.latchForceStop();
        // 每个停止动作都要尝试；最后由 ForceRuntime 记录失败并阻止确认恢复。
        std::exception_ptr failure;
        const auto attemptStop = [&failure](auto&& action) {
          try { action(); }
          catch (...) { if (!failure) failure = std::current_exception(); }
        };
        attemptStop([&]() { motion.emergencyStop(); });
        attemptStop([&]() { nativeTeleop.requestEmergencyStop(); });
        attemptStop([&]() { omega.requestEmergencyStop(); });
        if (failure) std::rethrow_exception(failure);
      },
      [&motion]() {
        motion.acknowledgeEmergencyStop();
      });
  ForceRuntimeConfig forceConfig;
  const char* rawForceConfig = std::getenv("APPSTATION_FORCE_CONFIG_JSON");
  if (rawForceConfig && *rawForceConfig) {
    forceConfig = jsonForceRuntimeConfig(rawForceConfig, forceConfig);
  }
  forceRuntime.configure(forceConfig, forceMonotonicMilliseconds());
  forceRuntime.start();

  MotionControlThread motionThread(motion, [&motion, &nativeTeleop](const char* error) {
    motion.failControlLease();
    nativeTeleop.reportControlFailure(error);
  });
  if (motionOk) {
    // 运动卡初始化成功后才启动后台刷新线程，避免无硬件环境反复访问 vendor SDK。
    motionThread.start(1000);
  }

  HalCommandDispatcher commandDispatcher(
      motion,
      omega,
      nativeTeleop,
      forceRuntime,
      started);
  ControlLeaseGuardian leaseGuardian(motion, [&commandDispatcher]() {
    commandDispatcher.handleEmergencyStop();
  });
  leaseGuardian.start();
  HalDdsControlServer ddsControl(
      commandDispatcher,
      motion,
      omega,
      nativeTeleop,
      forceRuntime,
      started);
  TeleopLeaderPublisher leaderPublisher;
  TeleopMappingNode teleopMapping(nativeTeleop);
  TeleopHardwareTargetExecutor teleopExecutor(motion, forceRuntime, [&motion, &nativeTeleop](const char* error) {
    motion.failControlLease();
    nativeTeleop.reportControlFailure(error);
  });
  TeleopFollowerTargetSubscriber followerSubscriber(teleopExecutor);

  // 最后装配、最先清理：发布器销毁前必须停止仍持有其回调的 Native 线程。
  struct RuntimeShutdown {
    LTDMCDriver& motion;
    Omega7Driver& omega;
    NativeTeleopController& native;
    HalDdsControlServer& dds;
    TeleopMappingNode& mapping;
    TeleopFollowerTargetSubscriber& follower;
    ForceControlRuntime& force;
    MotionControlThread& motionThread;
    ControlLeaseGuardian& guardian;
    ~RuntimeShutdown() {
      motion.failControlLease();
      native.latchControlStop();
      omega.latchForceStop();
      const auto attempt = [](auto&& action) {
        try { action(); }
        catch (...) { std::fputs("HAL resource shutdown failed; motion permission remains revoked\n", stderr); }
      };
      attempt([&] { guardian.stop(); });
      attempt([&] { dds.stop(); });
      attempt([&] { native.stop(); });
      attempt([&] { mapping.stop(); });
      attempt([&] { follower.stop(); });
      attempt([&] { native.setLeaderStatePublisher({}); });
      attempt([&] { native.setHardwareTargetPublisher({}); });
      attempt([&] { force.stop(); });
      attempt([&] { motionThread.stop(); });
    }
  } shutdown{motion, omega, nativeTeleop, ddsControl, teleopMapping,
      followerSubscriber, forceRuntime, motionThread, leaseGuardian};

  const char* rawTeleopExecutor = std::getenv("APPSTATION_TELEOP_EXECUTOR");
  const std::string teleopExecutorMode =
      rawTeleopExecutor && *rawTeleopExecutor ? lowercase(rawTeleopExecutor) : "dds_follower";
  // 仅当模式选择和三个 DDS 组件均可用时才接入发布/订阅链路，否则保留进程内执行路径。
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

  // 对象与回调全部装配完成后才接受命令，避免半启动时访问尚未存活的依赖。
  ddsControl.start();

  const int result = runHalHttpServer(
      halPort,
      commandDispatcher);

  // 正常返回和异常展开均由 shutdown 按同一顺序清理。
  return result;
}

int main() {
  try { return runHalServer(); }
  catch (const std::exception& error) {
    std::fprintf(stderr, "HAL startup/runtime failed: %s\n", error.what());
  } catch (...) {
    std::fputs("HAL startup/runtime failed: unknown C++ exception\n", stderr);
  }
  return 1;
}
