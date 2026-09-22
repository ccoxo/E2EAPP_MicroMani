#pragma once

#include "HalJson.h"
#include <cmath>
#include <limits>
#include <stdexcept>
#include <string>

namespace appstation::hal {

inline void ensureCommandNotExpired(const std::string& payload, double nowUnixMs) {
  // 旧客户端可缺省；新后端覆盖该字段，防止超时请求在 HAL 排队后迟到执行。
  if (payload.find("\"commandExpiresAtUnixMs\"") == std::string::npos) return;
  const double deadline = jsonNumberValue(payload, "commandExpiresAtUnixMs", std::numeric_limits<double>::quiet_NaN());
  if (!std::isfinite(deadline) || !std::isfinite(nowUnixMs) || nowUnixMs >= deadline) {
    throw std::runtime_error("HAL command expired before execution");
  }
}

}  // namespace appstation::hal
