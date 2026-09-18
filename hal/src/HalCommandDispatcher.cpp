/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：统一解析并分派 HAL 命令，调用运动、主手、遥操作和力运行时。
 * 先看：HalCommandDispatcher::handleEmergencyStop → HalCommandDispatcher::handle。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "HalCommandDispatcher.h"

#include "HalJson.h"

#include <stdexcept>
#include <cmath>
#include <exception>

namespace appstation::hal {

HalCommandDispatcher::HalCommandDispatcher(
    LTDMCDriver& motion,
    Omega7Driver& omega,
    NativeTeleopController& nativeTeleop,
    ForceControlRuntime& forceRuntime,
    const std::chrono::steady_clock::time_point& started)
    : motion_(motion),
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
    if (nativeTeleop_.running()) {
      throw std::runtime_error("force configuration requires native teleop to be stopped");
    }
    const auto motionState = motion_.readState();
    for (const auto& axis : motionState.axes) {
      if (axis.moving || axis.enabled) {
        throw std::runtime_error(
            "force configuration requires all axes stopped and servos disabled");
      }
    }
    forceRuntime_.configure(
        jsonForceRuntimeConfig(bodyText, forceRuntime_.config()),
        forceMonotonicMilliseconds());
    return "{\"ok\":true}";
  }
  if (name == "force.tare") {
    ensureCurrentMotionCommand();
    const auto sideValue = lowercase(jsonStringValueOr(bodyText, "side", "all"));
    int side = -1;
    if (sideValue == "left") {
      side = 0;
    } else if (sideValue == "right") {
      side = 1;
    } else if (sideValue != "all" && sideValue != "both") {
      throw std::runtime_error("force.tare side must be left, right, or all");
    }
    forceRuntime_.tare(
        side,
        static_cast<int>(jsonNumberValue(bodyText, "samples", 200)),
        [motion = &motion_, commandEpoch]() { return motion->commandEpochAllowed(commandEpoch); });
    return "{\"ok\":true}";
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
  if (name == "teleop.native.gripper_command" || name == "gripper.command") {
    ensureCurrentMotionCommand();
    const auto config = jsonNativeTeleopConfig(bodyText);
    nativeTeleop_.configureGripper(config.gripper);
    nativeTeleop_.configureGripperProtection(
        config.gripperIcfTargetProtectionEnabled,
        config.gripperIcfTargetMinGapMm);
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
    motion_.homeAll(jsonWorkOriginPulse(bodyText), enabledAxes, commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "motion.home_origin_side") {
    motion_.ensureMotionReturnAllowed();
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto enabledAxes = jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled);
    nativeTeleop_.stop();
    ensureCurrentMotionCommand();
    motion_.homeOriginSide(side, jsonSideWorkOriginPulse(bodyText), enabledAxes, commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "motion.enable_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto message = motion_.enableSide(side, true, jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled), commandEpoch);
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
  }
  if (name == "motion.disable_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto message = motion_.enableSide(side, false);
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
  }
  if (name == "motion.home_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    motion_.homeSide(side, jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled), commandEpoch);
    return "{\"ok\":true}";
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
    motion_.moveRelativeUi(
        side,
        axis,
        step * (direction >= 0 ? 1.0 : -1.0),
        maxVelocity,
        startVelocity,
        accTime,
        decTime, commandEpoch);
    return "{\"ok\":true}";
  }
  if (name == "motion.teleop_target_update") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const std::array<double, 6> deltas{
        jsonNumberValue(bodyText, "X", 0.0),
        jsonNumberValue(bodyText, "Y", 0.0),
        jsonNumberValue(bodyText, "Z", 0.0),
        jsonNumberValue(bodyText, "Roll", 0.0),
        jsonNumberValue(bodyText, "Pitch", 0.0),
        jsonNumberValue(bodyText, "Yaw", 0.0)};
    const auto result = motion_.updateTeleopTargetUi(
        side,
        deltas,
        jsonNumberValue(bodyText, "translationStepLimitPulse", 0.0),
        jsonNumberValue(bodyText, "rotationStepLimitPulse", 0.0),
        jsonNumberValue(bodyText, "translationPulseDeadband", 0.0),
        jsonNumberValue(bodyText, "rotationPulseDeadband", 0.0),
        jsonTeleopEnabledAxes(bodyText),
        jsonBoolValue(bodyText, "syncZeroDeltaTarget", false),
        jsonTeleopSoftLimits(bodyText),
        jsonNumberValue(bodyText, "translationVelocityUiPerSec", 0.0),
        jsonNumberValue(bodyText, "rotationVelocityUiPerSec", 0.0),
        jsonNumberValue(bodyText, "translationStartVelocityUiPerSec", 0.0),
        jsonNumberValue(bodyText, "rotationStartVelocityUiPerSec", 0.0),
        jsonNumberValue(bodyText, "accTimeSec", 0.0),
        jsonNumberValue(bodyText, "decTimeSec", 0.0), commandEpoch);
    return jsonTeleopTargetUpdateResult(side, result);
  }
  if (name == "motion.teleop_stop_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    motion_.stopTeleopSide(side);
    return "{\"ok\":true}";
  }
  throw std::runtime_error("unknown HAL command: " + name);

}

}  // namespace appstation::hal
