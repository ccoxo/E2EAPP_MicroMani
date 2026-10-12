#include <windows.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include "JodellGripperDriver.h"

using namespace appstation::hal;

int main(int argc, char** argv) {
  // 自身充当管道 worker，不加载厂商 DLL、不连接串口。
  if (argc > 1) {
    std::string line;
    while (std::getline(std::cin, line) && line != "EXIT") {
      std::cout << "OK\t12\t" << GetCurrentProcessId() << std::endl;
    }
    return 0;
  }
  try {
    JodellGripperDriver driver;
    JodellGripperConfig config;
    config.workerExePath = argv[0];
    driver.configure(config);
    const auto worker = [&]() {
      std::string message;
      if (!driver.commandTarget(Side::Left, 12, 10, 1, &message)) {
        throw std::runtime_error(message);
      }
      return message;
    };
    const auto first = worker();
    driver.configure(config);
    if (worker() != first) throw std::runtime_error("identical config restarted worker");
    config.ports[0] = "COM_TEST_ONLY";
    driver.configure(config);
    if (worker() == first) throw std::runtime_error("changed config retained old worker");
    config.enabled = false;
    driver.configure(config);
    if (driver.commandTarget(Side::Left, 12, 10, 1)) {
      throw std::runtime_error("disabled driver accepted command");
    }
    std::cout << "JodellGripperLifecycleTests passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
