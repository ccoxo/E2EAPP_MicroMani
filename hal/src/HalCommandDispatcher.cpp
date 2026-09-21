/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：统一解析并分派 HAL 命令，调用运动、主手、遥操作和力运行时。
 * 先看：HalCommandDispatcher::handleEmergencyStop → HalCommandDispatcher::handle。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "HalCommandDispatcher.h"

#include "HalJson.h"
#include "CommandDeadline.h"

#include <stdexcept>
#include <cmath>
#include <exception>
#include <limits>

namespace appstation::hal {

HalCommandDispatcher::HalCommandDispatcher(
    LTDMCDriver& motion,
    MotionExecutor& executor,
    Omega7Driver& omega,
    NativeTeleopController& nativeTeleop,
    ForceControlRuntime& forceRuntime,
    const std::chrono::steady_clock::time_point& started)
    : motion_(motion),
      executor_(executor),
      omega_(omega),
      nativeTeleop_(nativeTeleop),
      forceRuntime_(forceRuntime),
      started_(started) {}

std::string HalCommandDispatcher::handleEmergencyStop() {
  emergencyStopsInProgress_.fetch_add(1);
  struct StopCompletion {
    std::atomic_uint32_t& count;
    ~StopCompletion() { count.fetch_sub(1); }
  } completion{emergencyStopsInProgress_};
  // 先撤销软件运动权限；任何停止动作报错也必须继续其余动作。
  motion_.latchEmergencyStop();
  nativeTeleop_.latchControlStop();
  omega_.latchForceStop();
  std::exception_ptr failure;
  const auto attempt = [&failure](auto&& action) {
    try { action(); }
    catch (...) { if (!failure) failure = std::current_exception(); }
  };
  attempt([&]() { motion_.emergencyStop(); });
  attempt([&]() { nativeTeleop_.requestEmergencyStop(); });
  attempt([&]() { omega_.requestEmergencyStop(); });
  attempt([&]() { forceRuntime_.recordExternalEmergencyStop(
      "manual_emergency_stop", forceMonotonicMilliseconds()); });
  if (failure) std::rethrow_exception(failure);
  return "{\"ok\":true}";
}

void HalCommandDispatcher::requireForceMutationSafe(const char* operation) {
  if (nativeTeleop_.running()) {
    throw std::runtime_error(std::string(operation) + " requires native teleop to be stopped");
  }
  for (const auto& axis : motion_.readState().axes) {
    if (axis.moving || axis.enabled) {
      throw std::runtime_error(std::string(operation) + " requires all axes stopped and servos disabled");
    }
  }
}

std::string HalCommandDispatcher::handle(const std::string& name, const std::string& bodyText,
    std::optional<std::uint64_t> expectedEpoch) {
  if (name == "control.lease") {
    const auto sequence = jsonNumberValue(bodyText, "sequence", 0);
    const auto issuedAt = jsonNumberValue(bodyText, "issuedAtUnixMs", 0);
    const auto timeout = jsonNumberValue(bodyText, "timeoutMs", 0);
    const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    if (!std::isfinite(sequence) || sequence < 1 || sequence > 9007199254740991.0
        || std::floor(sequence) != sequence || !std::isfinite(issuedAt)
        || std::abs(static_cast<double>(now) - issuedAt) > 1000
        || timeout != ControlLeaseState::kTimeoutMs)
      throw std::runtime_error("invalid or stale control lease request");
    motion_.renewControlLease(jsonStringValue(bodyText, "sessionId"), static_cast<std::uint64_t>(sequence));
    return "{\"ok\":true,\"leaseFresh\":true,\"timeoutMs\":2500}";
  }
  const auto commandEpoch = expectedEpoch.value_or(motion_.commandEpoch());
  const auto ensureCurrentMotionCommand = [&]() {
    if (!motion_.commandEpochAllowed(commandEpoch)) {
      throw std::runtime_error("HAL command cancelled by emergency stop");
    }
  };
  // DDS command request 使用和 Python backend 相同的 command name；
  // 这里复用既有 HTTP 路由语义，避免两套控制面出现行为分叉。
  if (name == "hal.reconnect") {
    const double uptime =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - started_).count();
    omega_.ensureReady();
    return jsonHealth(motion_.health(uptime), omega_.ok(), omega_.lastError());
  }
  if (name == "omega7.gravity_compensation") {
    omega_.setGravityCompensation(
        jsonBoolValue(bodyText, "leftEnabled", true),
        jsonBoolValue(bodyText, "rightEnabled", true),
        jsonNumberValue(bodyText, "leftScale", 0.45),
        jsonNumberValue(bodyText, "rightScale", 1.0),
        [&]() { return motion_.commandEpochAllowed(commandEpoch); });
    return "{\"ok\":true}";
  }
  if (name == "omega7.zero_force_feedback") {
    omega_.zeroForceFeedback(static_cast<int>(jsonNumberValue(bodyText, "openId", -1)));
    return "{\"ok\":true}";
  }
  if (name == "force.state") {
    return forceRuntime_.forceStateJson(forceMonotonicMilliseconds());
  }
  if (name == "force.configure") {
    requireForceMutationSafe("force configuration");
    forceRuntime_.configure(
        jsonForceRuntimeConfig(bodyText, forceRuntime_.config()),
        forceMonotonicMilliseconds());
    return "{\"ok\":true}";
  }
  if (name == "force.tare") {
    const auto ensureTareDeadline = [&bodyText]() {
      const auto now = std::chrono::duration<double, std::milli>(
          std::chrono::system_clock::now().time_since_epoch()).count();
      ensureCommandNotExpired(bodyText, now);
    };
    ensureTareDeadline();
    if (!jsonBoolValue(bodyText, "unloadedConfirmed", false)) {
      throw std::runtime_error("force tare requires confirmation that both sensors are unloaded");
    }
    requireForceMutationSafe("force tare");
    const auto sideValue = lowercase(jsonStringValueOr(bodyText, "side", "all"));
    if (sideValue != "all" && sideValue != "both") {
      throw std::runtime_error("HKVL startup force self-check requires side=all");
    }
    const auto samples = jsonNumberValue(bodyText, "samples", kHkvlTareMinSamples);
    if (!std::isfinite(samples) || samples < kHkvlTareMinSamples
        || samples > kHkvlTareMaxSamples || std::floor(samples) != samples) {
      throw std::runtime_error("invalid HKVL tare sample count; expected 200..1000");
    }
    if (emergencyStopsInProgress_.load() != 0 || motion_.commandEpoch() != commandEpoch) {
      throw std::runtime_error("HKVL tare cancelled by emergency stop");
    }
    // 此停止路径先撤销权限，再由 emergencyStop 再次锁存，共增加两次代际。
    // 预先计算自身停机的代际，不能在停机后读取新值而吞掉并发急停。
    const auto tareCommandEpoch = (commandEpoch + 4U) | 1U;
    handleEmergencyStop();
    return forceRuntime_.tare(-1, static_cast<int>(samples),
        [this, tareCommandEpoch, &ensureTareDeadline]() {
          ensureTareDeadline();
          return emergencyStopsInProgress_.load() == 0 && motion_.commandEpoch() == tareCommandEpoch;
        });
  }
  if (name == "teleop.native.configure") {
    nativeTeleop_.configure(jsonNativeTeleopConfig(bodyText), commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "teleop.native.start") {
    ensureCurrentMotionCommand();
    forceRuntime_.resetCompliance();
    nativeTeleop_.configure(jsonNativeTeleopConfig(bodyText), commandEpoch);
    omega_.ensureReady();
    ensureCurrentMotionCommand();
    nativeTeleop_.start(
        jsonBoolValue(bodyText, "leftConnected", false),
        jsonBoolValue(bodyText, "rightConnected", false), commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "teleop.native.stop") {
    nativeTeleop_.stop();
    forceRuntime_.resetCompliance();
    return "{\"ok\":true}";
  }
  if (name == "teleop.native.status") {
    return nativeTeleop_.statusJson();
  }
  if (name == "gripper.prepare_replay") {
    ensureCurrentMotionCommand();
    const auto config = jsonNativeTeleopConfig(bodyText);
    nativeTeleop_.prepareReplayGripper(config.gripper, commandEpoch, config.gripperParticipating);
    nativeTeleop_.configureGripperProtection(config.gripperIcfTargetProtectionEnabled, config.gripperIcfTargetMinGapMm);
    return "{\"ok\":true}";
  }
  if (name == "teleop.native.gripper_command" || name == "gripper.command" || name == "gripper.replay_target") {
    ensureCurrentMotionCommand();
    const auto config = jsonNativeTeleopConfig(bodyText);
    if (name == "gripper.replay_target") {
      if (!nativeTeleop_.replayGripperReady()) throw std::runtime_error("replay gripper sampling is not ready");
    } else {
      nativeTeleop_.configureGripper(config.gripper);
      nativeTeleop_.configureGripperProtection(
          config.gripperIcfTargetProtectionEnabled,
          config.gripperIcfTargetMinGapMm);
    }
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto targetMm = jsonNumberValue(bodyText, "targetMm", 0.0);
    const auto effectiveTargetMm = effectiveGripperTargetMm(config, targetMm);
    const auto speed = static_cast<int>(jsonNumberValue(bodyText, "gripSpeed", config.gripper.speed));
    const auto torque = static_cast<int>(jsonNumberValue(bodyText, "gripTorque", config.gripper.torque));
    std::string message;
    ensureCurrentMotionCommand();
    if (!nativeTeleop_.commandGripperTarget(side, targetMm, speed, torque, &message, commandEpoch)) {
      throw std::runtime_error(message);
    }
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\",\"targetMm\":"
        + std::to_string(effectiveTargetMm) + "}";
  }
  if (name == "motion.emergency_stop") {
    return handleEmergencyStop();
  }
  if (name == "motion.acknowledge_estop") {
    if (emergencyStopsInProgress_.load() != 0) {
      throw std::runtime_error("emergency stop is still being applied; acknowledge again after it completes");
    }
    const auto expectedEpoch = commandEpoch;
    forceRuntime_.acknowledgeEmergencyStop(forceMonotonicMilliseconds(), [&]() {
      motion_.acknowledgeEmergencyStop(expectedEpoch);
    });
    return "{\"ok\":true,\"servoRestored\":false}";
  }
  if (name == "motion.home_all") {
    motion_.ensureMotionReturnAllowed();
    const auto enabledAxes = jsonHomeAllEnabledAxes(bodyText);
    nativeTeleop_.stop();
    ensureCurrentMotionCommand();
    executor_.homeAll(jsonWorkOriginPulse(bodyText), enabledAxes, commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "motion.home_origin_side" || name == "motion.return_home_reference") {
    motion_.ensureMotionReturnAllowed();
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const bool referenceReturn = name == "motion.return_home_reference";
    const auto enabledAxes = jsonBoolArray6(bodyText, "enabledAxes", referenceReturn ? std::array<bool, 6>{} : kAllAxesEnabled);
    nativeTeleop_.stop();
    ensureCurrentMotionCommand();
    executor_.homeOriginSide(side, jsonSideWorkOriginPulse(bodyText), enabledAxes, commandEpoch, referenceReturn);
    if (referenceReturn) return "{\"ok\":true,\"referenceReturnCompleted\":true}";
    return "{\"ok\":true}";
  }
  if (name == "motion.enable_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto message = executor_.enableSide(side, true, jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled), commandEpoch);
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
  }
  if (name == "motion.disable_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto message = executor_.enableSide(side, false, kAllAxesEnabled, commandEpoch);
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
  }
  if (name == "motion.home_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    nativeTeleop_.stop();
    ensureCurrentMotionCommand();
    executor_.homeSide(side, jsonBoolArray6(bodyText, "enabledAxes", {}), commandEpoch);
    return "{\"ok\":true,\"homeCompleted\":true}";
  }
  if (name == "motion.manual_axis_move") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto axis = parseAxis(jsonStringValue(bodyText, "axis"));
    const auto direction = jsonNumberValue(bodyText, "direction", 0);
    const auto step = jsonNumberValue(bodyText, "step", 0);
    const auto maxVelocity = jsonNumberValue(bodyText, "maxVelocityUiPerSec", 0);
    const auto startVelocity = jsonNumberValue(bodyText, "startVelocityUiPerSec", 0);
    const auto accTime = jsonNumberValue(bodyText, "accTimeSec", 0);
    const auto decTime = jsonNumberValue(bodyText, "decTimeSec", 0);
    executor_.moveRelativeUi(
        side,
        axis,
        step * (direction >= 0 ? 1.0 : -1.0),
        maxVelocity,
        startVelocity,
        accTime,
        decTime, commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "motion.teleop_target_update" || name == "motion.replay_absolute_target") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const double missing = name == "motion.replay_absolute_target"
        ? std::numeric_limits<double>::quiet_NaN() : 0.0;
    const std::array<double, 6> deltas{
        jsonNumberValue(bodyText, "X", missing),
        jsonNumberValue(bodyText, "Y", missing),
        jsonNumberValue(bodyText, "Z", missing),
        jsonNumberValue(bodyText, "Roll", missing),
        jsonNumberValue(bodyText, "Pitch", missing),
        jsonNumberValue(bodyText, "Yaw", missing)};
    TeleopHardwareTarget target;
    target.side = side == Side::Left ? 0 : 1;
    target.deltas = deltas;
    target.translationStepLimitPulse = jsonNumberValue(bodyText, "translationStepLimitPulse", 0.0);
    target.rotationStepLimitPulse = jsonNumberValue(bodyText, "rotationStepLimitPulse", 0.0);
    target.translationPulseDeadband = jsonNumberValue(bodyText, "translationPulseDeadband", 0.0);
    target.rotationPulseDeadband = jsonNumberValue(bodyText, "rotationPulseDeadband", 0.0);
    target.enabledAxes = jsonTeleopEnabledAxes(bodyText);
    target.syncZeroDeltaTarget = jsonBoolValue(bodyText, "syncZeroDeltaTarget", false);
    const auto limits = jsonTeleopSoftLimits(bodyText);
    for (std::size_t i = 0; i < limits.size(); ++i) {
      target.softLimitMin[i] = limits[i].min;
      target.softLimitMax[i] = limits[i].max;
    }
    target.translationVelocityUiPerSec = jsonNumberValue(bodyText, "translationVelocityUiPerSec", 0.0);
    target.rotationVelocityUiPerSec = jsonNumberValue(bodyText, "rotationVelocityUiPerSec", 0.0);
    target.translationStartVelocityUiPerSec = jsonNumberValue(bodyText, "translationStartVelocityUiPerSec", 0.0);
    target.rotationStartVelocityUiPerSec = jsonNumberValue(bodyText, "rotationStartVelocityUiPerSec", 0.0);
    target.accTimeSec = jsonNumberValue(bodyText, "accTimeSec", 0.0);
    target.decTimeSec = jsonNumberValue(bodyText, "decTimeSec", 0.0);
    const auto result = executor_.applyExternal(target, commandEpoch, name == "motion.replay_absolute_target");
    return jsonTeleopTargetUpdateResult(side, result);
  }
  if (name == "motion.teleop_stop_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    executor_.stopSide(side);
    return "{\"ok\":true}";
  }
  throw std::runtime_error("unknown HAL command: " + name);

}

}  // namespace appstation::hal
