## 变更摘要

<!-- 说明改了什么，以及为什么需要改。 -->

## 架构契约核对

- [ ] 我确认当前修改遵守 `appstation.dual_arm.operator_sides.v2`。
- [ ] 我确认 backend 是唯一的模型侧 ↔ 硬件侧转换边界，没有重复交换。
- [ ] 我确认没有因数值侧别调整而交换相机角色。
- [ ] 若修改数据集/策略输入，我已处理 `state`、`action`、`pulses`、`force` 和 metadata 的一致性。
- [ ] 若涉及旧数据，我已明确拒绝、人工确认或保留原数据的迁移路径。
- [ ] 若涉及硬件默认，我确认默认仍为 HKVL，并保留了显式 NI-DAQ 配置。
- [ ] 若涉及 HAL，我已说明 `HalServer.exe`、`JodellGripperWorker.exe` 和可选 Fast-DDS binding 的构建/部署状态。

## 验证

```text
# 填写实际执行的命令和结果；不要填写未执行的验证。
```

- [ ] Backend 相关测试：
- [ ] Frontend typecheck/Vitest：
- [ ] HAL 隔离离线编译：
- [ ] `git diff --check`：

## 未验证范围

- [ ] 我明确列出了未验证的真实 HAL、串口、相机、运动设备和外部 `act_deploy.py` 范围。
- [ ] 我确认没有提交 runtime 配置、凭据、SDK/DLL、模型权重、运行二进制或临时产物。

## 提交分组

- [ ] 源码修复
- [ ] 数据兼容
- [ ] 部署配套
