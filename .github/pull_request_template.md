## 变更摘要

<!-- 说明改了什么，以及为什么需要改。 -->

## 架构契约核对

- [ ] 数据集/Policy 遵守 `appstation.dual_arm.operator_sides.v2`，HAL 保持硬件侧顺序。
- [ ] backend 是唯一转换边界，没有重复交换，也没有交换相机角色。
- [ ] state、action、pulses、force、单位和 metadata 保持一致。
- [ ] 缺少或未知契约的数据有明确拒绝/迁移策略，原数据默认保留。
- [ ] HKVL 为默认，显式 NI-DAQ 参数保留，没有自动 fallback。
- [ ] 本地控制租约、急停代际、新鲜度和执行侧保护仍有效。
- [ ] capability 仅声明实际实现，不以版本号或 UI 提示代替能力。

## 验证

<!-- 填写实际命令和结果，包括失败、跳过及原因；不要填未执行的验证。 -->

- Backend：
- Frontend typecheck/Vitest/build：
- HAL 隔离编译（列出 SDK/DDS 开关）：
- 本次差异检查：

## 部署与回退

- HalServer.exe / JodellGripperWorker.exe 的配套构建与部署状态：
- native Fast-DDS binding 是否需要重建：
- 外部 act_deploy.py 仓库、固定版本与接口兼容性：
- 配置/数据兼容及回退点：

## 未验证范围

<!-- 明确真实 HAL、DDS、串口、相机、运动设备、数据落盘和外部模型程序的覆盖。 -->

- [ ] 未提交 runtime 配置、凭据、SDK/DLL、模型权重、运行二进制或临时产物。
- [ ] 没有将离线测试表述为实机验证。
