# MicroMani 前端任务流程

用于页面、遥测显示、连接生命周期、相机预览和控制反馈；纯文案调整只执行相关部分。继承根目录项目规则。

## 定位与契约

1. 从目标页面或组件进入，追踪 `frontend/src/api/index.ts`、`frontend/src/stores/telemetry.ts` 和 `frontend/src/types.ts` 中实际使用的请求、状态和字段。
2. 修改跨层字段时，对照 `backend/core/schemas.py` 与对应路由；必要时补读 [后端流程](backend.md)。不要用静态假数据掩盖接口不匹配。
3. 操作者左右侧先对照 `frontend/src/data.ts`、`backend/core/operator_view.py` 和现有测试。显示单位以 `backend/core/units.py` 为依据。

## 实施要点

- 复用已有组件、Ant Design、样式和状态结构；不因修改一个页面引入新的 UI 框架或全局状态层。
- 订阅、WebSocket、事件监听、定时器及图像流需要对称释放；检查卸载、重复挂载和重连，避免多个连接重复发送命令。
- 命令请求完成与遥测确认是不同状态。按现有协议显示等待、拒绝、失败、断连或过期状态，不将未确认的执行结果显示为成功。
- 急停与设备保护不能只依赖按钮禁用；执行侧仍须校验。涉及这些行为时沿 API 继续核对后端/HAL。
- 相机相关修改同时核对显示、订阅清理和资源释放。截图只能证明当时的视觉状态，不能证明流生命周期正确。
- 不编辑 `frontend/dist/` 或根目录压缩 JS 作为源码修复。

## 验证

从仓库根目录按改动选择，而非全部固定执行：

```powershell
npm --prefix frontend test -- src/api.lifecycle.test.ts
npm --prefix frontend test -- src/components/CameraPreview.test.tsx
npm --prefix frontend run typecheck
```

页面任务替换为对应现有测试；组件交互检查用户可观察结果。入口、构建配置或资源导入变化再运行 `npm --prefix frontend run build`。需要 lint 时使用 package.json 中现有脚本。

视觉验收先读 `frontend/scripts/visual-check.mjs` 的环境和副作用；具备浏览器时检查实际受影响页面和关键状态。缺少浏览器或运行环境则报告未完成视觉验证，不能拿类型检查代替。连接实机的页面操作必须在已有授权范围内。

交付说明可见行为、相关契约变动、运行的检查以及尚未覆盖的实时连接或实机路径。
