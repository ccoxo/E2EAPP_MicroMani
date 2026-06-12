#pragma once

#include <array>
#include <mutex>
#include <string>

#include "HalTypes.h"

namespace appstation::hal {

// 夹爪驱动配置。数组下标按 Side 左右顺序排列；端口、从站号和目标行程来自现场接线与夹爪型号。
struct JodellGripperConfig {
  bool enabled{true};
  std::array<std::string, 2> ports{"COM8", "COM9"};
  std::array<int, 2> slaveIds{10, 9};
  int baudrate{115200};
  double strokeMm{26.0};
  int speed{255};
  int torque{1};
  std::string dllPath{"F:/E2EAPP_MicroMani/backend/vendor/jodell/jodellTool.dll"};
  bool processWorkersEnabled{true};
  std::string workerExePath{};
  double workerCommandTimeoutMs{2000.0};
};

// JodellGripperDriver 封装久德夹爪 DLL/串口协议。默认通过独立 worker 进程执行命令，
// 避免左右夹爪共用同一 vendor DLL 状态时发生串口切换干扰。
class JodellGripperDriver {
 public:
  JodellGripperDriver();
  ~JodellGripperDriver();

  // 更新端口、从站号、速度和 worker 策略；会关闭已打开资源，让下一次命令按新配置重建。
  void configure(const JodellGripperConfig& config);
  // 下发目标开口，targetMm 单位为毫米；message 返回 vendor/worker 诊断信息。
  bool commandTarget(
      Side side,
      double targetMm,
      int speed,
      int torque,
      std::string* message = nullptr,
      bool readPosition = true);
  // 主动读取某侧当前位置，成功后刷新 positionMm_ 缓存。
  bool readPositionMm(Side side, std::string* message = nullptr);
  // 以下快照接口供 teleop 状态 JSON 使用，调用方不需要直接持有 mutex_。
  std::array<double, 2> targetMm() const;
  std::array<double, 2> positionMm() const;
  std::array<double, 2> positionMmSnapshot(std::array<double, 2> fallback) const;
  std::string lastError() const;

 private:
#ifdef _WIN32
  // 每侧一个 worker 进程，stdin/stdout 作为简单的 tab 分隔命令通道。
  struct ProcessWorkerHandle {
    void* process{nullptr};
    void* stdinWrite{nullptr};
    void* stdoutRead{nullptr};
  };
#endif

  // Unlocked 后缀表示调用方必须已经持有 mutex_，以串行化 vendor DLL 和 worker 管道访问。
  bool ensureLoadedUnlocked(std::string* message);
  bool ensurePortOpenUnlocked(int index, int port, std::string* message);
  int portNumber(const std::string& value) const;
  int sideIndex(Side side) const;
#ifdef _WIN32
  bool ensureProcessWorkerUnlocked(int index, std::string* message);
  bool commandProcessWorkerUnlocked(
      int index,
      const std::string& command,
      double* positionMm,
      std::string* message);
  void closeProcessWorkersUnlocked();
#endif

  // mutex_ 保护配置、目标/位置缓存、DLL 句柄和 worker 进程句柄。
  mutable std::mutex mutex_;
  JodellGripperConfig config_{};
  // targetMm_ 是最近一次命令目标；positionMm_ 是最近一次读取或命令返回的位置。
  std::array<double, 2> targetMm_{{0.0, 0.0}};
  std::array<double, 2> positionMm_{{-1.0, -1.0}};
  std::string lastError_;

#ifdef _WIN32
  void closeUnlocked();
  void* module_{nullptr};
  int(__stdcall* serialOperation_)(int, int, int) = nullptr;
  int(__stdcall* clawEnable_)(int, int) = nullptr;
  int(__stdcall* runWithParam_)(int, int, int, int) = nullptr;
  int(__stdcall* getClawCurrentLocation_)(int) = nullptr;
  // activePorts_ 记录 DLL 当前认为已打开的串口，用于减少重复打开和切换端口时的等待。
  std::array<int, 2> activePorts_{{-1, -1}};
  std::array<ProcessWorkerHandle, 2> workerProcesses_{};
#endif
};

}  // namespace appstation::hal
