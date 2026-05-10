# E2EAPP_MicroMani 与 ICFNewProject 控制/遥操作核对报告

生成时间：2026-05-09  
核对范围：
- `F:\E2EAPP_MicroMani`
- `F:\ICFNewProject`

本报告按源码和配置重新核对，重点覆盖遥操作链路、速度、缩放、限幅、端口、轴映射和未实装项。

---

## 1. 总览对比

| 项目 | E2EAPP_MicroMani | ICFNewProject |
|---|---|---|
| 主架构 | Python FastAPI 后端 + TS 前端 + C++ HAL HTTP 服务 | Qt C++ 单进程主控 + Unity/PICO 客户端 |
| 运动卡接口 | HAL `HalServer` 调 LTDMC | Qt 进程直接调 LTDMC |
| Omega.7 接入 | C++ HAL `Omega7Driver`，Python 读取 `/omega/state` | 本地 Qt `MDevice` 直接读；无本地设备时 TCP fallback |
| 遥操作主循环 | Python `TeleopMappingService`，100 ms | `CMoveTele` 线程，10 ms |
| 遥操作运动方式 | 每次挑一个最大轴，发相对 jog：`dmc_pmove` | 每周期更新目标位置：`dmc_update_target_position` |
| 单步上限 | 平移 20 um；旋转 0.02 deg | 平移 4000 pulse；旋转 700 pulse |
| 速度 | 平移 100 um/s；旋转 0.2 deg/s | 平移 max 5 mm/s；旋转 max 1 deg/s |
| PICO 端口 | adb 5555，video 12345，command 13579 | adb 5555，video 12345，command 13579 |
| 关键差异 | 保守、低速、记录联动 | 连续跟随、10 ms、高响应 |

---

## 2. E2EAPP_MicroMani

### 2.1 目录结构

| 路径 | 作用 |
|---|---|
| `F:\E2EAPP_MicroMani\backend` | FastAPI 后端、业务服务、HAL client、遥操作映射 |
| `F:\E2EAPP_MicroMani\frontend` | TS 前端 |
| `F:\E2EAPP_MicroMani\hal` | C++ HAL，封装 LTDMC、Omega.7、HTTP server |
| `F:\E2EAPP_MicroMani\scripts` | 启停/辅助脚本 |
| `F:\E2EAPP_MicroMani\硬件指南` | 硬件说明文档 |

### 2.2 默认配置

来源：`F:\E2EAPP_MicroMani\backend\core\defaults.py`

运动默认 profile：
- 平移 start velocity：10 um/s
- 平移 max velocity：100 um/s
- 平移 acc/dec：0.02 s
- 旋转 start velocity：0.3 deg/s
- 旋转 max velocity：3 deg/s
- 旋转 acc/dec：0.02 s

软限位：
- X：±25000 um
- Y：±37500 um
- Z：±37500 um
- Roll：±180 deg
- Pitch：±70 deg
- Yaw：±7.5 deg

HAL：
- HTTP base：`http://localhost:8091`
- WebSocket：`ws://localhost:8091/ws/telemetry`
- axisCount：12
- mode：real
- timeout：5000 ms

运动：
- leftCardNo：1
- rightCardNo：0
- motionThreadHz：1000
- jogStepUm：50
- jogStepDeg：0.05
- positionSource：`dmc_get_position`

力传感：
- left device：`Dev5/ai0:5`
- right device：`Dev3/ai0:5`
- sampleHz：200
- lowpassHz：10
- voltage：±10 V

夹爪：
- left COM：COM8
- right COM：COM9
- baudrate：115200
- leftSlaveId：10
- rightSlaveId：9
- stroke：26 mm
- target：13 mm
- commandForceLimitN：8
- commandSpeed：10
- commandTorque：1

安全阈值：
- Fxy warn/stop：2 N / 4 N
- Fz warn/stop：3 N / 5 N
- Moment warn/stop：0.02 Nm / 0.04 Nm
- watchdog：50 ms

自动控制默认：
- translationStepUm：100
- rotationStepDeg：0.1
- translationVelocityUmS：100
- rotationVelocityDegS：0.1

PICO：
- ip：`10.90.131.124`
- adbPort：5555
- videoPort：12345
- commandPort：13579
- gateway：`10.90.0.1`
- ifIndex：13
- rotation：`ccw90`
- scriptsDir：`F:/ICFNewProject/PicoWirelessTools`

### 2.3 E2E 遥操作配置

来源：`F:\E2EAPP_MicroMani\backend\core\defaults.py`

基础参数：
- speedModes：coarse=1.0，medium=0.35，fine=0.08
- inputIntervalMs：10
- commandIntervalMs：10
- leftOpenId：0
- rightOpenId：1
- gravityCompensation：true
- forceFeedback：false
- requireClutch：false
- tcpFallbackPort：12345

缩放：
- leftTranslationScale：0.24
- rightTranslationScale：0.30
- leftRotationScale：0.18
- rightRotationScale：0.18

死区：
- translationDeadzone：0.00002 m，即 20 um 原始主手位移
- rotationDeadzone：0.08 deg

夹爪遥操作：
- loopHz：100
- gap min/max：0 / 50 mm
- openThreshold：0.30
- closeThreshold：0.70
- gripSpeed：128
- gripTorque：192
- releaseSpeed：255
- releaseTorque：64
- objectDetectMargin：10

### 2.4 E2E 遥操作主循环

来源：`F:\E2EAPP_MicroMani\backend\services\teleop_mapping.py`

启动行为：
- 只在真实 HAL 模式下执行硬件运动。
- 启动日志标注最大步长：20 um / 0.02 deg。
- test 模式不发硬件运动。

状态上报中的限制：
- translationStepUm：20
- rotationStepDeg：0.02
- translationVelocityUmS：100
- rotationVelocityDegS：0.2

循环周期：
- `period_s = 0.1`
- 即 100 ms 主循环。
- sleep 下限 20 ms，避免异常忙等。

健康门控：
- HAL 必须 connected。
- `ltdmc_ok` 必须 true。
- `omega7_ok` 必须 true。
- 不满足时清除主手 reference，不继续发运动。

激活条件：
- 后端逻辑连接为 true。
- 对应 Omega hand connected。
- `lastReadOk` 为 true。
- 如果 `requireClutch=true`，还要求 clutch pressed。
- pose 长度必须为 6。

参考帧：
- 每只手第一次激活时，当前 pose 作为 reference。
- 第一帧只建参考，不发运动。

坐标增量：
- 平移：`delta_m * 1e6 * sideTranslationScale`
- 旋转：`delta_deg * sideRotationScale`

死区计算：
- 平移死区会乘 side scale 后比较。
- 例如 left：20 um * 0.24 = 4.8 um 输出死区。
- right：20 um * 0.30 = 6 um 输出死区。
- 旋转 left/right：0.08 deg * 0.18 = 0.0144 deg 输出死区。

单次命令：
- 每个 side 每轮只选择 score 最大的一个轴。
- 平移 step：`min(abs(value), 20)` um
- 旋转 step：`min(abs(value), 0.02)` deg
- 平移 velocity：100 um/s
- 旋转 velocity：0.2 deg/s
- speedMode：fine
- startVelocityUiPerSec：maxVelocity 的 20%
- acc/dec：0.05 s

busy 估算：
- 平移最大步 20 um / 100 um/s + 0.15 = 0.35 s。
- 旋转最大步 0.02 deg / 0.2 deg/s + 0.15 = 0.25 s。
- 同轴 busy 期间不重复下发该轴命令。

### 2.5 E2E 夹爪遥操作

来源：`F:\E2EAPP_MicroMani\backend\services\gripper_tele_service.py`

输入：
- 从 HAL Omega state 读取 `gripperGapMm`。
- gap 归一化为 `[0,1]`。

Schmitt 状态机：
- open 状态下，`n >= 0.70` 触发 close。
- close 状态下，`n <= 0.30` 触发 open。
- close 后启动 30 tick 检测倒计时。
- 默认 loopHz=100，所以 30 tick 约 300 ms。

命令参数：
- close：speed 128，torque 192。
- open：speed 255，torque 64。

物体检测：
- 使用 strokeMm 和 objectDetectMargin 计算阈值。
- 默认 26 mm * 10 / 255 ≈ 1.02 mm。
- 当前 E2E Python 逻辑用 `position_mm > threshold` 判断 detected。
- 这与 ICF 的 Jodell raw loc 判定方式不同，建议上机复核语义。

### 2.6 E2E 后端接口

来源：`F:\E2EAPP_MicroMani\backend\app.py`

基础：
- `GET /api/health`
- `GET /api/hardware/status`
- `GET /api/settings`
- `POST /api/hal/reconnect`
- `WS /ws`

运动：
- `POST /api/motion/emergency_stop`
- `POST /api/motion/home_all`
- `POST /api/motion/origin`
- `POST /api/motion/{side}/enable_all`
- `POST /api/motion/{side}/home`
- `POST /api/motion/manual_axis_move`
- `POST /api/motion/safety/acknowledge`

力/夹爪：
- `POST /api/sensors/tare`
- `POST /api/force/{side}/tare`
- `POST /api/gripper/{side}/command`
- `GET /api/gripper/{side}/position`
- `GET /api/gripper/{side}/diagnose`

遥操作：
- `POST /api/teleop/gripper/start`
- `POST /api/teleop/gripper/stop`
- `GET /api/teleop/gripper/status`
- `POST /api/teleop/clutch_toggle`
- `POST /api/teleop/speed`
- `POST /api/teleop/{side}/connect`
- `POST /api/teleop/{side}/disconnect`
- `POST /api/teleop/{side}/gravity_compensation`
- `POST /api/teleop/{side}/zero_force_feedback`
- `GET /api/teleop/state`

PICO：
- `POST /api/pico/adb/connect`
- `POST /api/pico/vision/start`
- `POST /api/pico/vision/stop`
- `GET /api/pico/status/check`

WebSocket：
- UI 上游约 30 Hz。
- motion/omega state 约每 50 ms 刷新。

### 2.7 E2E HAL

来源：
- `F:\E2EAPP_MicroMani\hal\include\HalTypes.h`
- `F:\E2EAPP_MicroMani\hal\src\HalServer.cpp`
- `F:\E2EAPP_MicroMani\hal\src\LTDMCDriver.cpp`
- `F:\E2EAPP_MicroMani\hal\src\Omega7Driver.cpp`

语义轴：
- X
- Y
- Z
- Roll
- Pitch
- Yaw

物理轴映射：
- left：`{0,1,3,5,4,2}`
- right：`{2,0,5,8,1,7}`

pulse per unit：
- left：`{-9000,-10000,-10000,1666.666667,2500,3333.333333}`
- right：`{-4878.0487804878,10000,-1923.07692307692,1666.666667,-2500,-3333.333333}`

单位：
- 平移 UI 单位是 um。
- 旋转 UI 单位是 deg。
- 底层平移物理单位按 mm 与 pulse 换算。

HAL server：
- 绑定 `127.0.0.1:8091`
- 启动时初始化 LTDMC 和 Omega。
- motion OK 时启动 1000 Hz motion thread。

HAL HTTP：
- `GET /health`
- `GET /motion/state`
- `GET /omega/state`
- `POST /motion/emergency_stop`
- `POST /motion/home_all`
- `POST /motion/enable_side`
- `POST /motion/home_side`
- `POST /motion/manual_axis_move`

Omega state：
- side
- connected
- calibrated
- openId
- deviceId
- serial
- systemName
- leftHanded
- pose[6]
- clutchPressed：button 0
- gripperPressed：button 1
- gripperGapMm：SDK gap * 1000
- lastReadOk / lastReadMessage

LTDMC 相对运动：
- `moveRelativeUi` 对单次 jog 做硬上限：
  - 平移 5000 um
  - 旋转 2 deg
- 调 `dmc_check_done`
- 调 `dmc_set_profile`
- 调 `dmc_set_s_profile`
- 调 `dmc_pmove(..., relative mode 0)`
- 软限位包含 yaw ±7.5 deg。

未实装/占位：
- `moveAllUi` 当前只更新本地 pulse array，未真正下发硬件群动。
- Omega gravity compensation 为 TODO。
- Omega force feedback zero / force feedback 为 TODO。

### 2.8 E2E 手动命令服务

来源：`F:\E2EAPP_MicroMani\backend\services\command_service.py`

手动 jog 验证：
- 平移单步上限 5000 um。
- 旋转单步上限 2 deg。

速度 profile：
- translation velocity cap：20000 um/s。
- rotation velocity cap：30 deg/s。
- speedMode scale：
  - coarse：1.0
  - medium：0.5
  - fine：0.2
- start velocity 默认 max 的 20%。
- acc/dec clamp：0.001 到 5 s。

注意：
- `defaults.py` 里 teleop speedModes 是 1.0/0.35/0.08。
- `command_service.py` 手动命令 profile 使用自己的 1.0/0.5/0.2。
- `teleop_mapping.py` 直接下发自己的 100 um/s 和 0.2 deg/s，不依赖手动 UI profile 的默认 max。

---

## 3. ICFNewProject

### 3.1 目录结构

| 路径 | 作用 |
|---|---|
| `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest` | Qt C++ 主控程序 |
| `F:\ICFNewProject\xrt_unity_client_src_2023_trial` | Unity/PICO 客户端 |
| `F:\ICFNewProject\PicoWirelessTools` | PICO 无线/投屏工具 |
| `F:\ICFNewProject\tools` | 测试工具，例如 Ubuntu teleop client |
| `F:\ICFNewProject\docs` | 文档 |
| `F:\ICFNewProject\_archive` | 历史归档 |

### 3.2 ICF config.ini 关键参数

来源：`F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\config.ini`

TCP：
- `[Tcp] Port=9000`
- 这是旧 UI 路径可能使用的端口。
- MainWindow 本地 Omega 不存在时 fallback master TCP 端口硬编码为 12345。

遥操作轴映射：
- LeftAxisMap：`0,1,3,5,4,2`
- RightAxisMap：`8,6,11,14,7,13`

逻辑含义：
- left：X0,Y0,Z0,C0,A0,B0
- right：X1,Y1,Z1,C1,A1,B1

ImpulseCoeff：
- legacy：`1000000,-1000000,-1000000,-5000,-3333,-2500`
- LeftImpulseCoeff：`-9000000,-10000000,-10000000,1667,2500,3333`
- RightImpulseCoeff：`-4878049,10000000,-1923077,1667,-2500,-3333`

方向：
- LeftDirectionSigns：`-1,-1,-1,1,1,1`
- RightDirectionSigns：`-1,1,-1,1,-1,-1`

其他映射：
- SyncImpulseCoeffFromKinematics：true
- LeftOpenId：0
- RightOpenId：1
- LocalSwapHands：true
- LocalMappingMode：direct
- ControlMode：incremental

稳定控制：
- LocalStabilityMode：off
- LocalRegulatePos：true
- LocalRegulateRot：true
- LocalAutoInit：true
- LocalFollowGainPos：0.10
- LocalFollowGainRot：0.08

缩放：
- TranslationOutputScale：0.30
- RotationOutputScale：0.18
- EffectiveTranslationOutputScale：1
- EffectiveRotationOutputScale：1
- LeftTranslationOutputScale：0.24
- RightTranslationOutputScale：0.30
- LeftRotationOutputScale：0.18
- RightRotationOutputScale：0.18
- LeftAxisOutputScales：`0.20,0.20,0.20,2.00,2.00,2.00`
- RightAxisOutputScales：`0.40,0.20,1.00,2.00,2.00,2.00`

死区：
- TranslationDeadzone：0.00002 m
- RotationDeadzone：0.08 deg
- IncrementalTranslationMinEffectiveDelta：0.00005 m
- IncrementalTranslationReverseDeadzone：0.00010 m

步长限制：
- TranslationStepLimit：4000 pulse
- RotationStepLimit：700 pulse

周期：
- InputProcessIntervalMs：10
- CommandUpdateIntervalMs：10

速度：
- TranslationStartSpeedMmPerSec：0.4
- TranslationMaxSpeedMmPerSec：5.0
- RotationStartSpeedDegPerSec：0.08
- RotationMaxSpeedDegPerSec：1.0
- ProfileAccTimeSec：0.05
- ProfileDecTimeSec：0.05

Safety：
- Teleop 轴默认启用。
- Soft limit 默认 ±200000000 pulse。
- AutoForceGuard：false
- AutoForceLimit：`10,10,10,300,300,300`

Auto/ZMQ：
- Backend：`zmq_msgpack`
- ObsPub：`tcp://0.0.0.0:5555`
- ActionRep：`tcp://0.0.0.0:5556`
- CtrlPush：`tcp://0.0.0.0:5557`
- ControlFreqHz：100
- ActionCardNo：0
- ActionAxisMap：`0,1,3,4,2,5`
- MaxDeltaPulse：`3000,3000,3000,500,500,500`

PICO：
- Ip：`10.90.132.174`
- adb：5555
- video：12345
- command：13579
- gateway：`10.90.0.1`
- ifIndex：13
- rotation：`ccw90`

Jodell：
- right Port：9
- right Slave：9
- left Port：8
- left Slave：10
- angle range：右手 0 到 -30；左手 0 到 +30
- thresholds：0.30 / 0.70
- close speed/torque：128 / 192
- open speed/torque：255 / 64
- objectDetectMargin：10

### 3.3 ICF 逻辑轴和物理轴

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\TypeData.h`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\CMoveBase.cpp`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\CMoveTele.cpp`

逻辑轴枚举：
- X0=0
- Y0=1
- B0=2
- Z0=3
- A0=4
- C0=5
- Y1=6
- A1=7
- X1=8
- Y2=9
- X2=10
- Z1=11
- Z2=12
- B1=13
- C1=14

RemoteAxisNum：
- X0,Y0,Z0,A0,B0,C0
- X1,Y1,Z1,A1,B1,C1
- X2,Y2,Z2

物理 card/axis：
- left 逻辑轴使用 leftCardNo，配置为 1。
- right 逻辑轴使用 rightCardNo，配置为 0。
- X0 -> physical 0
- Y0 -> physical 1
- B0 -> physical 2
- Z0 -> physical 3
- A0 -> physical 4
- C0 -> physical 5
- Y1 -> physical 0
- A1 -> physical 1
- X1 -> physical 2
- Y2 -> physical 3
- X2 -> physical 4
- Z1 -> physical 5
- Z2 -> physical 6
- B1 -> physical 7
- C1 -> physical 8

语义轴映射：
- left X0 -> X
- left Y0 -> Y
- left Z0 -> Z
- left C0 -> Roll
- left A0 -> Pitch
- left B0 -> Yaw
- right X1 -> X
- right Y1 -> Y
- right Z1 -> Z
- right C1 -> Roll
- right A1 -> Pitch
- right B1 -> Yaw

### 3.4 ICF 本地主手/Omega

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\MDevice.cpp`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\MainWindow.cpp`

读取：
- `dhdGetPositionAndOrientationDeg`
- 输出 x/y/z/rx/ry/rz。
- `dhdGetGripperAngleDeg` 读取夹爪角。

打开：
- 根据配置 `LeftOpenId=0`、`RightOpenId=1` 调 `dhdOpenID`。
- 可读取 serial、handedness。

本地设备存在时：
- MainWindow 启动 local master timer。
- 周期 10 ms。
- 读取左右主手。
- 写入 `operatorServer->MoveAllInfo.LeftHandPos/RightHandPos`。
- 同时更新 Jodell 夹爪遥操作。

本地设备不存在时：
- 停止本地读取。
- 启动 TCP server，端口 12345。

### 3.5 ICF TCP 主手协议

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\QTCPS.cpp`
- `F:\ICFNewProject\tools\ubuntu_teleop_client.py`

支持输入：
- newline JSON。
- int message。
- binary `MoveALLInformation` frame。

JSON 格式：
```json
{
  "MoveInfo": {
    "GearCmd": 1,
    "ScaleCmd": 4192,
    "remakeCmd": 4209
  },
  "LeftHandPos": {"x":0,"y":0,"z":0,"a":0,"b":0,"g":0},
  "RightHandPos": {"x":0,"y":0,"z":0,"a":0,"b":0,"g":0},
  "NumPos": 1
}
```

兼容字段：
- `DeviceNum`
- `DeciveNum`，源码里保留了这个拼写。

测试客户端默认：
- host：127.0.0.1
- port：12345
- rate：30 Hz
- device-num：1
- gear：1
- scale：0x1060
- remake：0x1071

### 3.6 ICF QTCPChild 输入转换

来源：`F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\QTCPChild.cpp`

模式：
- `relative`：手当前位置 - 初始位置。
- `incremental`：手当前位置 - 上一帧位置。
- config 当前为 `incremental`。

帧逻辑：
- `DeciveNum=0`：停止。
- `DeciveNum=1`：左手。
- `DeciveNum=2`：双手。
- 第一帧和 pause/resume 后只重置初始位置，不产生运动。

死区过滤：
- 平移死区：0.00002 m。
- 旋转死区：0.08 deg。
- incremental 模式有累计量。
- 反向运动需要更大的 reverse deadzone。
- 未超过阈值时输出 0。

脉冲转换：
- translation：`delta_m * pulse_per_mm * 1000 * sign`
- rotation：`delta_deg * pulse_per_deg * sign`
- config 中 `SyncImpulseCoeffFromKinematics=true` 时，实际系数由 Kinematics 段派生。

Gear/Scale/Remake：
- `GearCmd` 写入 `CMoveTele::m_sfGear`。
- `ScaleCmd=0x1060`：
  - C0/C1 decision tree scale = 0.5
  - 其他轴 = 1.0
- `ScaleCmd=0x1061`：
  - C0/C1 = 0.05
  - X0/X1/Z0/Z1 = 0.25
  - 其他轴 = 1.0
- `remakeCmd=0x1070`：停止并 InitPos。
- `remakeCmd=0x1071`：恢复。

### 3.7 ICF CMoveTele 遥操作运动线程

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\CMoveTele.h`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\CMoveTele.cpp`

线程：
- 继承 `QThread`。
- MOVETELENUM=15。
- `run()` 中先 apply profile。
- 然后 reset tracking。
- while 未停止：
  - `updateCurrentMoveInfoPos()`
  - `Move()`
  - `msleep(commandIntervalMs)`
- 当前 config 为 10 ms。

默认值：
- TranslationOutputScale：0.30
- RotationOutputScale：0.10
- Left/Right translation side scale：0.30 / 0.30
- Left/Right rotation side scale：0.10 / 0.10
- TranslationStepLimit：3000 pulse
- RotationStepLimit：500 pulse
- Translation start/max：1 / 10 mm/s
- Rotation start/max：0.3 / 3 deg/s
- acc/dec：0.02 s
- interval：10 ms

config 覆盖后：
- TranslationOutputScale：0.30
- RotationOutputScale：0.18
- LeftTranslationOutputScale：0.24
- RightTranslationOutputScale：0.30
- LeftRotationOutputScale：0.18
- RightRotationOutputScale：0.18
- TranslationStepLimit：4000 pulse
- RotationStepLimit：700 pulse
- Translation start/max：0.4 / 5.0 mm/s
- Rotation start/max：0.08 / 1.0 deg/s
- acc/dec：0.05 s
- interval：10 ms

profile：
- 只对 12 个双臂语义轴设置。
- pulseScale 来自 Kinematics。
- 平移：速度 mm/s * pulse/mm。
- 旋转：速度 deg/s * pulse/deg。
- 调 `dmc_set_profile(card, axis, start, max, acc, dec, 0)`。
- 调 `dmc_set_s_profile`。

Move()：
- 遍历 `RemoteAxisNum`。
- 从 `TCPTelePOS` 取当前轴 impulse。
- 计算系数：
  - relative scale
  - contact scale
  - collision scale
  - gear
  - decision-tree scale
  - axis output scale
- 得到 requestedDelta。
- 调 `SafetyGuard::ApplyAxisLimit` 做轴启用、单步、软限位限制。
- 目标 pulse 累加到 `m_nHuaTaiRealPos`。
- 调 `dmc_update_target_position(card, axis, target, 1)`。

### 3.8 ICF SafetyGuard

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\SafetyGuard.h`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\SafetyGuard.cpp`

限制码：
- none
- axis_disabled
- delta_clip
- soft_limit_clip

行为：
- 轴禁用时 delta 置 0。
- `stepLimit > 0` 时，delta clamp 到 ±stepLimit。
- target clamp 到 soft min/max。
- 默认 soft limit 为 ±200000000 pulse。

注意：
- ICF 的 4000 pulse 是每 10 ms target jump 限制，不是 um。
- 以左 X 9000 pulse/mm 估算，4000 pulse ≈0.444 mm/tick，折合约 44.4 mm/s 的目标跳变上限。
- 以右 Z 1923 pulse/mm 估算，4000 pulse ≈2.08 mm/tick，折合约 208 mm/s 的目标跳变上限。
- 因此当前 step limit 比 5 mm/s profile 宽很多，实际速度更可能由 profile/驱动跟随行为限制。

### 3.9 ICF Jodell 夹爪遥操作

来源：`F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\JodellGripperTele.cpp`

状态：
- first tick 只 armed，不发命令。
- 支持角度极性，右手 0 到 -30，左手 0 到 +30。

归一化：
- `n=(AngleMaxDeg - angle)/signedSpan`

触发：
- `n >= closeThreshold` 时 close。
- `n <= openThreshold` 时 open。

命令：
- close target：255
- close speed：128
- close torque：192
- open target：0
- open speed：255
- open torque：64

物体检测：
- close 后 countdown 30 tick。
- 如果原始 loc `< 255 - objectDetectMargin`，认为 object detected。
- margin 默认 10。

### 3.10 ICF 力传感

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\NiDaqForceDriver.cpp`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\config\force_devices.ini`

现代 NI-DAQ 路径：
- left：Dev5
- right：Dev3
- SampleRateHz：200
- SamplesPerRead：1
- UseOnDemand：true
- mode：DIFF
- voltage：±10 V
- lowpass：10 Hz

旧路径：
- `ForceProc.cpp` 里还有 legacy Dev3 / 1000 Hz 逻辑。
- 不应与现代 `NiDaqForceDriver` 混为同一条主路径。

### 3.11 ICF Auto/ZMQ

来源：
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\AutoModeCommManager.h`
- `F:\ICFNewProject\QSerialTest3.0\QSerialTest\QSerialTest\AutoModeCommManager.cpp`

端口：
- obs PUB：5555
- action REP：5556
- ctrl PUSH：5557

周期：
- action poll interval：10 ms。
- ControlFreqHz：100。

动作：
- 6 轴动作。
- 根据 confidence 和 pulse coeff 缩放。
- SafetyGuard 限制 max delta 和 soft limit。
- 最终调 `dmc_update_target_position(actionCardNo, axis, target, 1)`。

### 3.12 ICF Unity/PICO

来源：
- `F:\ICFNewProject\xrt_unity_client_src_2023_trial\Assets\Scripts\Network\TcpManager.cs`
- `F:\ICFNewProject\xrt_unity_client_src_2023_trial\Assets\Scripts\Network\TcpHandler.cs`
- `F:\ICFNewProject\xrt_unity_client_src_2023_trial\Assets\Scripts\UICameraCtrl.cs`

TcpManager：
- public port：13579。
- 支持 TCP server/client。

TcpHandler：
- legacy TCP_PORT：63901。
- default PC IP：127.0.0.1。
- function JSON 发送。
- Tracking 可作为 functionName=`Tracking` 发送。

UICameraCtrl：
- 启动 camera stream coroutine。
- 停止 TcpManager server。
- 启动 RemoteCameraWindow。
- 启动 TcpManager client 连接 video source IP。

视频配置：
- `video_source.yml` 中 VR 源为 2160x810，60 fps。

---

## 4. 关键差异和风险点

### 4.1 遥操作速度差异

E2EAPP：
- 100 ms 循环。
- 每侧每轮只动一个轴。
- 平移最大 20 um/命令。
- 旋转最大 0.02 deg/命令。
- 平移速度 100 um/s。
- 旋转速度 0.2 deg/s。
- 同轴 busy 后，实际同轴重复命令间隔约 0.25 到 0.35 s。

ICF：
- 10 ms 循环。
- 多轴可连续更新 target。
- 平移 profile max 5 mm/s，即 5000 um/s。
- 旋转 profile max 1 deg/s。
- step limit 是 pulse 级 target jump 限制，不是 UI 位移单位。

结论：
- ICF 遥操作明显更接近连续跟随。
- E2E 当前更像安全保守 jog recorder 模式。

### 4.2 缩放相同但执行完全不同

两边都有：
- leftTranslationScale：0.24
- rightTranslationScale：0.30
- left/rightRotationScale：0.18
- translationDeadzone：0.00002 m
- rotationDeadzone：0.08 deg

但：
- E2E 缩放后再截断到 20 um / 0.02 deg，并只发一个轴 jog。
- ICF 缩放后进入 pulse delta，再经 per-axis scale、gear、decision tree scale、SafetyGuard，然后连续 target update。

### 4.3 PICO IP 不一致

E2E 默认：
- `10.90.131.124`

ICF 默认：
- `10.90.132.174`

端口一致：
- adb 5555
- video 12345
- command 13579

建议：
- 如果两工程要指向同一台 PICO，需要统一 IP 或明确 profile。

### 4.4 ICF step limit 与速度关系

之前有说法认为 `TranslationStepLimit=4000` 会压低 `MaxSpeed=5 mm/s` 的峰值。按源码单位看，这个判断不准确。

原因：
- `TranslationStepLimit=4000` 是 pulse/tick。
- tick 是 10 ms。
- 对多数平移轴，它折合几十到上百 mm/s 的 target jump 上限。
- 它比 5 mm/s profile 宽，不会主动压低到 5 mm/s 以下。

更准确表述：
- 目标跳变上限较宽。
- 实际运动速度更可能由 `dmc_set_profile` 的 max speed、驱动插补/跟随行为和伺服响应决定。

### 4.5 夹爪物体检测逻辑不完全一致

ICF：
- close 后读取 raw loc。
- `loc < 255 - margin` 判断夹到物体。

E2E：
- close 后读取 position_mm。
- `position_mm > threshold` 判断 detected。

建议：
- 确认 E2E `HardwareService.gripper.position` 返回的是开口 mm、闭合位移 mm，还是经过换算的其他语义。

### 4.6 未实装项

E2E：
- `moveAllUi` 未真正下发硬件群动。
- Omega gravity compensation 是 TODO。
- Force feedback / zero force feedback 是 TODO。

ICF：
- `ForceProc.cpp` 有 legacy 力采集路径，需区分是否仍被使用。
- `config.ini [Tcp] Port=9000` 与 MainWindow fallback `12345` 并存，容易误读。

---

## 5. 后续建议

1. 如果目标是让 E2E 复刻 ICF 手感，优先比较这几项：
   - E2E 100 ms loop 是否改为 10 ms。
   - E2E 单轴选择是否改为多轴 target update。
   - E2E 20 um / 0.02 deg jog 上限是否放宽。
   - E2E HAL 是否增加 `dmc_update_target_position` 路径。

2. 如果目标是安全优先，只对 E2E 做保守调参：
   - 先保持 jog 模式。
   - 小幅提高 velocity。
   - 明确 busy 逻辑对实际响应的影响。
   - 保留每侧单轴选择策略。

3. 如果目标是统一两工程配置：
   - 统一 PICO IP。
   - 统一 Omega openId。
   - 统一 deadzone/scale。
   - 明确 TCP fallback 端口到底使用 12345 还是 config 的 9000。

4. 如果目标是修夹爪检测：
   - 先记录 E2E position 返回值在空夹、全闭、夹物三种情况下的数值。
   - 再决定是否与 ICF raw loc 逻辑对齐。
