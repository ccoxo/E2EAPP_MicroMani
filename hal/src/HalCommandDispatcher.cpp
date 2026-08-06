#include "HalCommandDispatcher.h"

#include "HalJson.h"

#include <stdexcept>

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
  forceRuntime_.recordExternalEmergencyStop(
      "manual_emergency_stop",
      forceMonotonicMilliseconds());
  motion_.emergencyStop();
  nativeTeleop_.requestEmergencyStop();
  return "{\"ok\":true}";
}

std::string HalCommandDispatcher::handle(const std::string& name, const std::string& bodyText) {
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
        jsonNumberValue(bodyText, "rightScale", 1.0));
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
        static_cast<int>(jsonNumberValue(bodyText, "samples", 200)));
    return "{\"ok\":true}";
  }
  if (name == "teleop.native.configure") {
    nativeTeleop_.configure(jsonNativeTeleopConfig(bodyText));
    return "{\"ok\":true}";
  }
  if (name == "teleop.native.start") {
    forceRuntime_.resetCompliance();
    nativeTeleop_.configure(jsonNativeTeleopConfig(bodyText));
    omega_.ensureReady();
    nativeTeleop_.start(
        jsonBoolValue(bodyText, "leftConnected", false),
        jsonBoolValue(bodyText, "rightConnected", false));
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
    if (!nativeTeleop_.commandGripperTarget(side, targetMm, speed, torque, &message)) {
      throw std::runtime_error(message);
    }
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\",\"targetMm\":"
        + std::to_string(effectiveTargetMm) + "}";
  }
  if (name == "motion.emergency_stop") {
    return handleEmergencyStop();
  }
  if (name == "motion.acknowledge_estop") {
    forceRuntime_.acknowledgeEmergencyStop(forceMonotonicMilliseconds());
    return "{\"ok\":true,\"servoRestored\":false}";
  }
  if (name == "motion.home_all") {
    motion_.ensureMotionReturnAllowed();
    const auto enabledAxes = jsonHomeAllEnabledAxes(bodyText);
    nativeTeleop_.stop();
    motion_.homeAll(jsonWorkOriginPulse(bodyText), enabledAxes);
    return "{\"ok\":true}";
  }
  if (name == "motion.home_origin_side") {
    motion_.ensureMotionReturnAllowed();
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto enabledAxes = jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled);
    nativeTeleop_.stop();
    motion_.homeOriginSide(side, jsonSideWorkOriginPulse(bodyText), enabledAxes);
    return "{\"ok\":true}";
  }
  if (name == "motion.enable_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto message = motion_.enableSide(side, true, jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled));
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
  }
  if (name == "motion.disable_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    const auto message = motion_.enableSide(side, false);
    return "{\"ok\":true,\"message\":\"" + jsonEscape(message) + "\"}";
  }
  if (name == "motion.home_side") {
    const auto side = parseSide(jsonStringValue(bodyText, "side"));
    motion_.homeSide(side, jsonBoolArray6(bodyText, "enabledAxes", kAllAxesEnabled));
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
        decTime);
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
        jsonNumberValue(bodyText, "decTimeSec", 0.0));
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
