#pragma once

#include "LTDMCDriver.h"
#include "TeleopDdsTypes.h"

namespace appstation::hal {

// 把 DDS 硬件目标转换成 LTDMCDriver 的 teleop target 更新调用。
// 这个类不订阅 DDS，也不做映射计算，只守住最终写硬件的边界。
class TeleopHardwareTargetExecutor {
 public:
  explicit TeleopHardwareTargetExecutor(LTDMCDriver& motion);

  void apply(const TeleopHardwareTarget& target);

 private:
  LTDMCDriver& motion_;
};

}  // namespace appstation::hal
