#include "MotionControlThread.h"

namespace appstation::hal {

// 只保存驱动引用，不拥有硬件资源；硬件初始化和关闭由外层服务生命周期管理。
MotionControlThread::MotionControlThread(LTDMCDriver& driver) : driver_(driver) {}

MotionControlThread::~MotionControlThread() {
  stop();
}

void MotionControlThread::start(int hz) {
  // exchange 同时完成“检查是否已运行”和“置为运行”，避免重复创建线程。
  if (running_.exchange(true)) {
    return;
  }
  // hz 由调用方保证为正数；这里保持最小实现，不额外引入配置校验。
  period_ = std::chrono::microseconds(1000000 / hz);
  worker_ = std::thread(&MotionControlThread::loop, this);
}

void MotionControlThread::stop() {
  // 未运行时直接返回；正在运行时让 loop 自然退出后 join。
  if (!running_.exchange(false)) {
    return;
  }
  if (worker_.joinable()) {
    worker_.join();
  }
}

void MotionControlThread::loop() {
  while (running_) {
    const auto started = std::chrono::steady_clock::now();
    // readState 内部负责真实硬件读取、缓存退回和异常隔离。
    driver_.readState();
    const auto elapsed = std::chrono::steady_clock::now() - started;
    // 周期从本轮开始时间计算，读硬件耗时会自动从 sleep 中扣除。
    if (elapsed < period_) {
      std::this_thread::sleep_for(period_ - elapsed);
    }
  }
}

}  // namespace appstation::hal
