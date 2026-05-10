#include "MotionControlThread.h"

namespace appstation::hal {

MotionControlThread::MotionControlThread(LTDMCDriver& driver) : driver_(driver) {}

MotionControlThread::~MotionControlThread() {
  stop();
}

void MotionControlThread::start(int hz) {
  if (running_.exchange(true)) {
    return;
  }
  period_ = std::chrono::microseconds(1000000 / hz);
  worker_ = std::thread(&MotionControlThread::loop, this);
}

void MotionControlThread::stop() {
  if (!running_.exchange(false)) {
    return;
  }
  if (worker_.joinable()) {
    worker_.join();
  }
}

void MotionControlThread::setSoftLimits(std::array<AxisLimit, 12> limits) {
  limits_ = limits;
}

MotionState MotionControlThread::latestState() const {
  return latest_;
}

void MotionControlThread::loop() {
  while (running_) {
    const auto started = std::chrono::steady_clock::now();
    latest_ = driver_.readState();
    const auto elapsed = std::chrono::steady_clock::now() - started;
    if (elapsed < period_) {
      std::this_thread::sleep_for(period_ - elapsed);
    }
  }
}

}  // namespace appstation::hal
