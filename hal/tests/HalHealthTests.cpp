#include "HalJson.h"
#include "LTDMCDriver.h"

#include <iostream>
#include <stdexcept>

using namespace appstation::hal;

int main() {
  try {
    // 不初始化控制卡或启动任何线程，只验证离线健康状态及其传输字段。
    LTDMCDriver motion;
    const auto health = motion.health(7.5);
    if (health.version != "hal-real/0.2") {
      throw std::runtime_error("HAL health version mismatch");
    }
    const auto payload = jsonHealth(health, false, "offline health test");
    if (payload.find("\"capabilities\":[]") == std::string::npos
        || payload.find("force_calibration_state_v1") != std::string::npos) {
      throw std::runtime_error("HAL advertised an unimplemented calibration capability");
    }
    if (payload.find("\"ltdmc_ok\":false") == std::string::npos
        || payload.find("\"uptime_s\":7.5") == std::string::npos) {
      throw std::runtime_error("HAL health fields changed unexpectedly");
    }
    std::cout << "HalHealthTests passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "HalHealthTests failed: " << error.what() << "\n";
    return 1;
  }
}
