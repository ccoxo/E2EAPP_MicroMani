# xie 分支上传说明（2026-09-18）

目标仓库为 `ccoxo/E2EAPP_MicroMani`，分支为小写 `xie`。本分支保存当前本地工程的源码版本，以及已按用户指定范围整合的远端改动。

## 来源与上传范围

- 本地源码快照：`45343fe86e8990f07951510c1353cfbad5c37f4f`（`codex/Xie`）。
- 上传规则核对来源：远端 `codex/hal-native-teleop-v2` 的 `aabe6734b2ad39945641fe8a53e107c8d3409b76`。
- 新分支共同历史基点：`6d898c9df4a3b13b415e3dabd75591ed2aabc2ac`。
- 在独立工作树复制、筛选本地文件；原工程、原分支和个人工作目录保留。
- 本地快照提交包含临时输出及备份，因此没有把该提交接入 `xie` 的祖先历史；这里只移入其筛选后的文件内容。既有远端分支没有改写。

上传包含 backend、frontend、HAL、启动/部署脚本、测试、依赖声明与锁文件、项目文档及协作入口。HAL fixture、IDL、完整后端测试辅助模块、前端启动/视觉检查脚本均保留。`frontend/output/ui-redesign/` 只保留 `DESIGN.md` 和用户指定的 `SESSION_SUMMARY.md` 两份项目文档。

排除 `output/`、其下的源码调查副本和备份 ZIP、前端临时输出、生成 bundle、9 个一次性源码重写脚本、`paper/`、`codex-provider-sync` 嵌套仓库，以及 runtime 配置、凭据、依赖目录、SDK、运行二进制和编译中间文件。`.gitignore` 已补齐相应规则。筛选前共 1,621 个 Git 跟踪条目，保留 383 个，排除 1,238 个；新增本说明后本分支包含 384 个文件。

本次是源码上传，没有安装 SDK、启动设备或将构建产物部署到工控机。

## 源码修复

保留本地界面重构、模块隔离、控制租约、HTTP 所有权、急停代际与执行检查，保留移除开机自动回工作原点后的行为。沿用 HKVL 主用和 NI-DAQ 手动备用，不自动切换来源。

按已确认范围整合腕相机候选识别、稳定身份绑定、保存与刷新，以及自动曝光下尝试禁止暗光降帧。自定义身份不会再被旧 index 标签迁移覆盖；相机角色不参与数值通道换侧。

## 数据兼容

录制和 Policy API 使用 `appstation.dual_arm.operator_sides.v2`，由 backend 在模型与 HAL 边界转换，HAL、原点和标定保持硬件侧语义。录制状态、在线 observation、保持位置 action 和原点归一化遵守一致的 14 维布局与单位。

已识别的 native 旧集缺少或不兼容契约时拒绝续录；新集写入完整 metadata。未自动迁移任何用户数据。legacy 预览/上传等此前审计指出的补漏没有纳入此次功能整合，不能把上传规则中的要求当成所有历史入口已通过验收。具体见[数据契约与部署说明](data-contract-and-deployment.md)。

## 部署配套

HAL health 上报 `hal-real/0.2` 和实际 capability。当前本地没有迁入双阶段校准状态机，能力列表为空，不声明 `force_calibration_state_v1`；能力检查仅用于诊断。

正式部署仍需具备完整 SDK/Fast-DDS 环境，并从同一份源码构建 `HalServer.exe` 和 `JodellGripperWorker.exe`。本轮完整 HalServer 构建未完成：DDS OFF 的既有源码分支仍无条件包含 `fastcdr/Cdr.h`，当前开发机缺少该依赖。不能使用关闭 DDS/vendor 的 Worker 骨架替代现场程序。

外部 `act_deploy.py` 不在仓库，其仓库 URL、固定版本、依赖锁定及参数实现未提供，标记为**未验证**。调用更新的 Policy action API 需要传递匹配的完整 `dataContract`；不能假定外部旧程序已经兼容。native Fast-DDS 运行 DLL 未在本机重新部署。

## 验证

2026-09-18 在本次筛选后的上传工作树执行，复用原工程 Python 虚拟环境，仅操作临时夹具：

```powershell
python -m pytest backend/tests/test_data_contract.py backend/tests/test_dataset_recorder.py backend/tests/test_normalize_origin.py backend/tests/test_units.py backend/tests/test_policy_bridge.py backend/tests/test_policy_contract_api.py backend/tests/test_control_stop_races.py backend/tests/test_hardware_defaults.py backend/tests/test_force_config.py backend/tests/test_camera_identification.py backend/tests/test_camera_fixed_fps.py backend/tests/test_camera_format.py backend/tests/test_hal_health_capabilities.py backend/tests/test_hal_dds_client.py -q -rs -p no:cacheprovider
```

结果：**219 passed、1 skipped**，94 条既有弃用警告。跳过项为需要 `pyarrow` 的 parquet 归一化用例；真实 `lerobot` 写入使用替身覆盖，未进行真实数据落盘验收。

上传文件与刚完成整合验证的源码逐文件核对；仅五个设置卡清理了文件末尾多余空行，没有改变代码内容，另增加上传说明、README 入口和忽略规则。以下结果来自同日整合阶段，未重复运行，也不与上面的数量累计：

| 范围 | 实际命令/方式 | 结果 |
| --- | --- | --- |
| 前端腕相机和预览 | `npm --prefix frontend test -- src/components/WristCameraIdentification.test.tsx src/views/SettingsView.cameraIdentification.test.tsx src/components/CameraPreview.test.tsx` | 3 文件、10 passed |
| 设置和 API 生命周期 | `npm --prefix frontend test -- src/views/SettingsView.regressions.test.tsx src/api.lifecycle.test.ts src/api.controlRecovery.test.ts` | 3 文件、18 passed |
| 前端类型与生产构建 | `npm --prefix frontend run build`（包括 typecheck） | 通过 |
| 前端规范 | 新相机组件及测试的定向 ESLint | 通过，非全仓 lint |
| HAL health | MSVC 隔离编译并运行 `HalHealthTests`，DDS/vendor OFF | 通过 |
| HAL 两个目标 | CMake + VS2022 Professional，DDS/vendor OFF | core、Worker 通过；HalServer 失败，原因见部署配套 |
| 相机 Windows 属性助手 | C# 仅编译 | 通过，未执行 COM/设备操作 |

上传完整性检查覆盖 30 个 CMake 本地路径、117 个 HAL 引号 include、254 个 Python 本地 import、430 个前端相对引用；相关文件均在允许上传集合。待提交差异的 `git diff --cached --check` 已通过。允许上传文本的有界凭据模式检查未发现明显真实秘密；识别到的 token 字符串为 mock 测试占位值。这不是全历史秘密审计。

`.specify/integrations/codex.manifest.json` 的 9 个 `speckit-*` 技能目标在原本地工程就不存在；此缺口不是本次过滤造成，也不影响应用构建。上传该元数据不表示这些技能已经安装。

## 回退与未验证范围

本分支不会改变 `master` 或现有开发分支。后续合并前可直接比较 `xie`；应用到其他分支后，应通过独立 revert 或逐项恢复撤回，不清除其他本地修改。代码回退不自动转换已生成的新契约数据。

真实 HAL、DDS 通信、串口、相机、运动、外部模型程序及长期录制未验证。隔离测试和源码上传不构成实机部署或安全验收。

更早的本机工作记录见[会话总结](../frontend/output/ui-redesign/SESSION_SUMMARY.md)。其中绝对磁盘路径指向本机证据；临时日志与备份按上传规则保留在原电脑，不随源码发布。
