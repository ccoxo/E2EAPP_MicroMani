#pragma once

#include <array>
#include <cstddef>
#include <string>

#include "HalTypes.h"
#include "ForceControlRuntime.h"
#include "NativeTeleopController.h"
#include "Omega7Driver.h"

namespace appstation::hal {

// HAL 只处理受控的小 JSON payload，这些 helper 负责轻量解析、序列化和默认值填充。
// 不是通用 JSON 解析器；新增命令字段时应优先保持格式简单、键名显式。
extern const std::array<bool, 6> kAllAxesEnabled;

std::string requestBody(const std::string& request);
std::string lowercase(std::string value);
int envIntValue(const char* key, int fallback);
bool envBoolValue(const char* key, bool fallback);

std::string jsonEscape(const std::string& value);
std::string jsonHealth(const HalHealth& motionHealth, bool omegaOk, const std::string& message);
std::string jsonMotionState(const MotionState& state);
std::string jsonTeleopTargetUpdateResult(Side side, const TeleopTargetUpdateResult& result);
std::string jsonOmegaState(const std::array<Omega7State, 2>& state);
std::array<Omega7State, 2> jsonOmegaStateValue(const std::string& body);

std::string jsonStringValue(const std::string& body, const std::string& key);
double jsonNumberValue(const std::string& body, const std::string& key, double fallback);
bool jsonBoolValue(const std::string& body, const std::string& key, bool fallback);
bool jsonNumberArrayValue(const std::string& body, const std::string& key, size_t index, double* out);
std::array<double, 12> jsonWorkOriginPulse(const std::string& body);
std::array<double, 6> jsonSideWorkOriginPulse(const std::string& body);
std::array<AxisLimit, 6> jsonTeleopSoftLimits(const std::string& body);
std::array<bool, 6> jsonTeleopEnabledAxes(const std::string& body);
std::array<double, 6> jsonNumberArray6(
    const std::string& body,
    const std::string& key,
    const std::array<double, 6>& fallback);
std::array<bool, 6> jsonBoolArray6(
    const std::string& body,
    const std::string& key,
    const std::array<bool, 6>& fallback);
std::array<std::array<bool, 6>, 2> jsonHomeAllEnabledAxes(const std::string& body);
std::array<AxisLimit, 6> jsonAxisLimits(
    const std::string& body,
    const std::string& minKey,
    const std::string& maxKey,
    const std::array<AxisLimit, 6>& fallback);
std::string jsonStringValueOr(const std::string& body, const std::string& key, const std::string& fallback);
ForceRuntimeConfig jsonForceRuntimeConfig(
    const std::string& body,
    const ForceRuntimeConfig& fallback = {});
NativeTeleopConfig jsonNativeTeleopConfig(const std::string& body);
double effectiveGripperTargetMm(const NativeTeleopConfig& config, double targetMm);
Side parseSide(const std::string& value);
SemanticAxis parseAxis(const std::string& value);

}  // namespace appstation::hal
