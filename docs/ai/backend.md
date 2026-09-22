# MicroMani 后端、录制与策略任务流程

用于 FastAPI 路由、配置、服务生命周期、LeRobot 录制与策略输入输出。继承根目录项目规则；按任务选择下面的分支。

## API 与服务

- 从 `backend/app_factory.py`、`backend/app.py` 找到服务装配和实际路由，再进入对应 `backend/services/`；避免仅按文件名猜测职责。
- 字段变更对照 `backend/core/schemas.py`、默认配置和前端类型；保留既有持久化配置的加载行为，必要时增加迁移回归。
- 不在模块导入时新增设备连接、线程或文件写入；按现有生命周期装配和释放资源。
- 遥操作来源可能共享控制器；停止一个录制/连接来源不能意外终止仍有效的其他来源。改动时检查 `teleop_mapping.py` 的来源管理。
- 真实 HAL 使用 `backend/hal_client/dds_client.py`，诊断 HTTP 与命令协议不能混淆；修改此边界时读 [HAL 流程](hal.md)。

## 录制与策略

- 从 `dataset_recorder.py` 的会话/episode 边界、帧组装和写队列定位问题。保持保存、丢弃、结束与排队帧的顺序，不绕过串行写入路径。
- 检查运动、力、夹爪和相机样本的时间来源、新鲜度、缺失值及对齐语义；超时或缺样不得伪装成正常测量。
- 先查 `units.py`、`policy_bridge.py` 和对应测试，再改变 observation/action。12 维运动、14 维含夹爪状态、旋转 degree/0.001 degree 的差异必须明确。
- 策略输出进入真实执行前仍须通过现有执行侧保护。模型文件的存在或推理成功不代表设备动作正确。
- 数据格式变更使用临时样本验证读写往返；保留用户已有数据集，不以生产数据作为可丢弃测试目录。

## 验证

依赖来源为 `backend/pyproject.toml`；优先 `backend/.venv/Scripts/python.exe`，缺失时报告或使用已确认兼容的环境。下列命令从根目录执行，选择相关项：

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_app.py -q
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_units.py backend/tests/test_operator_view.py -q
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_dataset_recorder.py backend/tests/test_policy_bridge.py -q
```

测试应使用替身和临时目录；检查测试实际配置，`APPSTATION_HAL_MODE=test` 本身不保证所有相机或传感器都被替换。不要为使离线测试通过而连接真实硬件。

LeRobot 等可选依赖缺失可能产生 skip，交付时区分通过和跳过。公共契约变化覆盖消费者；纯局部修复不强制全套测试。报告检查命令、结果、必要的失败原因与未验证路径。
