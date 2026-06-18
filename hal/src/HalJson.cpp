#include "HalJson.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <sstream>
#include <stdexcept>

namespace appstation::hal {

const std::array<bool, 6> kAllAxesEnabled{true, true, true, true, true, true};

long long unixTimeMs() {
  // HTTP 响应里的 timestamp 使用墙钟，便于和后端日志/浏览器时间对齐。
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
}

long long timestampOrNow(long long value) {
  // 驱动快照可能尚未带时间戳，兜底用当前时间避免前端看到 0。
  return value > 0 ? value : unixTimeMs();
}

std::string jsonEscape(const std::string& value) {
  // HAL 直接拼接小型 JSON，因此所有外部字符串都先做转义。
  std::ostringstream out;
  for (char ch : value) {
    switch (ch) {
      case '"':
        out << "\\\"";
        break;
      case '\\':
        out << "\\\\";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        out << ch;
        break;
    }
  }
  return out.str();
}

std::string jsonHealth(const appstation::hal::HalHealth& motionHealth, bool omegaOk, const std::string& message) {
  // /health 聚合 LTDMC 与 Omega.7，两者状态独立，message 当前承载 Omega 或 motion 诊断。
  std::ostringstream out;
  out << "{\"ltdmc_ok\":" << (motionHealth.ltdmcOk ? "true" : "false")
      << ",\"omega7_ok\":" << (omegaOk ? "true" : "false")
      << ",\"version\":\"" << motionHealth.version << "\""
      << ",\"uptime_s\":" << motionHealth.uptimeS
      << ",\"message\":\"" << jsonEscape(message) << "\"}";
  return out.str();
}

std::string jsonMotionState(const appstation::hal::MotionState& state) {
  // positions/pulses/enabled/moving 都按 MotionState::axes 的 12 轴顺序返回。
  std::ostringstream out;
  out << "{\"timestamp_ms\":" << timestampOrNow(state.readTimestampMs)
      << ",\"estop_active\":" << (state.estopActive ? "true" : "false") << ",\"positions\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << state.axes[i].uiPosition;
  }
  out << "],\"pulses\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << state.axes[i].pulse;
  }
  out << "],\"enabled\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << (state.axes[i].enabled ? "true" : "false");
  }
  out << "],\"moving\":[";
  for (size_t i = 0; i < state.axes.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << (state.axes[i].moving ? "true" : "false");
  }
  out << "]}";
  return out.str();
}

void appendDoubleArray(std::ostringstream& out, const std::array<double, 6>& values) {
  // 小型 JSON 拼接 helper，所有 6 轴数组都使用相同顺序。
  out << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << values[i];
  }
  out << "]";
}

void appendBoolArray(std::ostringstream& out, const std::array<bool, 6>& values) {
  out << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i > 0) {
      out << ",";
    }
    out << (values[i] ? "true" : "false");
  }
  out << "]";
}

std::string jsonTeleopTargetUpdateResult(
    appstation::hal::Side side,
    const appstation::hal::TeleopTargetUpdateResult& result) {
  // 返回完整 teleop 诊断而不只返回 ok，便于前端记录本帧是否被限位、死区或重发影响。
  std::ostringstream out;
  out << "{\"ok\":true,\"side\":\"" << (side == appstation::hal::Side::Left ? "left" : "right") << "\"";
  out << ",\"requestedDeltas\":";
  appendDoubleArray(out, result.requestedDeltaUi);
  out << ",\"appliedDeltas\":";
  appendDoubleArray(out, result.appliedDeltaUi);
  out << ",\"targetUi\":";
  appendDoubleArray(out, result.targetUi);
  out << ",\"requestedDeltaPulse\":";
  appendDoubleArray(out, result.requestedDeltaPulse);
  out << ",\"appliedDeltaPulse\":";
  appendDoubleArray(out, result.appliedDeltaPulse);
  out << ",\"targetPulse\":";
  appendDoubleArray(out, result.targetPulse);
  out << ",\"currentPulse\":";
  appendDoubleArray(out, result.currentPulse);
  out << ",\"launchDeltaPulse\":";
  appendDoubleArray(out, result.launchDeltaPulse);
  out << ",\"updateReturn\":";
  appendDoubleArray(out, result.updateReturn);
  out << ",\"stopReason\":";
  appendDoubleArray(out, result.stopReason);
  out << ",\"axisIoStatus\":";
  appendDoubleArray(out, result.axisIoStatus);
  out << ",\"movingBefore\":";
  appendBoolArray(out, result.movingBefore);
  out << ",\"moveStarted\":";
  appendBoolArray(out, result.moveStarted);
  out << ",\"clipped\":";
  appendBoolArray(out, result.clipped);
  out << "}";
  return out.str();
}

std::string jsonOmegaState(const std::array<appstation::hal::Omega7State, 2>& state) {
  // Omega 状态按逻辑左右手输出，不暴露物理接线顺序给前端。
  std::ostringstream out;
  out << "{\"timestamp_ms\":" << timestampOrNow(state[0].readTimestampMs) << ",\"hands\":[";
  for (size_t i = 0; i < state.size(); ++i) {
    const auto& hand = state[i];
    if (i > 0) {
      out << ",";
    }
    out << "{\"side\":\"" << (i == 0 ? "left" : "right") << "\""
        << ",\"connected\":" << (hand.connected ? "true" : "false")
        << ",\"calibrated\":" << (hand.calibrated ? "true" : "false")
        << ",\"openId\":" << hand.openId
        << ",\"deviceId\":" << hand.deviceId
        << ",\"serial\":\"" << jsonEscape(hand.serial) << "\""
        << ",\"systemName\":\"" << jsonEscape(hand.systemName) << "\"";
    if (hand.handednessKnown) {
      out << ",\"leftHanded\":" << (hand.leftHanded ? "true" : "false");
    } else {
      out << ",\"leftHanded\":null";
    }
    out << ",\"pose\":[";
    for (size_t axis = 0; axis < hand.pose.size(); ++axis) {
      if (axis > 0) {
        out << ",";
      }
      out << hand.pose[axis];
    }
    out << "]"
        << ",\"clutchPressed\":" << (hand.clutchPressed ? "true" : "false")
        << ",\"gripperPressed\":" << (hand.gripperPressed ? "true" : "false");
    if (hand.gripperGapAvailable) {
      out << ",\"gripperGapMm\":" << hand.gripperGap * 1000.0;
    } else {
      out << ",\"gripperGapMm\":null";
    }
    out << ",\"lastReadOk\":" << (hand.lastReadOk ? "true" : "false")
        << ",\"message\":\"" << jsonEscape(hand.lastReadError) << "\"}";
  }
  out << "]}";
  return out.str();
}

std::string jsonObjectFromArray(const std::string& body, const std::string& key, size_t index) {
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return {};
  }
  pos = body.find('[', pos + marker.size());
  if (pos == std::string::npos) {
    return {};
  }
  size_t current = 0;
  for (++pos; pos < body.size(); ++pos) {
    if (body[pos] != '{') {
      continue;
    }
    const auto start = pos;
    int depth = 0;
    for (; pos < body.size(); ++pos) {
      if (body[pos] == '{') {
        ++depth;
      } else if (body[pos] == '}') {
        --depth;
        if (depth == 0) {
          if (current == index) {
            return body.substr(start, pos - start + 1);
          }
          ++current;
          break;
        }
      }
    }
  }
  return {};
}

std::array<Omega7State, 2> jsonOmegaStateValue(const std::string& body) {
  std::array<Omega7State, 2> state{};
  const auto timestamp = static_cast<std::int64_t>(jsonNumberValue(body, "timestamp_ms", 0.0));
  for (size_t i = 0; i < state.size(); ++i) {
    const auto handJson = jsonObjectFromArray(body, "hands", i);
    if (handJson.empty()) {
      continue;
    }
    auto& hand = state[i];
    hand.connected = jsonBoolValue(handJson, "connected", false);
    hand.calibrated = jsonBoolValue(handJson, "calibrated", false);
    hand.openId = static_cast<int>(jsonNumberValue(handJson, "openId", 0.0));
    hand.deviceId = static_cast<int>(jsonNumberValue(handJson, "deviceId", -1.0));
    hand.serial = jsonStringValue(handJson, "serial");
    hand.systemName = jsonStringValue(handJson, "systemName");
    hand.handednessKnown = handJson.find("\"leftHanded\":null") == std::string::npos;
    hand.leftHanded = jsonBoolValue(handJson, "leftHanded", false);
    for (size_t axis = 0; axis < hand.pose.size(); ++axis) {
      (void)jsonNumberArrayValue(handJson, "pose", axis, &hand.pose[axis]);
    }
    hand.clutchPressed = jsonBoolValue(handJson, "clutchPressed", false);
    hand.gripperPressed = jsonBoolValue(handJson, "gripperPressed", false);
    const auto gapMm = jsonNumberValue(handJson, "gripperGapMm", -1.0);
    hand.gripperGapAvailable = gapMm >= 0.0;
    hand.gripperGap = hand.gripperGapAvailable ? gapMm / 1000.0 : 0.0;
    hand.lastReadOk = jsonBoolValue(handJson, "lastReadOk", false);
    hand.lastReadError = jsonStringValue(handJson, "message");
    hand.readTimestampMs = timestamp;
  }
  return state;
}

std::string requestBody(const std::string& request) {
  // 该服务器只处理简单 HTTP 请求，body 从 header 分隔符之后截取。
  const auto marker = request.find("\r\n\r\n");
  if (marker == std::string::npos) {
    return {};
  }
  return request.substr(marker + 4);
}

std::string lowercase(std::string value) {
  for (auto& ch : value) {
    ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  }
  return value;
}

std::string jsonStringValue(const std::string& body, const std::string& key) {
  // 这里不是通用 JSON 解析器，只服务受控的后端 payload；字符串不处理复杂转义。
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return {};
  }
  pos = body.find(':', pos + marker.size());
  if (pos == std::string::npos) {
    return {};
  }
  pos = body.find('"', pos);
  if (pos == std::string::npos) {
    return {};
  }
  const auto end = body.find('"', pos + 1);
  if (end == std::string::npos) {
    return {};
  }
  return body.substr(pos + 1, end - pos - 1);
}

double jsonNumberValue(const std::string& body, const std::string& key, double fallback) {
  // 缺字段或解析失败时使用 fallback，让新旧后端字段可以渐进兼容。
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return fallback;
  }
  pos = body.find(':', pos + marker.size());
  if (pos == std::string::npos) {
    return fallback;
  }
  const char* start = body.c_str() + pos + 1;
  char* end = nullptr;
  const double value = std::strtod(start, &end);
  return end == start ? fallback : value;
}

bool jsonBoolValue(const std::string& body, const std::string& key, bool fallback) {
  // 只接受 JSON true/false；没有字段时保留调用方默认值。
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return fallback;
  }
  pos = body.find(':', pos + marker.size());
  if (pos == std::string::npos) {
    return fallback;
  }
  ++pos;
  while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
    ++pos;
  }
  if (body.compare(pos, 4, "true") == 0) {
    return true;
  }
  if (body.compare(pos, 5, "false") == 0) {
    return false;
  }
  return fallback;
}

bool jsonNumberArrayValue(const std::string& body, const std::string& key, size_t index, double* out) {
  // 按索引读取数组元素，适合固定长度 6/12 轴 payload，不支持嵌套结构通用解析。
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return false;
  }
  pos = body.find('[', pos + marker.size());
  if (pos == std::string::npos) {
    return false;
  }
  ++pos;
  for (size_t current = 0; current <= index; ++current) {
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    const char* start = body.c_str() + pos;
    char* end = nullptr;
    const double value = std::strtod(start, &end);
    if (end == start) {
      return false;
    }
    if (current == index) {
      *out = value;
      return true;
    }
    pos = static_cast<size_t>(end - body.c_str());
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    if (pos >= body.size() || body[pos] != ',') {
      return false;
    }
    ++pos;
  }
  return false;
}

bool jsonBoolArrayValue(const std::string& body, const std::string& key, size_t index, bool* out) {
  // 与 jsonNumberArrayValue 对称，用于 enabledAxes 和左右轴掩码。
  const auto marker = std::string("\"") + key + "\"";
  auto pos = body.find(marker);
  if (pos == std::string::npos) {
    return false;
  }
  pos = body.find('[', pos + marker.size());
  if (pos == std::string::npos) {
    return false;
  }
  ++pos;
  for (size_t current = 0; current <= index; ++current) {
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    bool value = true;
    size_t valueLength = 0;
    if (body.compare(pos, 4, "true") == 0) {
      value = true;
      valueLength = 4;
    } else if (body.compare(pos, 5, "false") == 0) {
      value = false;
      valueLength = 5;
    } else {
      return false;
    }
    if (current == index) {
      *out = value;
      return true;
    }
    pos += valueLength;
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos]))) {
      ++pos;
    }
    if (pos >= body.size() || body[pos] != ',') {
      return false;
    }
    ++pos;
  }
  return false;
}

std::array<double, 12> jsonWorkOriginPulse(const std::string& body) {
  // home_all payload 分左右各 6 轴，最终合并为 MotionState 的 12 轴顺序。
  std::array<double, 12> pulses{};
  for (size_t i = 0; i < 6; ++i) {
    if (!jsonNumberArrayValue(body, "leftPulse", i, &pulses[i])) {
      throw std::runtime_error("home_all requires leftPulse[6] work origin payload");
    }
    if (!jsonNumberArrayValue(body, "rightPulse", i, &pulses[i + 6])) {
      throw std::runtime_error("home_all requires rightPulse[6] work origin payload");
    }
  }
  return pulses;
}

std::array<double, 6> jsonSideWorkOriginPulse(const std::string& body) {
  // 单侧回工作原点只需要该侧 6 个目标脉冲。
  std::array<double, 6> pulses{};
  for (size_t i = 0; i < pulses.size(); ++i) {
    if (!jsonNumberArrayValue(body, "pulse", i, &pulses[i])) {
      throw std::runtime_error("home_origin_side requires pulse[6] work origin payload");
    }
  }
  return pulses;
}

std::array<appstation::hal::AxisLimit, 6> jsonTeleopSoftLimits(const std::string& body) {
  // teleop 目标更新必须带软限位，HAL 在每帧进行裁剪，避免只依赖前端保护。
  std::array<appstation::hal::AxisLimit, 6> limits{};
  for (size_t i = 0; i < limits.size(); ++i) {
    if (!jsonNumberArrayValue(body, "softLimitMin", i, &limits[i].min)
        || !jsonNumberArrayValue(body, "softLimitMax", i, &limits[i].max)) {
      throw std::runtime_error("teleop_target_update requires softLimitMin[6] and softLimitMax[6]");
    }
    if (limits[i].min >= limits[i].max) {
      throw std::runtime_error("teleop_target_update soft limit min must be less than max");
    }
  }
  return limits;
}

std::array<bool, 6> jsonTeleopEnabledAxes(const std::string& body) {
  // enabledAxes 缺省为全开；存在字段时逐轴覆盖。
  std::array<bool, 6> enabled{true, true, true, true, true, true};
  for (size_t i = 0; i < enabled.size(); ++i) {
    bool value = true;
    if (jsonBoolArrayValue(body, "enabledAxes", i, &value)) {
      enabled[i] = value;
    }
  }
  return enabled;
}

std::array<double, 6> jsonNumberArray6(
    const std::string& body,
    const std::string& key,
    const std::array<double, 6>& fallback) {
  // 配置数组允许部分字段缺失，缺失项沿用 fallback，方便后端只更新某个配置组。
  auto values = fallback;
  for (size_t i = 0; i < values.size(); ++i) {
    double value = values[i];
    if (jsonNumberArrayValue(body, key, i, &value)) {
      values[i] = value;
    }
  }
  return values;
}

std::array<bool, 6> jsonBoolArray6(
    const std::string& body,
    const std::string& key,
    const std::array<bool, 6>& fallback) {
  auto values = fallback;
  for (size_t i = 0; i < values.size(); ++i) {
    bool value = values[i];
    if (jsonBoolArrayValue(body, key, i, &value)) {
      values[i] = value;
    }
  }
  return values;
}

std::array<std::array<bool, 6>, 2> jsonHomeAllEnabledAxes(const std::string& body) {
  // home_all 可分别控制左右参与回原点的轴；缺省两侧全轴参与。
  return {{
      jsonBoolArray6(body, "leftEnabledAxes", kAllAxesEnabled),
      jsonBoolArray6(body, "rightEnabledAxes", kAllAxesEnabled),
  }};
}

std::array<appstation::hal::AxisLimit, 6> jsonAxisLimits(
    const std::string& body,
    const std::string& minKey,
    const std::string& maxKey,
    const std::array<appstation::hal::AxisLimit, 6>& fallback) {
  // 读取 min/max 两个数组并覆盖到同一组 AxisLimit，未提供的轴保持默认限位。
  auto limits = fallback;
  for (size_t i = 0; i < limits.size(); ++i) {
    double minValue = limits[i].min;
    double maxValue = limits[i].max;
    if (jsonNumberArrayValue(body, minKey, i, &minValue)) {
      limits[i].min = minValue;
    }
    if (jsonNumberArrayValue(body, maxKey, i, &maxValue)) {
      limits[i].max = maxValue;
    }
  }
  return limits;
}

std::string jsonStringValueOr(const std::string& body, const std::string& key, const std::string& fallback) {
  const auto value = jsonStringValue(body, key);
  return value.empty() ? fallback : value;
}

appstation::hal::NativeTeleopConfig jsonNativeTeleopConfig(const std::string& body) {
  using appstation::hal::AxisLimit;
  using appstation::hal::NativeTeleopConfig;
  NativeTeleopConfig config;
  // 该函数把后端 JSON payload 转成原生 teleop 配置；所有字段都允许缺省。
  config.controlMode = jsonStringValueOr(body, "controlMode", config.controlMode);
  config.mappingMode = jsonStringValueOr(body, "mappingMode", config.mappingMode);
  config.loopHz = static_cast<int>(jsonNumberValue(body, "nativeLoopHz", config.loopHz));
  config.swapTeleopChannels = jsonBoolValue(body, "swapTeleopChannels", config.swapTeleopChannels);
  config.requireClutch = jsonBoolValue(body, "requireClutch", config.requireClutch);
  config.leftGravityCompensation =
      jsonBoolValue(body, "leftGravityCompensation", config.leftGravityCompensation);
  config.rightGravityCompensation =
      jsonBoolValue(body, "rightGravityCompensation", config.rightGravityCompensation);
  config.leftGravityScale = jsonNumberValue(body, "leftGravityScale", config.leftGravityScale);
  config.rightGravityScale = jsonNumberValue(body, "rightGravityScale", config.rightGravityScale);
  config.translationScale[0] = jsonNumberValue(body, "leftTranslationScale", config.translationScale[0]);
  config.translationScale[1] = jsonNumberValue(body, "rightTranslationScale", config.translationScale[1]);
  config.rotationScale[0] = jsonNumberValue(body, "leftRotationScale", config.rotationScale[0]);
  config.rotationScale[1] = jsonNumberValue(body, "rightRotationScale", config.rotationScale[1]);
  config.axisOutputScale[0] = jsonNumberArray6(body, "leftAxisOutputScale", config.axisOutputScale[0]);
  config.axisOutputScale[1] = jsonNumberArray6(body, "rightAxisOutputScale", config.axisOutputScale[1]);
  config.impulseCoeff[0] = jsonNumberArray6(body, "leftImpulseCoeff", config.impulseCoeff[0]);
  config.impulseCoeff[1] = jsonNumberArray6(body, "rightImpulseCoeff", config.impulseCoeff[1]);
  config.enabledAxes[0] = jsonBoolArray6(body, "leftEnabledAxes", config.enabledAxes[0]);
  config.enabledAxes[1] = jsonBoolArray6(body, "rightEnabledAxes", config.enabledAxes[1]);
  const std::array<AxisLimit, 6> defaultLimits{
      AxisLimit{-25000.0, 25000.0},
      AxisLimit{-37500.0, 37500.0},
      AxisLimit{-37500.0, 37500.0},
      AxisLimit{-100.0, 100.0},
      AxisLimit{-100.0, 100.0},
      AxisLimit{-7.0, 7.0},
  };
  // 默认软限位按 UI 单位表达：平移 um，旋转 degree。
  config.softLimits[0] = jsonAxisLimits(body, "leftSoftLimitMin", "leftSoftLimitMax", defaultLimits);
  config.softLimits[1] = jsonAxisLimits(body, "rightSoftLimitMin", "rightSoftLimitMax", defaultLimits);
  config.rotationWorkLimitEnabled =
      jsonBoolValue(body, "rotationWorkLimitEnabled", config.rotationWorkLimitEnabled);
  config.rotationWorkLimits[0] =
      jsonAxisLimits(body, "leftRotationWorkLimitMin", "leftRotationWorkLimitMax", config.rotationWorkLimits[0]);
  config.rotationWorkLimits[1] =
      jsonAxisLimits(body, "rightRotationWorkLimitMin", "rightRotationWorkLimitMax", config.rotationWorkLimits[1]);
  config.workOriginValid[0] = jsonBoolValue(body, "leftWorkOriginValid", config.workOriginValid[0]);
  config.workOriginValid[1] = jsonBoolValue(body, "rightWorkOriginValid", config.workOriginValid[1]);
  config.workOriginPulse[0] = jsonNumberArray6(body, "leftWorkOriginPulse", config.workOriginPulse[0]);
  config.workOriginPulse[1] = jsonNumberArray6(body, "rightWorkOriginPulse", config.workOriginPulse[1]);
  config.homeReferenceValid[0] = jsonBoolValue(body, "leftHomeReferenceValid", config.homeReferenceValid[0]);
  config.homeReferenceValid[1] = jsonBoolValue(body, "rightHomeReferenceValid", config.homeReferenceValid[1]);
  config.homeReferencePulse[0] = jsonNumberArray6(body, "leftHomeReferencePulse", config.homeReferencePulse[0]);
  config.homeReferencePulse[1] = jsonNumberArray6(body, "rightHomeReferencePulse", config.homeReferencePulse[1]);
  config.translationStepLimitPulse =
      jsonNumberValue(body, "translationStepLimitPulse", config.translationStepLimitPulse);
  config.rotationStepLimitPulse = jsonNumberValue(body, "rotationStepLimitPulse", config.rotationStepLimitPulse);
  config.translationPulseDeadband =
      jsonNumberValue(body, "translationPulseDeadband", config.translationPulseDeadband);
  config.rotationPulseDeadband = jsonNumberValue(body, "rotationPulseDeadband", config.rotationPulseDeadband);
  config.translationStartVelocityUmS =
      jsonNumberValue(body, "translationStartVelocityUmS", config.translationStartVelocityUmS);
  config.translationMaxVelocityUmS =
      jsonNumberValue(body, "translationMaxVelocityUmS", config.translationMaxVelocityUmS);
  config.rotationStartVelocityDegS =
      jsonNumberValue(body, "rotationStartVelocityDegS", config.rotationStartVelocityDegS);
  config.rotationMaxVelocityDegS =
      jsonNumberValue(body, "rotationMaxVelocityDegS", config.rotationMaxVelocityDegS);
  config.accTimeSec = jsonNumberValue(body, "motionProfileAccSec", config.accTimeSec);
  config.decTimeSec = jsonNumberValue(body, "motionProfileDecSec", config.decTimeSec);
  config.nativeTranslationDeadzoneM =
      jsonNumberValue(body, "nativeTranslationDeadzoneM", config.nativeTranslationDeadzoneM);
  config.nativeTranslationFullScaleM =
      jsonNumberValue(body, "nativeTranslationFullScaleM", config.nativeTranslationFullScaleM);
  config.nativeRotationDeadzoneDeg =
      jsonNumberValue(body, "nativeRotationDeadzoneDeg", config.nativeRotationDeadzoneDeg);
  config.nativeRotationFullScaleDeg =
      jsonNumberValue(body, "nativeRotationFullScaleDeg", config.nativeRotationFullScaleDeg);
  config.nativeVelocitySmoothingMs =
      jsonNumberValue(body, "nativeVelocitySmoothingMs", config.nativeVelocitySmoothingMs);
  // kalmanFilterEnabled：从 UI/后端 payload 读取滤波开关。
  config.kalmanFilterEnabled = jsonBoolValue(body, "kalmanFilterEnabled", config.kalmanFilterEnabled);
  // kalmanBeta：读取遗忘因子 beta，用于 Q/R 自适应更新。
  config.kalmanBeta = jsonNumberValue(body, "kalmanBeta", config.kalmanBeta);
  // kalmanMinVariance：读取 P/Q/R 数值下限。
  config.kalmanMinVariance = jsonNumberValue(body, "kalmanMinVariance", config.kalmanMinVariance);
  // kalmanMaxVariance：读取 P/Q/R 数值上限。
  config.kalmanMaxVariance = jsonNumberValue(body, "kalmanMaxVariance", config.kalmanMaxVariance);
  // kalmanDtMinSec：读取滤波 dt 下限。
  config.kalmanDtMinSec = jsonNumberValue(body, "kalmanDtMinSec", config.kalmanDtMinSec);
  // kalmanDtMaxSec：读取滤波 dt 上限。
  config.kalmanDtMaxSec = jsonNumberValue(body, "kalmanDtMaxSec", config.kalmanDtMaxSec);
  // kalmanTranslationPositionVariance：读取平移轴 P00 初始方差。
  config.kalmanTranslationPositionVariance =
      jsonNumberValue(body, "kalmanTranslationPositionVariance", config.kalmanTranslationPositionVariance);
  // kalmanTranslationVelocityVariance：读取平移轴 P11 初始方差。
  config.kalmanTranslationVelocityVariance =
      jsonNumberValue(body, "kalmanTranslationVelocityVariance", config.kalmanTranslationVelocityVariance);
  // kalmanTranslationMeasurementVariance：读取平移轴 R 初始方差。
  config.kalmanTranslationMeasurementVariance =
      jsonNumberValue(body, "kalmanTranslationMeasurementVariance", config.kalmanTranslationMeasurementVariance);
  // kalmanTranslationProcessPositionVariance：读取平移轴 Q00 初始方差。
  config.kalmanTranslationProcessPositionVariance =
      jsonNumberValue(
          body,
          "kalmanTranslationProcessPositionVariance",
          config.kalmanTranslationProcessPositionVariance);
  // kalmanTranslationProcessVelocityVariance：读取平移轴 Q11 初始方差。
  config.kalmanTranslationProcessVelocityVariance =
      jsonNumberValue(
          body,
          "kalmanTranslationProcessVelocityVariance",
          config.kalmanTranslationProcessVelocityVariance);
  // kalmanRotationPositionVariance：读取旋转轴 P00 初始方差。
  config.kalmanRotationPositionVariance =
      jsonNumberValue(body, "kalmanRotationPositionVariance", config.kalmanRotationPositionVariance);
  // kalmanRotationVelocityVariance：读取旋转轴 P11 初始方差。
  config.kalmanRotationVelocityVariance =
      jsonNumberValue(body, "kalmanRotationVelocityVariance", config.kalmanRotationVelocityVariance);
  // kalmanRotationMeasurementVariance：读取旋转轴 R 初始方差。
  config.kalmanRotationMeasurementVariance =
      jsonNumberValue(body, "kalmanRotationMeasurementVariance", config.kalmanRotationMeasurementVariance);
  // kalmanRotationProcessPositionVariance：读取旋转轴 Q00 初始方差。
  config.kalmanRotationProcessPositionVariance =
      jsonNumberValue(body, "kalmanRotationProcessPositionVariance", config.kalmanRotationProcessPositionVariance);
  // kalmanRotationProcessVelocityVariance：读取旋转轴 Q11 初始方差。
  config.kalmanRotationProcessVelocityVariance =
      jsonNumberValue(body, "kalmanRotationProcessVelocityVariance", config.kalmanRotationProcessVelocityVariance);
  // kalmanTranslationIntentVelocityThreshold：读取平移轴意图速度阈值 v_th。
  config.kalmanTranslationIntentVelocityThreshold =
      jsonNumberValue(body, "kalmanTranslationIntentVelocityThreshold", config.kalmanTranslationIntentVelocityThreshold);
  // kalmanRotationIntentVelocityThreshold：读取旋转轴意图速度阈值 v_th。
  config.kalmanRotationIntentVelocityThreshold =
      jsonNumberValue(body, "kalmanRotationIntentVelocityThreshold", config.kalmanRotationIntentVelocityThreshold);
  config.translationDeadzoneM = jsonNumberValue(body, "translationDeadzone", config.translationDeadzoneM);
  config.rotationDeadzoneDeg = jsonNumberValue(body, "rotationDeadzone", config.rotationDeadzoneDeg);
  config.incrementalTranslationMinEffectiveDeltaM = jsonNumberValue(
      body,
      "incrementalTranslationMinEffectiveDelta",
      config.incrementalTranslationMinEffectiveDeltaM);
  config.incrementalTranslationReverseDeadzoneM = jsonNumberValue(
      body,
      "incrementalTranslationReverseDeadzone",
      config.incrementalTranslationReverseDeadzoneM);
  config.continuousIncrementMode =
      jsonBoolValue(body, "continuousIncrementMode", config.continuousIncrementMode);
  config.translationInputEpsilonM =
      jsonNumberValue(body, "translationInputEpsilon", config.translationInputEpsilonM);
  config.rotationInputEpsilonDeg =
      jsonNumberValue(body, "rotationInputEpsilon", config.rotationInputEpsilonDeg);
  config.translationMinActivePulse =
      jsonNumberValue(body, "translationMinActivePulse", config.translationMinActivePulse);
  config.rotationMinActivePulse =
      jsonNumberValue(body, "rotationMinActivePulse", config.rotationMinActivePulse);
  config.continuousMicroConfirmTicks =
      static_cast<int>(jsonNumberValue(body, "continuousMicroConfirmTicks", config.continuousMicroConfirmTicks));

  config.gripperTeleopEnabled = jsonBoolValue(body, "gripperTeleopEnabled", config.gripperTeleopEnabled);
  config.gripper.ports[0] = jsonStringValueOr(body, "leftPort", "COM8");
  config.gripper.ports[1] = jsonStringValueOr(body, "rightPort", "COM9");
  config.gripper.slaveIds[0] = static_cast<int>(jsonNumberValue(body, "leftSlaveId", 10));
  config.gripper.slaveIds[1] = static_cast<int>(jsonNumberValue(body, "rightSlaveId", 9));
  config.gripper.baudrate = static_cast<int>(jsonNumberValue(body, "baudrate", config.gripper.baudrate));
  config.gripper.strokeMm = jsonNumberValue(body, "strokeMm", config.gripper.strokeMm);
  config.gripper.speed = static_cast<int>(jsonNumberValue(body, "gripSpeed", config.gripper.speed));
  config.gripper.torque = static_cast<int>(jsonNumberValue(body, "gripTorque", config.gripper.torque));
  config.gripper.dllPath = jsonStringValueOr(body, "jodellDllPath", config.gripper.dllPath);
  config.gripper.processWorkersEnabled =
      jsonBoolValue(body, "gripperProcessWorkersEnabled", config.gripper.processWorkersEnabled);
  config.gripper.workerExePath = jsonStringValueOr(body, "jodellWorkerExePath", config.gripper.workerExePath);
  config.gripper.workerCommandTimeoutMs =
      jsonNumberValue(body, "gripperWorkerCommandTimeoutMs", config.gripper.workerCommandTimeoutMs);
  config.gripperGapMinMm[0] = jsonNumberValue(body, "leftGapMinMm", config.gripperGapMinMm[0]);
  config.gripperGapMaxMm[0] = jsonNumberValue(body, "leftGapMaxMm", config.gripperGapMaxMm[0]);
  config.gripperGapMinMm[1] = jsonNumberValue(body, "rightGapMinMm", config.gripperGapMinMm[1]);
  config.gripperGapMaxMm[1] = jsonNumberValue(body, "rightGapMaxMm", config.gripperGapMaxMm[1]);
  config.gripperGapInvert[0] = jsonBoolValue(body, "leftGapInvert", config.gripperGapInvert[0]);
  config.gripperGapInvert[1] = jsonBoolValue(body, "rightGapInvert", config.gripperGapInvert[1]);
  config.gripperSourceHand[0] = jsonStringValueOr(body, "leftSourceHand", config.gripperSourceHand[0]);
  config.gripperSourceHand[1] = jsonStringValueOr(body, "rightSourceHand", config.gripperSourceHand[1]);
  config.gripperDeadbandCounts =
      static_cast<int>(jsonNumberValue(body, "positionDeadbandCounts", config.gripperDeadbandCounts));
  config.gripperMinCommandIntervalMs =
      jsonNumberValue(body, "minCommandIntervalMs", config.gripperMinCommandIntervalMs);
  config.gripperIcfTargetProtectionEnabled =
      jsonBoolValue(body, "icfTargetProtectionEnabled", config.gripperIcfTargetProtectionEnabled);
  config.gripperIcfTargetMinGapMm =
      jsonNumberValue(body, "icfTargetMinGapMm", config.gripperIcfTargetMinGapMm);
  config.gripperButtonFallback = jsonBoolValue(body, "buttonFallback", config.gripperButtonFallback);
  return config;
}

double effectiveGripperTargetMm(const appstation::hal::NativeTeleopConfig& config, double targetMm) {
  // ICF 目标保护会给夹爪留出最小间隙，避免 teleop 或手动命令完全闭合损伤末端。
  const double stroke = (std::max)(0.001, config.gripper.strokeMm);
  const double bounded = std::clamp(targetMm, 0.0, stroke);
  if (!config.gripperIcfTargetProtectionEnabled) {
    return bounded;
  }
  const double minGap = std::clamp(config.gripperIcfTargetMinGapMm, 0.0, stroke);
  return std::clamp(bounded, minGap, stroke);
}

appstation::hal::Side parseSide(const std::string& value) {
  // 后端只允许左右两侧，解析失败直接返回 500 给调用方暴露配置错误。
  if (value == "left") {
    return appstation::hal::Side::Left;
  }
  if (value == "right") {
    return appstation::hal::Side::Right;
  }
  throw std::runtime_error("side must be left or right");
}

appstation::hal::SemanticAxis parseAxis(const std::string& value) {
  // 轴名使用 UI 语义名，驱动层再映射到具体控制卡通道。
  using appstation::hal::SemanticAxis;
  if (value == "X") return SemanticAxis::X;
  if (value == "Y") return SemanticAxis::Y;
  if (value == "Z") return SemanticAxis::Z;
  if (value == "Roll") return SemanticAxis::Roll;
  if (value == "Pitch") return SemanticAxis::Pitch;
  if (value == "Yaw") return SemanticAxis::Yaw;
  throw std::runtime_error("unknown axis");
}

int envIntValue(const char* key, int fallback) {
  // 环境变量用于现场部署覆盖端口和 Omega openId；解析失败时回到代码默认值。
  const char* raw = std::getenv(key);
  if (!raw || !*raw) {
    return fallback;
  }
  char* end = nullptr;
  const auto value = std::strtol(raw, &end, 10);
  return end == raw ? fallback : static_cast<int>(value);
}

bool envBoolValue(const char* key, bool fallback) {
  // 支持常见布尔写法，便于 PowerShell/cmd 环境设置。
  const char* raw = std::getenv(key);
  if (!raw || !*raw) {
    return fallback;
  }
  const auto value = lowercase(raw);
  return value == "1" || value == "true" || value == "yes" || value == "on";
}

std::string httpResponse(int code, const std::string& body, bool keepAlive) {
  // 所有响应都按 JSON 返回；状态文本固定 OK，不依赖客户端读取 reason phrase。
  std::ostringstream out;
  out << "HTTP/1.1 " << code << " OK\r\n"
      << "Content-Type: application/json; charset=utf-8\r\n"
      << "Content-Length: " << body.size() << "\r\n"
      << "Connection: " << (keepAlive ? "keep-alive" : "close") << "\r\n\r\n"
      << body;
  return out.str();
}

}  // namespace appstation::hal
