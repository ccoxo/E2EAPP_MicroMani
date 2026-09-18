#pragma once

#include "HalCommandDispatcher.h"

namespace appstation::hal {

// 本地 HTTP 兼容入口，仅监听 loopback；实际命令执行仍统一走 HalCommandDispatcher。
int runHalHttpServer(
    int halPort,
    HalCommandDispatcher& commandDispatcher);

}  // namespace appstation::hal
