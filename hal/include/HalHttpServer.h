/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：声明HalHttpServer 的接口与状态结构；提供本机 HTTP 健康探测入口；当前业务控制通过 DDS。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#pragma once

#include "HalCommandDispatcher.h"

namespace appstation::hal {

// 本地 HTTP 兼容入口，仅监听 loopback；实际命令执行仍统一走 HalCommandDispatcher。
int runHalHttpServer(
    int halPort,
    HalCommandDispatcher& commandDispatcher);

}  // namespace appstation::hal
