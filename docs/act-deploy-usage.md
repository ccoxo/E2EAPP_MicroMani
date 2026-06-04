# ACT 部署脚本使用说明

## 脚本位置

一键启动脚本：

```powershell
F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1
```

它会自动完成：

- 启动 HAL
- 启动后端 policy bridge
- 等待后端 `/api/policy/observation` 可用
- 进入 `lero` conda 环境
- 运行 `act_deploy.py`
- 打开三路相机
- 读取真实机械臂 state
- 推理 ACT action
- 通过后端限幅、安全检查后预览或发送动作

默认不启动前端，避免前端或后端摄像头探测抢占相机。

## 默认 Dry-Run

只推理和打印，不控制机械臂：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1
```

看到类似输出：

```text
Action: [...]
Delta left: {...} | right: {...} | sent=False
```

`sent=False` 表示没有真正发送动作。

## 真实控制机械臂

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send
```

默认安全小步参数：

```text
平移最大: 50 um
旋转最大: 0.02 deg
夹爪最大: 0.2 mm
```

如果机械臂太慢，可以逐步提高限幅。

中等速度：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send `
  -MaxTranslationUm 200 `
  -MaxRotationDeg 0.08 `
  -MaxGripperMm 0.5
```

更快一档：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send `
  -MaxTranslationUm 500 `
  -MaxRotationDeg 0.2 `
  -MaxGripperMm 1.0
```

不要一开始直接调很大。先确认机械臂运动方向、左右臂映射、相机顺序都正确。

## 更换训练好的模型

默认模型目录：

```text
D:\ACT_TEXT\act_text\checkpoints\003000\pretrained_model
```

如果新模型在：

```text
D:\ACT_TEXT\act_text\checkpoints\004000\pretrained_model
```

Dry-run：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 `
  -CheckpointDir D:\ACT_TEXT\act_text\checkpoints\004000 `
  -Checkpoint pretrained_model
```

真实发送：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 `
  -CheckpointDir D:\ACT_TEXT\act_text\checkpoints\004000 `
  -Checkpoint pretrained_model `
  -Send
```

新模型目录需要包含：

```text
config.json
model.safetensors
policy_preprocessor_step_3_normalizer_processor.safetensors
```

模型格式需要保持一致：

```text
observation.images.global
observation.images.wrist_left
observation.images.wrist_right

observation.state: 14 维
action: 14 维
```

14 维 state/action 顺序：

```text
left_x_um
left_y_um
left_z_um
left_roll_mdeg
left_pitch_mdeg
left_yaw_mdeg
left_gripper_gap_mm
right_x_um
right_y_um
right_z_um
right_roll_mdeg
right_pitch_mdeg
right_yaw_mdeg
right_gripper_gap_mm
```

如果新模型不是这个维度或顺序，需要同步修改部署脚本或后端 policy bridge。

## 相机顺序

默认相机参数：

```powershell
-CameraIds 1,2,0
```

含义：

```text
global      -> camera 1
wrist_left  -> camera 2
wrist_right -> camera 0
```

如果画面对应错了，修改 `-CameraIds`：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 `
  -CameraIds 1,2,0
```

## 开启前端

默认不启动前端，避免抢相机。

如果需要同时看前端：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -WithFrontend
```

真实发送并开前端：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send -WithFrontend
```

部署 ACT 时不建议开前端，因为可能导致相机读取失败。

## 常见问题

### `WinError 10061`

含义：

```text
后端没有启动，或者后端进程已经退出。
```

处理：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1
```

### `Failed to read camera`

含义：

```text
相机被其他程序占用，或相机顺序不对。
```

处理：

- 关闭前端页面
- 关闭其他相机程序
- 不加 `-WithFrontend` 重新运行脚本
- 检查 `-CameraIds`

### `sent=False`

含义：

```text
当前是 dry-run，没有控制机械臂。
```

处理：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send
```

### 机械臂很慢

含义：

```text
限幅参数较小。
```

处理：

```powershell
-MaxTranslationUm 200 -MaxRotationDeg 0.08 -MaxGripperMm 0.5
```

确认方向稳定后再继续加大。

### 机械臂方向不对

不要加大速度。优先检查：

- 相机顺序是否正确
- 左右臂是否对应正确
- `observation.state` 顺序是否和训练一致
- action 维度和单位是否和训练一致
- 轴符号映射是否正确

## 推荐启动命令

日常 dry-run：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1
```

低速真实控制：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send
```

中速真实控制：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 -Send `
  -MaxTranslationUm 200 `
  -MaxRotationDeg 0.08 `
  -MaxGripperMm 0.5
```

指定新模型：

```powershell
powershell -ExecutionPolicy Bypass -File F:\E2EAPP_MicroMani\scripts\run-act-deploy.ps1 `
  -CheckpointDir D:\ACT_TEXT\act_text\checkpoints\004000 `
  -Checkpoint pretrained_model `
  -Send
```
