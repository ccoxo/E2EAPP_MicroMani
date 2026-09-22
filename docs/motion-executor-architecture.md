# 控制权仲裁与统一运动执行器

## 实现范围

`HalServer` 创建一个 `MotionExecutor`，显式注入 `HalCommandDispatcher`、
`NativeTeleopController` 和 `TeleopHardwareTargetExecutor`。
手动运动、回原点、外部连续目标、原生直连和 DDS Follower 的运动写入均经过它。
SDK 访问、轴单位转换、伺服检查、软限位和运动急停代际检查继续由 `LTDMCDriver` 执行。

```text
命令 dispatcher ───────────────────┐
原生映射（进程内路径）───────────────┼─ MotionExecutor ─ LTDMCDriver
DDS Follower ─ 柔顺目标适配 ────────┘
安全锁存 / 租约 / 力保护 ────────────────────┘
```

这是运动机构的控制权边界；Omega.7 力输出、夹爪串口 worker、策略推理调度
仍沿用既有职责。本轮不改变 DDS topic、IDL 字段、JSON 命令或进程布局。

## 控制权与切换

控制权按硬件侧保存：`Idle`、`Manual`、`Homing`、`External`、`NativeTeleop`。
它表示当前保留的控制源，不是硬件正在运动的标志。

| 来源 | 占用与释放 |
| --- | --- |
| 原生遥操作（直连或 DDS） | start 同时取得双侧控制权；stop 撤销许可、停止目标后释放 |
| 外部连续目标 | 首帧取得该侧控制权；后续同源更新复用；该侧 stop 或 disable 释放 |
| 手动 / 回原点 | 驱动调用返回后仍记住来源；后续申请需查询设备确认该侧已停止 |

- 不抢占原生或外部连续控制源；冲突请求返回错误。
- `motion.home_all` / `motion.home_origin_side` 保留原有的先停止原生遥操作行为，
  再申请 Homing；外部连续控制必须先显式停止。
- 原生会话内的离合/零增量停止仍保留 NativeTeleop 控制权，不允许手动命令趁机接管。
- `teleop.native.stop` 只释放原生控制权，不停止当前外部/手动控制源。
- 模式交接使用 `requireSideStopped` 的实时设备完成查询，不能依据缓存遥测批准交接。
- 运动提交与准入处于同一临界区；普通提交遇忙立即拒绝，不排队等待执行。
  本轮执行锁为整个设备共享，不承诺双侧长耗时操作并行。
- 运动调用失败可能已经部分下发，所以保留控制权；调用者须停止或走既有急停恢复流程。

## 停止与迟到目标

原生停止先以原子操作撤销执行许可，然后等待在途执行结束并停止驱动。
停止失败会进入既有控制故障锁存路径。急停、力保护及租约撤销不取得执行器锁，
仍先通过驱动急停代际撤销权限；急停确认不会恢复旧的原生会话许可。

**急停拥有最高否决权，不是普通的 MotionOwner，也不需要先取得控制权。**
即使普通执行器锁和驱动状态锁同时被占用，dispatcher 仍先锁存运动、原生遥操作和
主手力输出，再执行既有硬件停止路径。普通请求已通过准入但尚未进入驱动时，
后续驱动检查也必须拒绝旧代际；操作员确认急停不能复活该请求。

当前三个原生 DDS 节点处于同一 HAL 进程，使用同一个不重置的目标序号计数器。
执行器在 start 记录序号下界；按侧拒绝重复、倒序和停止前已发布的目标。
离合停止也推进该侧序号下界。该设计不新增线缆字段，**不支持将独立远程发布器的
序号直接当成本机会话身份**；跨进程独立重启/跨机器控制应另行引入会话契约。

现有 `motion.teleop_target_update` 没有策略实例身份，因此 External 仍是一个统一外部来源，
尚未实现多个策略实例之间的所有权仲裁。目标的通用 TTL、DDS 断流 watchdog 和
事件驱动收包不属于本轮实现。

## 离线验证与部署边界

从仓库根目录运行：

```powershell
cmd /c hal\tests\run_motion_tests.cmd
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_hal_source_contracts.py backend/tests/test_hal_protocol.py backend/tests/test_hal_dds_client.py -q
```

批处理关闭 vendor SDK 和 DDS，只运行离线测试，产物写入 `hal/build/motion-tests`。
新增 `MotionExecutorTests` 覆盖源冲突、按侧占用、停止/重启后的迟到目标、去重、
硬件未停拒绝交接、急停恢复、dispatcher 接入、忙时拒绝和在途回调撤销。
CMake 在关闭 vendor SDK 且 `BUILD_TESTING=ON` 时也注册该测试，超时为 30 秒。

专项并发回归包含：同时持有执行器锁和驱动状态锁时通过 dispatcher 急停，
以及请求已通过准入但尚未进入驱动时发生急停/确认的交错。
这些离线断言验证软件权限和锁依赖，不是对真实 SDK 阻塞或停机时延的保证。

本轮验证：180 项 Python 检查通过；仲裁 11 项、线程稳定性 6 项、worker 异常 14 项、
力自检运行时 15 项及 ForceCoreTests 通过。
整套批处理仍返回失败：旧 EmergencyStopTests 异常退出，ControlLeaseTests 报
`startup force self-check is not complete`；修改前 HEAD 的独立构建也复现了这两项失败。
本轮不修改与控制权改造无关的旧力自检测试语义。

启用真实 vendor SDK / Fast DDS 的 `HalServer.next.exe`、`JodellGripperWorker.next.exe`
已在 `hal/build/motion-architecture` 独立编译链接。未替换现场程序、未启动 DDS 运行验收，
未进行实机操作；编译和离线测试不等于设备安全验收。
