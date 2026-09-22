# 本地代码同步与阅读整理记录

## 同步结果

- 日期：2026-09-09。
- 目录：`D:\E2EAPP_MicroMani`。
- 采用当前分支已配置的上游：[ccoxo/E2EAPP_MicroMani](https://github.com/ccoxo/E2EAPP_MicroMani)，分支 `codex/hal-native-teleop-v2`。
- 从 `fdd8ad4` 快进 6 个提交至 `6d898c9df4a3b13b415e3dabd75591ed2aabc2ac`；最后提交为 `feat: add HKVL force safety and hardware monitoring`。
- 同时 fetch 了 `origin`；它的同名分支仍为较旧的 `1114354`。按已有上游配置更新，没有切换到其他开发分支。
- 同步后 `HEAD...ccoxo/codex/hal-native-teleop-v2` 的左右差异计数为 `0 / 0`。

更新前已有 `claude.md` 的中文语言要求修改和 `scripts/run-act-deploy.ps1` 的删除。已按用户选择将部署脚本恢复为远端最新版本，保留 `claude.md` 修改。本地未跟踪的 `codex-provider-sync/`、`output/`、`paper/` 保持原样。

最终检查时发现其他来源在本任务执行期间更新了 `AGENTS.md`、`claude.md`、`.gitignore` 及项目协作资料。本任务没有写入这些规则文件，也没有用更新前备份覆盖它们；以当前本地版本为准。这些并行改动不计入下面的源码注释统计。

原有已跟踪修改的补丁、HEAD 和状态清单保存于 `C:\Users\TIANXI~1\AppData\Local\Temp\MicroMani-before-update-20260909-163151`。该备份仅覆盖当时已跟踪文件差异与状态记录，不是整目录备份。

## 阅读整理内容

- 新增 [根目录入口](../README.md)、[中文阅读指南](CODE_READING_GUIDE.md) 和 [完整源码索引](SOURCE_INDEX.md)。
- 193 个自有源码、测试与脚本增加中文文件头导航：后端 69 个、前端 61 个、HAL 44 个、scripts 17 个、根目录启动/停止脚本 2 个。
- 增加 25 处关键说明，覆盖服务装配、录制时间对齐和写线程、原点锁、操作者侧映射、DDS 超时与应答、力安全与柔顺回写。
- 源码文件保留原逻辑、原有注释、编码标记与换行风格。依赖、压缩构建产物、JSON/锁文件、二进制与数据样本保留原格式，并在索引解释用途。
- 本次注释和文档保留为本地未提交修改；未创建提交或推送 GitHub。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 注释差异还原 | 193 个文件移除新增注释并统一换行后，与拉取的 HEAD 内容完全一致。 |
| Python 语法树 | 全部本次处理的 Python 文件，与 HEAD 的 AST 一致。 |
| JavaScript / TypeScript 输出 | 59 个 JS/TS 文件在移除注释的转译设置下，输出与 HEAD 完全一致。 |
| PowerShell | 所有本次修改的 PowerShell 文件语法解析通过。 |
| Git 差异格式 | `git diff --check` 通过。 |
| 前端构建 | `npm run build` 通过，包含 `tsc -b` 类型检查。 |
| 前端独立测试组 | 排除 `src/App.test.tsx` 后，11 个测试文件、37 项全部通过。 |
| 前端仪表盘单项 | 当前代码及原始 HEAD 的 `renders the dashboard workbench` 均通过。 |
| 前端完整测试 | 初次全量运行长时间未结束后停止；单独对当前及原始 HEAD 的 App 测试组各设 55 秒期限，两者均未结束，最后均输出相同的前 8 项测试通过。完整前端回归未通过验收。 |
| 后端默认环境全量 | 528 通过、47 失败、5 跳过；47 项失败均因缺少 Fast-DDS 原生绑定 DLL。原始 HEAD 的代表性用例也复现同一错误。 |
| 后端原 47 项定向复查 | 仅对这 47 项设置 `APPSTATION_HAL_MODE=test`：42 通过，5 项仍因测试内显式使用 real 模式而缺少 DLL。 |

因此，后端在上述两种明确环境下累计有 570 个不同用例通过，但这不表示默认环境的全量测试通过。5 个跳过项中，4 个缺少 `lerobot[dataset]`，1 个缺少 `pyarrow`。

排查时还尝试过对整套后端测试统一设置 test 模式，结果为 565 通过、10 失败、5 跳过。这个覆盖不适合整套测试：部分服务测试本来验证真实模式的状态分支，也会被全局变量改变。最终以默认环境全量结果和原失败组的定向复查分别记录，不通过修改业务代码或断言掩盖环境差异。

## 本机环境与真实限制

前端通过现有 `package-lock.json` 执行 `npm ci`。后端创建了本项目的 `backend/.venv` 并安装测试所需运行依赖，未修改系统 Python 包或依赖声明。详细测试 XML 保存在本地忽略目录 `test-reports/`，测试日志在本机临时目录 `micromani-*.log`。

本机当前缺少已构建的 `backend/native/build/appstation_fastdds_transport.dll`；完整 HAL 编译及实机功能没有验收。本次没有启动应用硬件服务、运行设备运动脚本或部署模型。获取代码与源码注释完成，不能据此认为这台机器已具备完整实机运行环境。
