/*
 * 阅读导航 06｜HAL 硬件与安全
 * 职责：管理运动状态后台轮询线程的启动、循环和停止。
 * 先看：MotionControlThread::start → MotionControlThread::stop → MotionControlThread::loop。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
#include "MotionControlThread.h"

#include <stdexcept>

namespace appstation::hal {

// 只保存驱动引用，不拥有硬件资源；硬件初始化和关闭由外层服务生命周期管理。
MotionControlThread::MotionControlThread(
    LTDMCDriver& driver,
    std::function<void(const char*)> failureCallback,
    std::function<void()> poll,
    WorkerLauncher launcher)
    : driver_(driver), failureCallback_(std::move(failureCallback)),
      poll_(std::move(poll)), launcher_(std::move(launcher)) {}

MotionControlThread::~MotionControlThread() {
  stop();
}

void MotionControlThread::start(int hz) {
  std::scoped_lock lifecycleLock(lifecycleMutex_);
  if (hz <= 0 || hz > 1000000) throw std::invalid_argument("motion polling frequency must be in [1, 1000000]");
  if (running_.load()) return;
  if (worker_.joinable()) worker_.join();
  if (!lastError().empty() && driver_.estopActive()) throw std::runtime_error("motion polling fault requires emergency-stop acknowledgement before restart");
  period_ = std::chrono::microseconds(1000000 / hz);
  running_.store(true);
  try {
    worker_ = launchWorker(launcher_, [this]() {
      runWorkerBoundary([this]() { loop(); }, [this](const char* error) { reportFailure(error); });
    });
  } catch (...) {
    reportFailure("motion polling worker could not be created");
    throw;
  }
}

void MotionControlThread::stop() {
  std::scoped_lock lifecycleLock(lifecycleMutex_);
  running_.store(false);
  // 故障已清除 running 时，退出线程仍需回收。
  if (worker_.joinable()) {
    worker_.join();
  }
}

bool MotionControlThread::running() const { return running_.load(); }

std::string MotionControlThread::lastError() const {
  std::scoped_lock lock(diagnosticMutex_);
  return lastError_;
}

void MotionControlThread::reportFailure(const char* message) noexcept {
  running_.store(false);
  driver_.latchEmergencyStop();
  try { if (failureCallback_) failureCallback_(message); }
  catch (...) { std::fputs("motion polling fault: additional safety action failed\n", stderr); }
  try { driver_.emergencyStop(); }
  catch (...) { std::fputs("motion polling fault: emergency stop action failed\n", stderr); }
  std::fprintf(stderr, "motion polling stopped: %s\n", message);
  try { std::scoped_lock lock(diagnosticMutex_); lastError_ = message; }
  catch (...) { std::fputs("motion polling fault: diagnostic update failed\n", stderr); }
}

void MotionControlThread::loop() {
  while (running_) {
    const auto started = std::chrono::steady_clock::now();
    if (poll_) poll_();
    else driver_.readState();
    const auto elapsed = std::chrono::steady_clock::now() - started;
    // 周期从本轮开始时间计算，读硬件耗时会自动从 sleep 中扣除。
    if (elapsed < period_) {
      std::this_thread::sleep_for(period_ - elapsed);
    }
  }
}

}  // namespace appstation::hal
