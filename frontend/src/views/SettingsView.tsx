import { Button, Dropdown, Form, Input, InputNumber, Modal, Segmented, Select, Slider, Space, Switch, Tabs, Tag, Typography, type MenuProps } from 'antd'
import {
  Activity,
  AlertTriangle,
  Camera,
  Cpu,
  Crosshair,
  Download,
  FolderOpen,
  Gamepad2,
  Hand,
  Network,
  Pause,
  Play,
  PlugZap,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  Square,
  Trash2,
  Usb,
  Waves,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { CameraPreview } from '../components/CameraPreview'
import { ForceChart } from '../components/Charts'
import {
  connectTeleopHand,
  disconnectTeleopHand,
  captureMotionOrigin,
  clearMotionOrigin,
  disableMotionSide,
  enableMotionSide,
  applyCameraTuning,
  homeMotionSide,
  reconnectCamera,
  reconnectHal,
  setTeleopGravityCompensation,
  tareForceSensor,
  zeroTeleopForceFeedback,
  startGripperTeleop,
  stopGripperTeleop,
  stopMotionSide,
  fetchGripperTeleopStatus,
  mockMode,
} from '../api'
import { refreshCameraStream } from '../hooks/useLiveCameraSnapshot'
import {
  armHardwareSpecs,
  axisHardwareSpecs,
  cameraHardwareSpecs,
  forceChannels,
  nano17Spec,
  semanticAxes,
  type RobotSide,
} from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import type {
  AppConfig,
  ArmMotionProfile,
  ArmSoftLimitConfig,
  CameraTelemetry,
  CameraTuningProfile,
  ConnectionState,
  LogEntry,
  ManualControlAction,
  ManualControlAxis,
  ManualControlMemory,
  ManualControlState,
  ManualGripperCommand,
  ManualSpeedMode,
  ParameterSnapshotScope,
  TelemetryFrame,
} from '../types'

type CameraKey = keyof typeof cameraHardwareSpecs

const sideOrder: RobotSide[] = ['left', 'right']
const cameraOrder: CameraKey[] = ['global', 'wrist_left', 'wrist_right']
const defaultCameraTuning: Record<CameraKey, CameraTuningProfile> = {
  global: {
    autoExposure: false,
    exposure: -5.5,
    gain: 0,
    autoWhiteBalance: false,
  },
  wrist_left: {
    autoExposure: false,
    exposure: -6,
    gain: 0,
    autoWhiteBalance: false,
  },
  wrist_right: {
    autoExposure: false,
    exposure: -6,
    gain: 0,
    autoWhiteBalance: false,
  },
}
const previewResolutionOptions = [
  { value: '640x480', label: '640x480（推荐）' },
  { value: '320x240', label: '320x240（低负载）' },
]

const hashLabels: Record<string, string> = {
  hal: 'HAL 通信',
  safety: '安全链路',
  teleop: 'PICO-4 视觉推流',
  'motion-left': '左运动控制卡',
  'motion-right': '右运动控制卡',
  'camera-global': '全局相机',
  'camera-left': '左腕相机',
  'camera-right': '右腕相机',
  'force-left': '左 Nano-17',
  'force-right': '右 Nano-17',
  'gripper-left': '左夹爪',
  'gripper-right': '右夹爪',
  'teleop-left': '左 Omega.7',
  'teleop-right': '右 Omega.7',
  manual: '手动控制',
}

function tabForHardwareHash(focusHash: string) {
  return focusHash === 'manual' ? 'manual' : 'config'
}

function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function stateTone(state: ConnectionState) {
  if (state === 'ok') return 'success'
  if (state === 'warn') return 'warning'
  if (state === 'error') return 'error'
  if (state === 'checking') return 'processing'
  return 'default'
}

function stateText(state: ConnectionState) {
  if (state === 'ok') return '正常'
  if (state === 'warn') return '注意'
  if (state === 'error') return '错误'
  if (state === 'checking') return '检查中'
  return '待确认'
}

function cameraByKey(cameras: CameraTelemetry[], key: CameraKey) {
  return cameras.find((camera) => camera.key === key)
}

function formatAxisValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? `${value.toFixed(1)} µm` : `${value.toFixed(3)}°`
}

function displaySoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value : value / 1000
}

function configSoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value : value * 1000
}

function formatSoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value.toFixed(0) : value.toFixed(3)
}

function formatForceValue(value: number, index: number) {
  return index < 3 ? `${(value * 1000).toFixed(0)} mN` : `${(value * 1000).toFixed(1)} mN·m`
}

function formatGripperPosition(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} mm` : '不可用'
}

function safeGripperPosition(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
}

function forceState(values: number[], config: AppConfig) {
  const danger = Math.max(
    Math.abs(values[0]) / config.safety.fxyStopN,
    Math.abs(values[1]) / config.safety.fxyStopN,
    Math.abs(values[2]) / config.safety.fzStopN,
    Math.abs(values[3]) / config.safety.momentStopNm,
    Math.abs(values[4]) / config.safety.momentStopNm,
    Math.abs(values[5]) / config.safety.momentStopNm,
  )
  if (danger >= 1) return 'error'
  if (danger >= 0.65) return 'warn'
  return 'ok'
}

function commandLog(injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void, channel: LogEntry['channel'], msg: string) {
  injectLog('INFO', msg, channel)
}

function formatSnapshotTime(ts: number) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(ts))
}

function defaultSnapshotName(scope: ParameterSnapshotScope) {
  const prefix = scope === 'all' ? '全局硬件' : scope === 'motion-left' ? '左臂运动控制卡' : '右臂运动控制卡'
  return `${prefix}快照 ${formatSnapshotTime(Date.now())}`
}

function motionSnapshotScope(side: RobotSide): ParameterSnapshotScope {
  return side === 'left' ? 'motion-left' : 'motion-right'
}

function snapshotModalTitle(scope: ParameterSnapshotScope) {
  if (scope === 'all') return '保存全局硬件参数快照'
  return scope === 'motion-left' ? '保存左臂运动控制卡参数' : '保存右臂运动控制卡参数'
}

const softLimitRows = [
  { key: 'x', label: 'X', unit: 'µm' },
  { key: 'y', label: 'Y', unit: 'µm' },
  { key: 'z', label: 'Z', unit: 'µm' },
  { key: 'roll', label: 'Roll', unit: '°' },
  { key: 'pitch', label: 'Pitch', unit: '°' },
  { key: 'yaw', label: 'Yaw', unit: '°' },
] as const

const manualAxisOrder: ManualControlAxis[] = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const speedModeOptions: { value: ManualSpeedMode; label: string }[] = [
  { value: 'fine', label: '精调' },
  { value: 'medium', label: '中速' },
  { value: 'coarse', label: '粗调' },
]

function HardwareConfigCard({
  id,
  focusHash,
  icon,
  title,
  subtitle,
  state,
  badges,
  actions,
  children,
  wide,
}: {
  id: string
  focusHash: string
  icon: ReactNode
  title: string
  subtitle: string
  state: ConnectionState
  badges?: ReactNode
  actions?: ReactNode
  children: ReactNode
  wide?: boolean
}) {
  const focused = focusHash === id
  return (
    <article id={id} className={`hardware-config-card ${wide ? 'hardware-config-card-wide' : ''} ${focused ? 'hardware-config-card-focused' : ''}`}>
      <div className="hardware-config-card-head">
        <div className="hardware-config-title">
          <span className="hardware-config-icon">{icon}</span>
          <div>
            <Typography.Title level={3}>{title}</Typography.Title>
            <Typography.Text type="secondary">{subtitle}</Typography.Text>
          </div>
        </div>
        <Space wrap>
          {badges}
          <Tag color={stateTone(state)}>{stateText(state)}</Tag>
        </Space>
      </div>
      {actions && <div className="hardware-config-actions">{actions}</div>}
      {children}
    </article>
  )
}

function MetricBox({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: 'warn' | 'ok' | 'neutral' }) {
  return (
    <span className={`hardware-metric-box hardware-metric-${tone ?? 'neutral'}`}>
      <small>{label}</small>
      <b>{value}</b>
      {hint && <em>{hint}</em>}
    </span>
  )
}

function HalCard({
  config,
  updateConfig,
  focusHash,
  injectLog,
}: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const [reconnecting, setReconnecting] = useState(false)
  const handleReconnect = async () => {
    setReconnecting(true)
    try {
      await reconnectHal()
      commandLog(injectLog, '[HAL]', 'HAL 重连请求已发送')
    } catch (error) {
      injectLog('ERROR', `HAL 重连失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setReconnecting(false)
    }
  }

  return (
    <HardwareConfigCard
      id="hal"
      focusHash={focusHash}
      icon={<Network size={20} />}
      title="C++ HAL 通信"
      subtitle="Windows HalServer.exe · LTDMC 与 Omega.7 汇聚入口"
      state="ok"
      badges={<Tag color="processing">Real HAL</Tag>}
      actions={
        <Space wrap>
          <Button icon={<RefreshCw size={15} />} loading={reconnecting} onClick={() => void handleReconnect()}>
            重连
          </Button>
        </Space>
      }
      wide
    >
      <Form layout="vertical" className="hardware-form-grid">
        <Form.Item label="HAL HTTP">
          <Input value={config.hal.baseUrl} onChange={(event) => updateConfig({ hal: { ...config.hal, baseUrl: event.target.value } })} />
        </Form.Item>
        <Form.Item label="HAL WebSocket">
          <Input value={config.hal.wsUrl} onChange={(event) => updateConfig({ hal: { ...config.hal, wsUrl: event.target.value } })} />
        </Form.Item>
        <Form.Item label="轴数">
          <InputNumber min={12} max={24} value={config.hal.axisCount} onChange={(value) => updateConfig({ hal: { ...config.hal, axisCount: Number(value ?? 12) } })} />
        </Form.Item>
        <Form.Item label="开机回工作原点">
          <div className="motion-startup-row">
            <Switch
              checked={config.motion.homeOnStartup.enabled}
              checkedChildren="已启用"
              unCheckedChildren="未启用"
              onChange={(checked) =>
                updateConfig({
                  motion: {
                    ...config.motion,
                    homeOnStartup: { ...config.motion.homeOnStartup, enabled: checked, mode: 'work_origin' },
                  },
                })
              }
            />
            <Tag color="processing">{config.motion.homeOnStartup.mode}</Tag>
          </div>
        </Form.Item>
      </Form>
      <div className="hardware-metric-grid">
        <MetricBox label="控制卡初始化" value="dmc_board_init()" hint="无参数，自动发现两张卡" tone="ok" />
        <MetricBox label="位置读取" value={config.motion.positionSource} hint="步进系统读取内部脉冲计数" tone="ok" />
        <MetricBox label="线程约束" value={`${config.motion.motionThreadHz} Hz`} hint="LTDMC DLL 串行化调用" tone="ok" />
        <MetricBox label="禁止路径" value="Python ctypes 直连 DLL" hint="线程安全风险" tone="warn" />
      </div>
    </HardwareConfigCard>
  )
}

function SafetyCard({
  config,
  updateConfig,
  focusHash,
  dangerIndex,
  setDangerOverride,
  acknowledgeSafety,
  injectLog,
}: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  dangerIndex: number
  setDangerOverride: (danger: number | null) => void
  acknowledgeSafety: () => void
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const state: ConnectionState = dangerIndex > 0.85 ? 'error' : dangerIndex > 0.55 ? 'warn' : 'ok'
  return (
    <HardwareConfigCard
      id="safety"
      focusHash={focusHash}
      icon={<ShieldAlert size={20} />}
      title="安全链路 / 急停 / 软限位"
      subtitle="ForceMonitor、HAL 急停、MotionControl 软限位三层保护"
      state={state}
      badges={<Tag color={stateTone(state)}>danger_index {dangerIndex.toFixed(2)}</Tag>}
      actions={
        <Space wrap>
          <Button danger icon={<AlertTriangle size={15} />} onClick={() => setDangerOverride(0.92)}>
            模拟危险
          </Button>
          <Button icon={<RotateCcw size={15} />} onClick={() => { acknowledgeSafety(); commandLog(injectLog, '[SAFETY]', '安全状态复位') }}>
            复位确认
          </Button>
        </Space>
      }
      wide
    >
      <Form layout="vertical" className="hardware-form-grid hardware-form-grid-compact">
        <Form.Item label="Fx/Fy 警告 N"><InputNumber min={0} step={0.1} value={config.safety.fxyWarnN} onChange={(value) => updateConfig({ safety: { ...config.safety, fxyWarnN: Number(value ?? 2) } })} /></Form.Item>
        <Form.Item label="Fx/Fy 急停 N"><InputNumber min={0} step={0.1} value={config.safety.fxyStopN} onChange={(value) => updateConfig({ safety: { ...config.safety, fxyStopN: Number(value ?? 4) } })} /></Form.Item>
        <Form.Item label="Fz 警告 N"><InputNumber min={0} step={0.1} value={config.safety.fzWarnN} onChange={(value) => updateConfig({ safety: { ...config.safety, fzWarnN: Number(value ?? 3) } })} /></Form.Item>
        <Form.Item label="Fz 急停 N"><InputNumber min={0} step={0.1} value={config.safety.fzStopN} onChange={(value) => updateConfig({ safety: { ...config.safety, fzStopN: Number(value ?? 5) } })} /></Form.Item>
        <Form.Item label="Moment 警告 Nm"><InputNumber min={0} step={0.001} value={config.safety.momentWarnNm} onChange={(value) => updateConfig({ safety: { ...config.safety, momentWarnNm: Number(value ?? 0.02) } })} /></Form.Item>
        <Form.Item label="Moment 急停 Nm"><InputNumber min={0} step={0.001} value={config.safety.momentStopNm} onChange={(value) => updateConfig({ safety: { ...config.safety, momentStopNm: Number(value ?? 0.04) } })} /></Form.Item>
        <Form.Item label="Yaw 软限位 °"><InputNumber value={config.safety.yawSoftLimitDeg} onChange={(value) => updateConfig({ safety: { ...config.safety, yawSoftLimitDeg: Number(value ?? 7.5) } })} /></Form.Item>
        <Form.Item label="Watchdog ms"><InputNumber value={config.safety.watchdogMs} onChange={(value) => updateConfig({ safety: { ...config.safety, watchdogMs: Number(value ?? 50) } })} /></Form.Item>
      </Form>
      <div className="safety-layer-grid">
        <MetricBox label="Layer 1" value="ForceMonitor <2ms" hint="NI-DAQmx 采样后直接判断" />
        <MetricBox label="Layer 2" value="HAL 急停 5-15ms" hint="HTTP 调用 LTDMC" />
        <MetricBox label="Layer 3" value="软限位 <1ms" hint="Motion Thread 内拦截" />
      </div>
    </HardwareConfigCard>
  )
}

function AxisMappingTable({
  side,
  positions,
  profile,
  limits,
  onProfileChange,
  onLimitChange,
}: {
  side: RobotSide
  positions: number[]
  profile: ArmMotionProfile
  limits: ArmSoftLimitConfig
  onProfileChange: (nextProfile: ArmMotionProfile) => void
  onLimitChange: (nextLimits: ArmSoftLimitConfig) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const updateProfile = (
    group: keyof ArmMotionProfile,
    field: keyof ArmMotionProfile[keyof ArmMotionProfile],
    value: number,
  ) => {
    onProfileChange({
      ...profile,
      [group]: {
        ...profile[group],
        [field]: value,
      },
    })
  }
  const updateLimit = (axis: keyof ArmSoftLimitConfig, bound: 'min' | 'max', value: number) => {
    onLimitChange({
      ...limits,
      [axis]: {
        ...limits[axis],
        [bound]: value,
      },
    })
  }
  const renderProfileInput = (
    group: keyof ArmMotionProfile,
    field: keyof ArmMotionProfile[keyof ArmMotionProfile],
    step = 1,
  ) => (
    <InputNumber
      className="axis-map-input"
      min={0}
      step={step}
      value={profile[group][field]}
      onChange={(value) => updateProfile(group, field, Number(value ?? 0))}
    />
  )
  return (
    <div className="axis-map-table-wrap">
      <table className="axis-map-table">
        <thead>
          <tr>
            <th>语义轴</th>
            <th>物理轴号</th>
            <th>型号 / 行程</th>
            <th>当前位置</th>
            <th>脉冲当量</th>
            <th>初始速度</th>
            <th>最大速度</th>
            <th>加速时间</th>
            <th>减速时间</th>
            <th>软限位下限</th>
            <th>软限位上限</th>
          </tr>
        </thead>
        <tbody>
          {axisHardwareSpecs.map((axis, index) => {
            const pulse = side === 'left' ? axis.leftPulsePerUnit : axis.rightPulsePerUnit
            const group = index < 3 ? 'translation' : 'rotation'
            const axisKey = softLimitRows[index].key
            const minLimit = displaySoftLimitValue(limits[axisKey].min, index)
            const maxLimit = displaySoftLimitValue(limits[axisKey].max, index)
            return (
              <tr key={axis.axis} className={axis.warning ? 'axis-row-warning' : ''}>
                <td><b>{axis.axis}</b></td>
                <td>axis {sideSpec.axisOrder[index]}</td>
                <td>
                  <b className="axis-model">{axis.model}</b>
                  <span className="axis-travel">{axis.travel}</span>
                </td>
                <td className="numeric-cell">{formatAxisValue(positions[sideSpec.stateOffset + index] ?? 0, index)}</td>
                <td className="numeric-cell">{pulse.toFixed(axis.axis === 'X' || axis.axis === 'Z' ? 4 : 3)}</td>
                <td>{renderProfileInput(group, 'startSpeed', group === 'translation' ? 0.1 : 0.01)}</td>
                <td>{renderProfileInput(group, 'maxSpeed', group === 'translation' ? 0.1 : 0.01)}</td>
                <td>{renderProfileInput(group, 'accTimeSec', 0.01)}</td>
                <td>{renderProfileInput(group, 'decTimeSec', 0.01)}</td>
                <td>
                  <InputNumber
                    className="axis-map-input axis-limit-input"
                    step={index < 3 ? 100 : 0.1}
                    value={minLimit}
                    onChange={(value) => updateLimit(axisKey, 'min', configSoftLimitValue(Number(value ?? 0), index))}
                  />
                </td>
                <td>
                  <span className="axis-limit-field">
                    <InputNumber
                      className="axis-map-input axis-limit-input"
                      step={index < 3 ? 100 : 0.1}
                      value={maxLimit}
                      onChange={(value) => updateLimit(axisKey, 'max', configSoftLimitValue(Number(value ?? 0), index))}
                    />
                    <span className="axis-unit">{axis.unit}</span>
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="axis-map-note">
        <Tag>平移单位 um，速度 um/s</Tag>
        <Tag>旋转界面单位 °，配置存储 mdeg</Tag>
        <Tag>LTDMC profile 使用初始速度、最大速度、加速时间、减速时间</Tag>
        <Tag color="processing">同侧同类型 3 轴共用速度 profile；软限位逐轴独立</Tag>
      </div>
    </div>
  )
}

function MotionCard({
  side,
  config,
  updateConfig,
  focusHash,
  positions,
  motionEnabled,
  motionAxisEnabled,
  injectLog,
  triggerEmergencyStop,
  snapshotMenu,
  openSnapshotModal,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  positions: number[]
  motionEnabled: boolean | null | undefined
  motionAxisEnabled?: Array<boolean | null>
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
  triggerEmergencyStop: () => void
  snapshotMenu: (scope: ParameterSnapshotScope) => MenuProps
  openSnapshotModal: (scope: ParameterSnapshotScope) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const id = `motion-${side}`
  const snapshotScope = motionSnapshotScope(side)
  const configCardNo = side === 'left' ? config.motion.leftCardNo : config.motion.rightCardNo
  const profileKey = side === 'left' ? 'leftProfile' : 'rightProfile'
  const softLimitKey = side === 'left' ? 'leftSoftLimits' : 'rightSoftLimits'
  const updateCardNo = (cardNo: number) =>
    updateConfig({ motion: { ...config.motion, [side === 'left' ? 'leftCardNo' : 'rightCardNo']: cardNo } })
  const updateProfile = (nextProfile: ArmMotionProfile) =>
    updateConfig({ motion: { ...config.motion, [profileKey]: nextProfile } })
  const updateSoftLimits = (nextLimits: ArmSoftLimitConfig) =>
    updateConfig({ motion: { ...config.motion, [softLimitKey]: nextLimits } })
  const motionOrigin = config.motion.origin
  const sideOriginValid = side === 'left' ? motionOrigin.leftValid : motionOrigin.rightValid
  const originStatusText = sideOriginValid ? '已设置' : '未设置'
  const originScopeText =
    sideOriginValid
      ? motionOrigin.valid
        ? '双侧零点都已设置'
        : '仅当前侧零点已设置'
      : '当前侧零点未设置'
  const originUpdatedText = motionOrigin.updatedAt > 0 ? `最后更新 ${formatSnapshotTime(motionOrigin.updatedAt)}` : originScopeText
  const [pendingMotionAction, setPendingMotionAction] = useState<'enable' | 'disable' | 'home' | null>(null)
  const [pendingOriginAction, setPendingOriginAction] = useState<'capture' | 'clear' | null>(null)
  const [optimisticEnabled, setOptimisticEnabled] = useState<boolean | null>(null)
  useEffect(() => {
    if (optimisticEnabled !== null && motionEnabled === optimisticEnabled) {
      const timer = window.setTimeout(() => setOptimisticEnabled(null), 0)
      return () => window.clearTimeout(timer)
    }
    return undefined
  }, [motionEnabled, optimisticEnabled])
  const effectiveEnabled = optimisticEnabled ?? motionEnabled ?? null
  const knownAxisEnabled = motionAxisEnabled?.filter((value) => value !== null && value !== undefined) ?? []
  const partialEnabled = effectiveEnabled !== true && knownAxisEnabled.some((value) => value === true)
  const enableTag =
    effectiveEnabled === true
      ? <Tag color="success">已使能</Tag>
      : partialEnabled
        ? <Tag color="warning">部分使能</Tag>
      : effectiveEnabled === false
        ? <Tag color="warning">未使能</Tag>
        : <Tag color="default">使能状态未知</Tag>
  const handleEnable = async () => {
    setPendingMotionAction('enable')
    try {
      await enableMotionSide(side)
      setOptimisticEnabled(true)
      commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}全部轴使能请求已发送`)
    } catch (error) {
      injectLog('ERROR', `${sideSpec.shortLabel}使能失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingMotionAction(null)
    }
  }
  const handleDisable = async () => {
    setPendingMotionAction('disable')
    try {
      await disableMotionSide(side)
      setOptimisticEnabled(false)
      commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel} motion axes disable requested`)
    } catch (error) {
      injectLog('ERROR', `${sideSpec.shortLabel} motion disable failed: ${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingMotionAction(null)
    }
  }
  const handleHome = () => {
    Modal.confirm({
      title: `${sideSpec.shortLabel}回零`,
      content: '将通过 HAL 调用 LTDMC dmc_home_move，执行前请确认工作区安全。',
      okText: '回零',
      cancelText: '取消',
      onOk: async () => {
        setPendingMotionAction('home')
        try {
          await homeMotionSide(side)
          commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}回零请求已发送`)
        } catch (error) {
          injectLog('ERROR', `${sideSpec.shortLabel}回零失败：${commandErrorMessage(error)}`, '[HAL]')
        } finally {
          setPendingMotionAction(null)
        }
      },
    })
  }
  const handleCaptureOrigin = async () => {
    setPendingOriginAction('capture')
    try {
      const response = await captureMotionOrigin(side)
      const nextOrigin = response.data?.origin ?? {
        ...motionOrigin,
        leftValid: side === 'left' ? true : motionOrigin.leftValid,
        rightValid: side === 'right' ? true : motionOrigin.rightValid,
        valid:
          (side === 'left' ? true : motionOrigin.leftValid) &&
          (side === 'right' ? true : motionOrigin.rightValid),
        updatedAt: Date.now(),
      }
      updateConfig({ motion: { ...config.motion, origin: nextOrigin } })
      commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}采集零点已写入`)
    } catch (error) {
      injectLog('ERROR', `${sideSpec.shortLabel}采集零点失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingOriginAction(null)
    }
  }
  const handleClearOrigin = async () => {
    setPendingOriginAction('clear')
    try {
      const response = await clearMotionOrigin(side)
      const nextOrigin = response.data?.origin ?? {
        ...motionOrigin,
        leftValid: side === 'left' ? false : motionOrigin.leftValid,
        rightValid: side === 'right' ? false : motionOrigin.rightValid,
        valid:
          (side === 'left' ? false : motionOrigin.leftValid) &&
          (side === 'right' ? false : motionOrigin.rightValid),
        leftPulse: side === 'left' ? [0, 0, 0, 0, 0, 0] : motionOrigin.leftPulse,
        rightPulse: side === 'right' ? [0, 0, 0, 0, 0, 0] : motionOrigin.rightPulse,
        updatedAt: Date.now(),
      }
      updateConfig({ motion: { ...config.motion, origin: nextOrigin } })
      commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}采集零点已清除`)
    } catch (error) {
      injectLog('ERROR', `${sideSpec.shortLabel}采集零点清除失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingOriginAction(null)
    }
  }

  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Cpu size={20} />}
      title={`${sideSpec.shortLabel}运动控制卡 · Card ${configCardNo}`}
      subtitle={`LTDMC/DMC3000 · ${sideSpec.configKey} · 6 轴串行控制`}
      state="ok"
      badges={
        <>
          <Tag color="processing">{sideSpec.axisOrder.join(',')}</Tag>
          <Tag color="warning">Yaw ±8°</Tag>
          {enableTag}
        </>
      }
      actions={
        <Space wrap>
          <Button icon={<PlugZap size={15} />} loading={pendingMotionAction === 'enable'} onClick={() => void handleEnable()}>
            使能全部
          </Button>
          <Button danger icon={<Usb size={15} />} loading={pendingMotionAction === 'disable'} onClick={() => void handleDisable()}>
            断使能
          </Button>
          <Button icon={<RotateCcw size={15} />} loading={pendingMotionAction === 'home'} onClick={handleHome}>
            回零
          </Button>
          <Button danger icon={<ShieldAlert size={15} />} onClick={triggerEmergencyStop}>
            急停
          </Button>
        </Space>
      }
      wide
    >
      <div className="motion-card-snapshot-toolbar">
        <Dropdown menu={snapshotMenu(snapshotScope)} trigger={['click']}>
          <Button icon={<RefreshCw size={15} />}>
            选择运动参数
          </Button>
        </Dropdown>
      </div>
      <Form layout="vertical" className="hardware-form-grid hardware-form-grid-compact">
        <Form.Item label="控制卡号">
          <InputNumber min={0} max={8} value={configCardNo} onChange={(value) => updateCardNo(Number(value ?? sideSpec.cardNo))} />
        </Form.Item>
        <Form.Item label="位置源">
          <Select
            value={config.motion.positionSource}
            onChange={(value: AppConfig['motion']['positionSource']) => updateConfig({ motion: { ...config.motion, positionSource: value } })}
            options={[{ value: 'dmc_get_position' }, { value: 'dmc_get_encoder', label: 'dmc_get_encoder（不建议）' }]}
          />
        </Form.Item>
        <Form.Item label="Motion Thread">
          <Tag color="processing">{config.motion.motionThreadHz} Hz</Tag>
        </Form.Item>
        <Form.Item label="线程策略">
          <Tag>串行化 LTDMC 调用</Tag>
        </Form.Item>
        <Form.Item label="Yaw 硬件行程">
          <Tag color="warning">±8° / UI 限 ≤±7.5°</Tag>
        </Form.Item>
        <Form.Item label="参数映射">
          <Tag>表内编辑</Tag>
        </Form.Item>
      </Form>
      <div className="motion-origin-panel">
        <div className="hardware-subtitle-row">
          <b>采集零点</b>
          <span>{originScopeText}</span>
        </div>
        <div className="hardware-metric-grid hardware-metric-grid-single">
          <MetricBox label="当前状态" value={originStatusText} hint={originUpdatedText} tone={sideOriginValid ? 'ok' : 'warn'} />
        </div>
        <Space wrap className="motion-origin-actions">
          <Button
            icon={<Crosshair size={15} />}
            loading={pendingOriginAction === 'capture'}
            onClick={() => void handleCaptureOrigin()}
          >
            设为采集零点
          </Button>
          <Button
            icon={<Trash2 size={15} />}
            loading={pendingOriginAction === 'clear'}
            onClick={() => void handleClearOrigin()}
          >
            清除零点
          </Button>
        </Space>
      </div>
      <AxisMappingTable
        side={side}
        positions={positions}
        profile={config.motion[profileKey]}
        limits={config.motion[softLimitKey]}
        onProfileChange={updateProfile}
        onLimitChange={updateSoftLimits}
      />
      <div className="motion-card-snapshot-footer">
        <Button type="primary" icon={<Save size={15} />} onClick={() => openSnapshotModal(snapshotScope)}>
          保存运动参数
        </Button>
      </div>
    </HardwareConfigCard>
  )
}

function CameraCard({
  cameraKey,
  camera,
  config,
  updateConfig,
  focusHash,
  injectLog,
}: {
  cameraKey: CameraKey
  camera?: CameraTelemetry
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const spec = cameraHardwareSpecs[cameraKey]
  const id = cameraKey === 'global' ? 'camera-global' : cameraKey === 'wrist_left' ? 'camera-left' : 'camera-right'
  const configField = cameraKey === 'global' ? 'global' : cameraKey === 'wrist_left' ? 'wristLeft' : 'wristRight'
  const resolutionField =
    cameraKey === 'global' ? 'globalResolution' : cameraKey === 'wrist_left' ? 'wristLeftResolution' : 'wristRightResolution'
  const previewResolution = config.cameras[resolutionField] ?? config.cameras.previewResolution
  const telemetryState = camera?.health ?? 'pending'
  const [previewHealth, setPreviewHealth] = useState<ConnectionState>('checking')
  const state: ConnectionState = !camera
    ? 'pending'
    : telemetryState === 'error' || previewHealth === 'error'
      ? 'error'
      : telemetryState === 'pending'
        ? 'pending'
        : previewHealth === 'checking' || previewHealth === 'pending'
          ? 'checking'
          : telemetryState === 'warn'
            ? 'warn'
            : 'ok'
  const tuning = config.cameras.tuning?.[cameraKey] ?? defaultCameraTuning[cameraKey]
  const isWristCamera = cameraKey !== 'global'
  const [pendingCameraAction, setPendingCameraAction] = useState<'apply' | 'reconnect' | null>(null)
  const sanitizeTuning = (next: CameraTuningProfile): CameraTuningProfile => {
    const exposure = Math.min(0, Math.max(-13, Number(next.exposure)))
    const wristExposure = isWristCamera ? Math.min(exposure, -5) : exposure
    const gain = Math.min(64, Math.max(0, Number(next.gain)))
    return {
      autoExposure: isWristCamera ? false : Boolean(next.autoExposure),
      exposure: Number.isFinite(wristExposure) ? wristExposure : defaultCameraTuning[cameraKey].exposure,
      gain: Number.isFinite(gain) ? gain : defaultCameraTuning[cameraKey].gain,
      autoWhiteBalance: Boolean(next.autoWhiteBalance),
    }
  }
  const updateTuning = (patch: Partial<CameraTuningProfile>) => {
    const nextTuning = sanitizeTuning({ ...tuning, ...patch })
    updateConfig({
      cameras: {
        ...config.cameras,
        tuning: {
          ...(config.cameras.tuning ?? defaultCameraTuning),
          [cameraKey]: nextTuning,
        },
      },
    })
  }
  const handleApplyTuning = async () => {
    setPendingCameraAction('apply')
    try {
      await applyCameraTuning(cameraKey, {
        ...config,
        cameras: {
          ...config.cameras,
          tuning: {
            ...(config.cameras.tuning ?? defaultCameraTuning),
            [cameraKey]: sanitizeTuning(tuning),
          },
        },
      })
      refreshCameraStream(cameraKey)
      commandLog(injectLog, '[CAMERA]', `${spec.label} camera tuning applied`)
    } catch (error) {
      injectLog('ERROR', `${spec.label} camera tuning failed: ${commandErrorMessage(error)}`, '[CAMERA]')
    } finally {
      setPendingCameraAction(null)
    }
  }
  const handleReconnect = async () => {
    setPendingCameraAction('reconnect')
    try {
      await reconnectCamera(cameraKey)
      refreshCameraStream(cameraKey)
      commandLog(injectLog, '[CAMERA]', `${spec.label} camera reconnect requested`)
    } catch (error) {
      injectLog('ERROR', `${spec.label} camera reconnect failed: ${commandErrorMessage(error)}`, '[CAMERA]')
    } finally {
      setPendingCameraAction(null)
    }
  }

  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Camera size={20} />}
      title={`${spec.label} · ${spec.model}`}
      subtitle={`${previewResolution} @ ${config.cameras.fps}Hz · ${spec.lerobotKey}`}
      state={state}
      badges={<Tag color={stateTone(state)}>{previewResolution}</Tag>}
      actions={
        <Space wrap>
          <Button icon={<Save size={15} />} loading={pendingCameraAction === 'apply'} onClick={() => void handleApplyTuning()}>
            应用参数
          </Button>
          <Button icon={<RefreshCw size={15} />} loading={pendingCameraAction === 'reconnect'} onClick={() => void handleReconnect()}>
            重连预览
          </Button>
        </Space>
      }
    >
      {camera && <CameraPreview camera={camera} compact resolution={previewResolution} onPreviewHealthChange={setPreviewHealth} />}
      <div className="hardware-metric-grid camera-metric-grid">
        <MetricBox label="FPS" value={`${(camera?.fps ?? 0).toFixed(1)} / ${spec.fps}`} />
        <MetricBox label="Frame age" value={`${(camera?.frameAgeMs ?? 0).toFixed(0)} ms`} />
        <MetricBox label="Clock skew" value={`${(camera?.timestampSkewMs ?? 0).toFixed(1)} ms`} tone={Math.abs(camera?.timestampSkewMs ?? 0) > 16 ? 'warn' : 'ok'} />
      </div>
      <Form layout="vertical" className="hardware-form-grid hardware-form-grid-compact">
        <Form.Item label="设备">
          <Input value={config.cameras[configField]} onChange={(event) => updateConfig({ cameras: { ...config.cameras, [configField]: event.target.value } })} />
        </Form.Item>
        <Form.Item label="预览分辨率" tooltip="仅影响前端预览和相机流负载；默认 640x480 足够观察。">
          <Select
            value={previewResolution}
            onChange={(value) => updateConfig({ cameras: { ...config.cameras, [resolutionField]: value } })}
            options={previewResolutionOptions}
          />
        </Form.Item>
        <Form.Item label="相机采集目标 FPS" tooltip="后端相机预览流的目标帧率；数据保存频率在数据存储里的录制 FPS 单独设置。"><InputNumber min={1} max={60} value={config.cameras.fps} onChange={(value) => updateConfig({ cameras: { ...config.cameras, fps: Number(value ?? 30) } })} /></Form.Item>
        <Form.Item label="曝光 / 增益">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Switch
              checked={Boolean(tuning.autoExposure)}
              disabled={isWristCamera}
              checkedChildren="Auto"
              unCheckedChildren="Manual"
              onChange={(checked) => updateTuning({ autoExposure: checked })}
            />
            <Space size={8} wrap>
              <Typography.Text type="secondary">Exposure</Typography.Text>
              <InputNumber
                min={-13}
                max={isWristCamera ? -5 : 0}
                step={0.5}
                value={tuning.exposure}
                onChange={(value) => updateTuning({ exposure: Number(value ?? defaultCameraTuning[cameraKey].exposure) })}
              />
            </Space>
            <Space size={8} wrap>
              <Typography.Text type="secondary">Gain</Typography.Text>
              <InputNumber
                min={0}
                max={64}
                step={1}
                value={tuning.gain}
                onChange={(value) => updateTuning({ gain: Number(value ?? 0) })}
              />
            </Space>
            <Switch
              checked={Boolean(tuning.autoWhiteBalance)}
              checkedChildren="Auto WB"
              unCheckedChildren="Manual WB"
              onChange={(checked) => updateTuning({ autoWhiteBalance: checked })}
            />
          </Space>
        </Form.Item>
      </Form>
    </HardwareConfigCard>
  )
}

function ForceSensorCard({
  side,
  config,
  updateConfig,
  focusHash,
  values,
  history,
  injectLog,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  values: number[]
  history: ReturnType<typeof useTelemetryStore.getState>['history']
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const ipKey = sideSpec.forceIpKey
  const state = forceState(values, config)
  const id = `force-${side}`
  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Waves size={20} />}
      title={`${sideSpec.shortLabel} Nano-17 六维力`}
      subtitle={`${nano17Spec.model} · Fx/Fy/Fz=mN · Mx/My/Mz=mN·m`}
      state={state}
      actions={
        <Space wrap>
          <Button
            icon={<RotateCcw size={15} />}
            onClick={() => {
              void tareForceSensor(side)
              commandLog(injectLog, '[FORCE]', `${sideSpec.shortLabel} Nano-17 Tare`)
            }}
          >
            Tare
          </Button>
          <Button icon={<Download size={15} />} onClick={() => commandLog(injectLog, '[FORCE]', `${sideSpec.shortLabel} 力数据导出`)}>
            CSV
          </Button>
        </Space>
      }
      wide
    >
      <div className="force-settings-layout">
        <div>
          <ForceChart history={history} side={side} height={170} />
          <div className="force-current-grid force-current-grid-settings">
            {forceChannels.map((channel, index) => (
              <span key={channel}>
                <b>{channel}</b>
                {formatForceValue(values[index] ?? 0, index)}
              </span>
            ))}
          </div>
        </div>
        <div>
          <div className="hardware-metric-grid">
            <MetricBox label="Fx/Fy 量程" value={nano17Spec.range.fxy} />
            <MetricBox label="Fz 量程" value={nano17Spec.range.fz} />
            <MetricBox label="Moment 量程" value={nano17Spec.range.moment} />
            <MetricBox label="NI-DAQmx" value="DIFF ai0:5" hint={`${nano17Spec.fastDaqHz}Hz`} />
          </div>
          <Form layout="vertical" className="hardware-form-grid hardware-form-grid-compact">
            <Form.Item label="DAQ 通道">
              <Input value={config.force[ipKey]} onChange={(event) => updateConfig({ force: { ...config.force, [ipKey]: event.target.value } })} />
            </Form.Item>
            <Form.Item label="保留端口"><InputNumber disabled value={config.force.port} onChange={(value) => updateConfig({ force: { ...config.force, port: Number(value ?? 49152) } })} /></Form.Item>
            <Form.Item label="采样模式"><Select defaultValue="nidaqmx" options={[{ value: 'nidaqmx', label: 'NI-DAQmx 200Hz' }, { value: 'rdt', label: 'RDT UDP 备用' }]} /></Form.Item>
            <Form.Item label="采样率 Hz"><InputNumber min={1} value={config.force.sampleHz} onChange={(value) => updateConfig({ force: { ...config.force, sampleHz: Number(value ?? 200) } })} /></Form.Item>
            <Form.Item label="录制窗口样本"><InputNumber min={0} max={512} value={config.force.recordWindowSamples} onChange={(value) => updateConfig({ force: { ...config.force, recordWindowSamples: Number(value ?? 0) } })} /></Form.Item>
            <Form.Item label="Tare 样本"><InputNumber min={0} max={512} value={config.force.tareSamples} onChange={(value) => updateConfig({ force: { ...config.force, tareSamples: Number(value ?? 0) } })} /></Form.Item>
            <Form.Item label="低通滤波">
              <Switch
                checked={config.force.lowpassEnabled}
                checkedChildren="ON"
                unCheckedChildren="OFF"
                onChange={(checked) => updateConfig({ force: { ...config.force, lowpassEnabled: checked } })}
              />
            </Form.Item>
            <Form.Item label="低通截止 Hz"><InputNumber min={0} value={config.force.lowpassCutoffHz} onChange={(value) => updateConfig({ force: { ...config.force, lowpassCutoffHz: Number(value ?? 10) } })} /></Form.Item>
            <Form.Item label="标定证书">
              <Switch
                checked={config.force.certificateConfirmed}
                checkedChildren="已确认"
                unCheckedChildren="待确认"
                onChange={(checked) => updateConfig({ force: { ...config.force, certificateConfirmed: checked } })}
              />
            </Form.Item>
          </Form>
        </div>
      </div>
    </HardwareConfigCard>
  )
}

function GripperCard({
  side,
  config,
  updateConfig,
  focusHash,
  currentMm,
  issueManualGripperMove,
  injectLog,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  currentMm: number
  issueManualGripperMove: (side: RobotSide, command: ManualGripperCommand, targetMm?: number) => void
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const id = `gripper-${side}`
  const portKey = side === 'left' ? 'leftPort' : 'rightPort'
  const targetKey = side === 'left' ? 'targetLeftMm' : 'targetRightMm'
  const slaveKey = side === 'left' ? 'leftSlaveId' : 'rightSlaveId'
  const enabledKey = side === 'left' ? 'leftEnabled' : 'rightEnabled'
  const gripperEnabled = Boolean(config.gripper[enabledKey])
  const gapMinKey = side === 'left' ? 'leftGapMinMm' : 'rightGapMinMm'
  const gapMaxKey = side === 'left' ? 'leftGapMaxMm' : 'rightGapMaxMm'
  const setTarget = (value: number) => updateConfig({ gripper: { ...config.gripper, [targetKey]: value } })
  const setForceFeedback = (checked: boolean) => updateConfig({ gripper: { ...config.gripper, forceFeedbackAvailable: checked } })
  const currentText = formatGripperPosition(currentMm)
  const setTargetAndRun = (label: string, value: number) => {
    setTarget(value)
    issueManualGripperMove(side, 'target', value)
    commandLog(injectLog, '[GRIPPER]', `${sideSpec.shortLabel}夹爪${label}`)
  }
  const gt = config.teleop.gripperTeleop
  const setGt = (patch: Partial<typeof gt>) =>
    updateConfig({ teleop: { ...config.teleop, gripperTeleop: { ...gt, ...patch } } })
  const [teleopRunning, setTeleopRunning] = useState(false)
  const [objectDetected, setObjectDetected] = useState(false)
  useEffect(() => {
    fetchGripperTeleopStatus()
      .then((r: { data?: { running?: boolean; leftObjectDetected?: boolean; rightObjectDetected?: boolean } }) => {
        const d = r?.data ?? {}
        setTeleopRunning(Boolean(d.running))
        setObjectDetected(Boolean(side === 'left' ? d.leftObjectDetected : d.rightObjectDetected))
      })
      .catch(() => {})
    const timer = setInterval(() => {
      fetchGripperTeleopStatus()
        .then((r: { data?: { running?: boolean; leftObjectDetected?: boolean; rightObjectDetected?: boolean } }) => {
          const d = r?.data ?? {}
          setTeleopRunning(Boolean(d.running))
          setObjectDetected(Boolean(side === 'left' ? d.leftObjectDetected : d.rightObjectDetected))
        })
        .catch(() => {})
    }, 1000)
    return () => clearInterval(timer)
  }, [side])
  const handleTeleopToggle = async () => {
    try {
      if (teleopRunning) {
        await stopGripperTeleop()
        setTeleopRunning(false)
      } else {
        await startGripperTeleop()
        setTeleopRunning(true)
      }
    } catch {
      /* ignore */
    }
  }
  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Hand size={20} />}
      title={`${sideSpec.shortLabel}夹爪 · EPG006`}
      subtitle={`RS485 / pyserial · ${config.gripper[portKey]} · 从站 ${config.gripper[slaveKey]}`}
      state={config.gripper[enabledKey] ? 'ok' : 'pending'}
      badges={
        <>
          <Tag color="processing">0-26 mm</Tag>
          <Tag color="warning">力传感待确认</Tag>
        </>
      }
    >
      <div className="gripper-settings-stack">
        <div className="gripper-config-section">
          <div className="hardware-subtitle-row">
            <b>连接参数</b>
            <span>配置保存到后端；下方按钮会通过 RS485 下发夹爪命令</span>
          </div>
          <Form layout="vertical" className="hardware-form-grid hardware-form-grid-compact">
            <Form.Item label="COM 口">
              <Input value={config.gripper[portKey]} onChange={(event) => updateConfig({ gripper: { ...config.gripper, [portKey]: event.target.value } })} />
            </Form.Item>
            <Form.Item label="波特率"><InputNumber value={config.gripper.baudrate} onChange={(value) => updateConfig({ gripper: { ...config.gripper, baudrate: Number(value ?? 115200) } })} /></Form.Item>
            <Form.Item label="从站地址"><InputNumber value={config.gripper[slaveKey]} onChange={(value) => updateConfig({ gripper: { ...config.gripper, [slaveKey]: Number(value ?? sideSpec.gripperSlaveId) } })} /></Form.Item>
            <Form.Item label="行程 mm"><InputNumber value={config.gripper.strokeMm} onChange={(value) => updateConfig({ gripper: { ...config.gripper, strokeMm: Number(value ?? 26) } })} /></Form.Item>
            <Form.Item label="命令力限制 N"><InputNumber min={0} max={8} value={config.gripper.commandForceLimitN} onChange={(value) => updateConfig({ gripper: { ...config.gripper, commandForceLimitN: Number(value ?? 8) } })} /></Form.Item>
            <Form.Item label="采样模式">
              <Segmented<AppConfig['gripper']['sampleMode']>
                value={config.gripper.sampleMode}
                onChange={(value) => updateConfig({ gripper: { ...config.gripper, sampleMode: value } })}
                options={[
                  { value: 'direct', label: '直连' },
                  { value: 'dual_worker', label: '双 worker' },
                ]}
              />
            </Form.Item>
            <Form.Item label="采样 Hz">
              <InputNumber min={1} max={60} value={config.gripper.sampleHz} onChange={(value) => updateConfig({ gripper: { ...config.gripper, sampleHz: Number(value ?? 30) } })} />
            </Form.Item>
            <Form.Item label="力反馈传感">
              <Switch checked={config.gripper.forceFeedbackAvailable} checkedChildren="已接入" unCheckedChildren="待确认" onChange={setForceFeedback} />
            </Form.Item>
          </Form>
        </div>
        <div className="gripper-status-section">
          <div className="hardware-metric-grid gripper-metric-grid">
            <MetricBox label="使能状态" value={config.gripper[enabledKey] ? '已使能' : '未使能'} tone={config.gripper[enabledKey] ? 'ok' : 'warn'} />
            <MetricBox label="当前开合" value={currentText} />
            <MetricBox label="目标开合" value={`${config.gripper[targetKey].toFixed(1)} mm`} />
            <MetricBox label="Omega.7 映射" value="0-25 mm" hint="夹持角 0-0.45 rad" />
            <MetricBox label="夹持力/力矩反馈" value="手册未给出" hint="EPG006 章节仅确认位置接口" tone="warn" />
            <MetricBox label="命令侧力限制" value={`≤ ${config.gripper.commandForceLimitN.toFixed(1)} N`} hint="Omega.7 gripper force 输出上限" />
          </div>
          <Slider min={0} max={config.gripper.strokeMm} step={0.1} value={config.gripper[targetKey]} onChange={(value) => setTarget(Number(value))} />
        </div>
        <div className="gripper-action-section">
          <Button
            type={gripperEnabled ? 'default' : 'primary'}
            icon={<PlugZap size={15} />}
            onClick={() => issueManualGripperMove(side, gripperEnabled ? 'disable' : 'enable')}
          >
            {gripperEnabled ? '断使能' : '使能'}
          </Button>
          <Button disabled={!gripperEnabled} onClick={() => issueManualGripperMove(side, 'target', config.gripper[targetKey])}>执行目标</Button>
          <Button disabled={!gripperEnabled} onClick={() => issueManualGripperMove(side, 'open')}>打开</Button>
          <Button disabled={!gripperEnabled} onClick={() => issueManualGripperMove(side, 'close')}>闭合</Button>
          <Button disabled={!gripperEnabled} icon={<RotateCcw size={15} />} onClick={() => setTargetAndRun('回零', 0)}>
            回零
          </Button>
          <Button icon={<Square size={15} />} onClick={() => issueManualGripperMove(side, 'stop')}>
            停止
          </Button>
        </div>
        <div className="gripper-config-section">
          <div className="hardware-subtitle-row">
            <b>Omega7 夹爪遥操作</b>
            <Space size={6}>
              {objectDetected && <Tag color="success">已夹持物体</Tag>}
              <Button
                type={teleopRunning ? 'primary' : 'default'}
                size="small"
                onClick={handleTeleopToggle}
              >
                {teleopRunning ? '停止遥操' : '启动遥操'}
              </Button>
            </Space>
          </div>
          <Form layout="vertical" className="hardware-form-grid hardware-form-grid-compact">
            <Form.Item label="Gap 最小 mm (夹紧)">
              <InputNumber value={gt[gapMinKey]} onChange={(v) => setGt({ [gapMinKey]: Number(v ?? 0) })} />
            </Form.Item>
            <Form.Item label="Gap 最大 mm (张开)">
              <InputNumber value={gt[gapMaxKey]} onChange={(v) => setGt({ [gapMaxKey]: Number(v ?? 50) })} />
            </Form.Item>
            <Form.Item label="开阈值 (0-1)">
              <InputNumber min={0} max={1} step={0.05} value={gt.openThreshold} onChange={(v) => setGt({ openThreshold: Number(v ?? 0.3) })} />
            </Form.Item>
            <Form.Item label="闭阈值 (0-1)">
              <InputNumber min={0} max={1} step={0.05} value={gt.closeThreshold} onChange={(v) => setGt({ closeThreshold: Number(v ?? 0.7) })} />
            </Form.Item>
            <Form.Item label="夹持速度">
              <InputNumber min={1} max={255} value={gt.gripSpeed} onChange={(v) => setGt({ gripSpeed: Number(v ?? 128) })} />
            </Form.Item>
            <Form.Item label="夹持力矩">
              <InputNumber min={1} max={255} value={gt.gripTorque} onChange={(v) => setGt({ gripTorque: Number(v ?? 192) })} />
            </Form.Item>
            <Form.Item label="释放速度">
              <InputNumber min={1} max={255} value={gt.releaseSpeed} onChange={(v) => setGt({ releaseSpeed: Number(v ?? 255) })} />
            </Form.Item>
            <Form.Item label="释放力矩">
              <InputNumber min={1} max={255} value={gt.releaseTorque} onChange={(v) => setGt({ releaseTorque: Number(v ?? 64) })} />
            </Form.Item>
            <Form.Item label="诊断日志">
              <Switch checked={gt.diagLog} checkedChildren="开" unCheckedChildren="关" onChange={(v) => setGt({ diagLog: v })} />
            </Form.Item>
          </Form>
        </div>
      </div>
    </HardwareConfigCard>
  )
}

function PicoVisionCard({
  config,
  updateConfig,
  focusHash,
  injectLog,
}: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const updatePico = (patch: Partial<AppConfig['picoVision']>) => updateConfig({ picoVision: { ...config.picoVision, ...patch } })
  return (
    <HardwareConfigCard
      id="teleop"
      focusHash={focusHash}
      icon={<Camera size={20} />}
      title="PICO-4 视觉推流"
      subtitle="将上位机相机画面编码后通过 ADB/TCP 链路推送到 PICO-4 显示"
      state="pending"
      badges={<Tag color="warning">ADB 待连接</Tag>}
      actions={
        <Space wrap>
          <Button icon={<Usb size={15} />} onClick={() => commandLog(injectLog, '[CAMERA]', `adb connect ${config.picoVision.ip}:${config.picoVision.adbPort}`)}>
            连接无线 ADB
          </Button>
          <Button type="primary" icon={<Play size={15} />} onClick={() => commandLog(injectLog, '[CAMERA]', `启动 PICO-4 视觉推流 ${config.picoVision.ip}:${config.picoVision.videoPort}`)}>
            启动视觉
          </Button>
          <Button icon={<Square size={15} />} onClick={() => commandLog(injectLog, '[CAMERA]', '停止 PICO-4 视觉推流')}>
            停止视觉
          </Button>
          <Button icon={<RefreshCw size={15} />} onClick={() => commandLog(injectLog, '[CAMERA]', `检查 PICO-4 状态 ${config.picoVision.ip}:${config.picoVision.adbPort}`)}>
            检查状态
          </Button>
        </Space>
      }
      wide
    >
      <div className="hardware-metric-grid">
        <MetricBox label="ADB 端点" value={`${config.picoVision.ip}:${config.picoVision.adbPort}`} />
        <MetricBox label="视频端口" value={config.picoVision.videoPort} hint="PICO 侧 H.264 接收" />
        <MetricBox label="命令端口" value={config.picoVision.commandPort} hint="PC sender 等待控制连接" />
        <MetricBox label="画面源" value={cameraHardwareSpecs[config.picoVision.cameraSource].label} hint={cameraHardwareSpecs[config.picoVision.cameraSource].model} />
      </div>
      <Form layout="vertical" className="hardware-form-grid pico-vision-form">
        <Form.Item label="PICO IP">
          <Input value={config.picoVision.ip} onChange={(event) => updatePico({ ip: event.target.value })} />
        </Form.Item>
        <Form.Item label="ADB 端口">
          <InputNumber min={1} max={65535} value={config.picoVision.adbPort} onChange={(value) => updatePico({ adbPort: Number(value ?? 5555) })} />
        </Form.Item>
        <Form.Item label="视频端口">
          <InputNumber min={1} max={65535} value={config.picoVision.videoPort} onChange={(value) => updatePico({ videoPort: Number(value ?? 12345) })} />
        </Form.Item>
        <Form.Item label="命令端口">
          <InputNumber min={1} max={65535} value={config.picoVision.commandPort} onChange={(value) => updatePico({ commandPort: Number(value ?? 13579) })} />
        </Form.Item>
        <Form.Item label="网关">
          <Input value={config.picoVision.gateway} onChange={(event) => updatePico({ gateway: event.target.value })} />
        </Form.Item>
        <Form.Item label="网卡 IF">
          <InputNumber min={0} value={config.picoVision.ifIndex} onChange={(value) => updatePico({ ifIndex: Number(value ?? 13) })} />
        </Form.Item>
        <Form.Item label="画面旋转">
          <Select
            value={config.picoVision.rotation}
            options={[
              { value: 'ccw90', label: '逆时针 90°' },
              { value: 'cw90', label: '顺时针 90°' },
              { value: '180', label: '旋转 180°' },
              { value: 'none', label: '不旋转' },
            ]}
            onChange={(value) => updatePico({ rotation: value })}
          />
        </Form.Item>
        <Form.Item label="相机源">
          <Select
            value={config.picoVision.cameraSource}
            options={[
              { value: 'global', label: '全局相机' },
              { value: 'wrist_left', label: '左腕相机' },
              { value: 'wrist_right', label: '右腕相机' },
            ]}
            onChange={(value) => updatePico({ cameraSource: value })}
          />
        </Form.Item>
      </Form>
    </HardwareConfigCard>
  )
}

function StorageCard({
  config,
  updateConfig,
  focusHash,
}: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
}) {
  const updateStorage = (patch: Partial<AppConfig['storage']>) => updateConfig({ storage: { ...config.storage, ...patch } })
  const recordFps = config.storage.recordFps ?? config.cameras.fps
  return (
    <HardwareConfigCard
      id="storage"
      focusHash={focusHash}
      icon={<FolderOpen size={20} />}
      title="数据存储"
      subtitle="录制完成的数据集写入目录"
      state="ok"
      badges={<Tag color="processing">Dataset Root</Tag>}
      wide
    >
      <div className="hardware-metric-grid">
        <MetricBox label="当前目录" value={config.storage.datasetRoot} hint="支持绝对路径或 ~ 用户目录" />
        <MetricBox label="录制 FPS" value={recordFps} hint="数据集保存帧率" />
        <MetricBox label="视频 CRF" value={config.storage.videoCrf} />
        <MetricBox label="Hub 上传" value={config.storage.pushToHub ? '启用' : '关闭'} />
      </div>
      <Form layout="vertical" className="hardware-form-grid">
        <Form.Item label="数据集根目录">
          <Input
            value={config.storage.datasetRoot}
            placeholder="C:/Users/Administrator/.appstation/datasets"
            onChange={(event) => updateStorage({ datasetRoot: event.target.value })}
          />
        </Form.Item>
        <Form.Item label="录制 FPS" tooltip="数据集保存帧率；未设置时后端旧逻辑会回退到相机采集目标 FPS。">
          <InputNumber min={1} max={60} value={recordFps} onChange={(value) => updateStorage({ recordFps: Number(value ?? 30) })} />
        </Form.Item>
      </Form>
    </HardwareConfigCard>
  )
}

function TeleopHandCard({
  side,
  config,
  updateConfig,
  focusHash,
  frame,
  injectLog,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  frame: TelemetryFrame
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const id = `teleop-${side}`
  const handState = frame.teleopHands.find((item) => item.side === side)
  const logicalConnected = side === 'left' ? config.teleop.leftConnected : config.teleop.rightConnected
  const connected = logicalConnected && Boolean(handState?.connected)
  const openId = side === 'left' ? config.teleop.leftOpenId : config.teleop.rightOpenId
  const translationScale = side === 'left' ? config.teleop.leftTranslationScale : config.teleop.rightTranslationScale
  const rotationScale = side === 'left' ? config.teleop.leftRotationScale : config.teleop.rightRotationScale
  const gravityCompensation = side === 'left' ? config.teleop.leftGravityCompensation : config.teleop.rightGravityCompensation
  const forceFeedback = side === 'left' ? config.teleop.leftForceFeedback : config.teleop.rightForceFeedback
  const updateTeleop = (patch: Partial<AppConfig['teleop']>) => updateConfig({ teleop: { ...config.teleop, ...patch } })
  const setConnected = (value: boolean) => updateTeleop(side === 'left' ? { leftConnected: value } : { rightConnected: value })
  const setOpenId = (value: number) => updateTeleop(side === 'left' ? { leftOpenId: value } : { rightOpenId: value })
  const setTranslationScale = (value: number) => updateTeleop(side === 'left' ? { leftTranslationScale: value } : { rightTranslationScale: value })
  const setRotationScale = (value: number) => updateTeleop(side === 'left' ? { leftRotationScale: value } : { rightRotationScale: value })
  const setGravityCompensation = (value: boolean) => updateTeleop(side === 'left' ? { leftGravityCompensation: value } : { rightGravityCompensation: value })
  const setForceFeedback = (value: boolean) => updateTeleop(side === 'left' ? { leftForceFeedback: value } : { rightForceFeedback: value })
  const axisOutputScale = side === 'left' ? config.teleop.leftAxisOutputScale : config.teleop.rightAxisOutputScale
  const enabledAxes = side === 'left' ? config.teleop.leftEnabledAxes : config.teleop.rightEnabledAxes
  const setAxisOutputScale = (axisIndex: number, value: number) => {
    const next = [...axisOutputScale]
    next[axisIndex] = value
    updateTeleop(side === 'left' ? { leftAxisOutputScale: next } : { rightAxisOutputScale: next })
  }
  const setEnabledAxis = (axisIndex: number, value: boolean) => {
    const next = [...enabledAxes]
    next[axisIndex] = value
    updateTeleop(side === 'left' ? { leftEnabledAxes: next } : { rightEnabledAxes: next })
  }
  const pose = logicalConnected ? (handState?.pose ?? [0, 0, 0, 0, 0, 0]) : [0, 0, 0, 0, 0, 0]
  const positionMm = pose.slice(0, 3).map((value) => value * 1000)
  const rotationDeg = pose.slice(3, 6)
  const readState = !logicalConnected ? 'pending' : connected && handState?.lastReadOk ? 'ok' : 'warn'
  const [connectionPending, setConnectionPending] = useState(false)
  const toggleConnection = () => {
    setConnectionPending(true)
    if (logicalConnected) {
      commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel} Omega.7 logical disconnect`)
      void disconnectTeleopHand(side)
        .then(() => setConnected(false))
        .catch((error) => injectLog('ERROR', `${sideSpec.shortLabel} Omega.7 disconnect failed: ${String(error)}`, '[HAL]'))
        .finally(() => setConnectionPending(false))
      return
    }
    commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel} Omega.7 connect dhdOpenID(${openId})`)
    void connectTeleopHand(side)
      .then((result) => {
        const payload = result as { data?: { connected?: boolean; message?: string } }
        const nextConnected = payload.data?.connected ?? true
        setConnected(nextConnected)
        if (!nextConnected && payload.data?.message) {
          injectLog('WARNING', `${sideSpec.shortLabel} Omega.7 connect rejected: ${payload.data.message}`, '[HAL]')
        }
      })
      .catch((error) => injectLog('ERROR', `${sideSpec.shortLabel} Omega.7 connect failed: ${String(error)}`, '[HAL]'))
      .finally(() => setConnectionPending(false))
  }
  const setGravityEnabled = (enabled: boolean) => {
    setGravityCompensation(enabled)
    void setTeleopGravityCompensation(side, enabled).catch((error) =>
      injectLog('ERROR', `${sideSpec.shortLabel} gravity compensation command failed: ${String(error)}`, '[HAL]'),
    )
    commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}主手重力补偿${enabled ? '启用' : '关闭'}`)
  }
  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Gamepad2 size={20} />}
      title={`${sideSpec.shortLabel} Omega.7 主手`}
      subtitle={`dhdOpenID(${openId}) · Force Dimension SDK / USB 直连`}
      state={readState}
      badges={
        <Space size={6} wrap>
          <Tag color={connected ? 'success' : logicalConnected ? 'warning' : 'default'}>{connected ? '已连接' : logicalConnected ? '连接异常' : '未连接'}</Tag>
          <Tag color={connected && handState?.lastReadOk ? 'success' : 'warning'}>{connected && handState?.lastReadOk ? '读数正常' : logicalConnected ? '未收到读数' : '逻辑断开'}</Tag>
          <Tag>SN {handState?.serial || '-'}</Tag>
          <Tag>建议固定 USB 口顺序</Tag>
        </Space>
      }
      actions={
        <Space wrap>
          <Button
            type={logicalConnected ? 'default' : 'primary'}
            danger={logicalConnected}
            icon={<Usb size={15} />}
            onClick={toggleConnection}
            loading={connectionPending}
          >
            {logicalConnected ? '断开主手' : '连接主手'}
          </Button>
          <Button icon={<PlugZap size={15} />} onClick={() => setGravityEnabled(!gravityCompensation)}>
            重力补偿
          </Button>
          <Button icon={<ShieldAlert size={15} />} onClick={() => {
            void zeroTeleopForceFeedback(side).catch((error) =>
              injectLog('ERROR', `${sideSpec.shortLabel} zero force feedback failed: ${String(error)}`, '[HAL]'),
            )
            commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}主手清零力反馈`)
          }}>
            清零力反馈
          </Button>
        </Space>
      }
    >
      <div className="hardware-metric-grid">
        <MetricBox label="X / Y / Z" value={`${positionMm[0].toFixed(1)}, ${positionMm[1].toFixed(1)}, ${positionMm[2].toFixed(1)} mm`} />
        <MetricBox label="Roll / Pitch / Yaw" value={`${rotationDeg[0].toFixed(2)}, ${rotationDeg[1].toFixed(2)}, ${rotationDeg[2].toFixed(2)}°`} hint={`旋转比例 ${rotationScale}`} tone={handState?.lastReadOk ? 'ok' : 'warn'} />
        <MetricBox label="按钮 0 / 1" value={`${handState?.clutchPressed ? '按下' : '释放'} / ${handState?.gripperPressed ? '按下' : '释放'}`} />
        <MetricBox label="设备" value={`id ${handState?.deviceId ?? -1} · ${handState?.systemName || 'Omega.7'}`} hint={handState?.message || undefined} tone={handState?.message ? 'warn' : 'ok'} />
        <MetricBox label="夹爪间隙" value={handState?.gripperGapMm == null ? '-' : `${handState.gripperGapMm.toFixed(1)} mm`} />
        <MetricBox label="左右手属性" value={handState?.leftHanded == null ? '-' : handState.leftHanded ? 'Left-handed' : 'Right-handed'} />
      </div>
      <Form layout="vertical" className="hardware-form-grid teleop-hand-form">
        <Form.Item label="OpenID">
          <InputNumber min={0} value={openId} onChange={(value) => setOpenId(Number(value ?? sideSpec.omegaDeviceId))} />
        </Form.Item>
        <Form.Item label="命令更新周期 ms">
          <InputNumber min={1} value={config.teleop.commandIntervalMs} onChange={(value) => updateTeleop({ commandIntervalMs: Number(value ?? 10) })} />
        </Form.Item>
        <Form.Item label="平移单步上限 um">
          <InputNumber
            min={1}
            step={100}
            value={config.teleop.translationStepUm}
            onChange={(value) => updateTeleop({ translationStepUm: Number(value ?? 5000) })}
          />
        </Form.Item>
        <Form.Item label="旋转单步上限 °">
          <InputNumber
            min={0.001}
            step={0.01}
            value={config.teleop.rotationStepDeg}
            onChange={(value) => updateTeleop({ rotationStepDeg: Number(value ?? 0.2) })}
          />
        </Form.Item>
        <Form.Item label="稳定模式">
          <Select
            value={config.teleop.stabilityMode}
            options={[
              { value: 'free', label: 'Off / Free' },
            ]}
            onChange={(value) => updateTeleop({ stabilityMode: value })}
          />
        </Form.Item>
        <Form.Item label="平移比例">
          <InputNumber min={0} step={0.01} value={translationScale} onChange={(value) => setTranslationScale(Number(value ?? 0.3))} />
        </Form.Item>
        <Form.Item label="旋转比例">
          <InputNumber min={0} step={0.01} value={rotationScale} onChange={(value) => setRotationScale(Number(value ?? 0.18))} />
        </Form.Item>
        <Form.Item label="Translation step pulse">
          <InputNumber min={1} step={100} value={config.teleop.translationStepLimitPulse} onChange={(value) => updateTeleop({ translationStepLimitPulse: Number(value ?? 4000) })} />
        </Form.Item>
        <Form.Item label="Rotation step pulse">
          <InputNumber min={1} step={50} value={config.teleop.rotationStepLimitPulse} onChange={(value) => updateTeleop({ rotationStepLimitPulse: Number(value ?? 1250) })} />
        </Form.Item>
        <Form.Item label="平移死区">
          <InputNumber min={0} step={0.00001} value={config.teleop.translationDeadzone} onChange={(value) => updateTeleop({ translationDeadzone: Number(value ?? 0) })} />
        </Form.Item>
        <Form.Item label="旋转死区 °">
          <InputNumber min={0} step={0.01} value={config.teleop.rotationDeadzone} onChange={(value) => updateTeleop({ rotationDeadzone: Number(value ?? 0.08) })} />
        </Form.Item>
        <Form.Item label="Translation min delta">
          <InputNumber min={0} step={0.00001} value={config.teleop.incrementalTranslationMinEffectiveDelta} onChange={(value) => updateTeleop({ incrementalTranslationMinEffectiveDelta: Number(value ?? 0.00005) })} />
        </Form.Item>
        <Form.Item label="Reverse deadzone">
          <InputNumber min={0} step={0.00001} value={config.teleop.incrementalTranslationReverseDeadzone} onChange={(value) => updateTeleop({ incrementalTranslationReverseDeadzone: Number(value ?? 0.0001) })} />
        </Form.Item>
        <Form.Item label="Translation speed um/s">
          <Space.Compact>
            <InputNumber min={0} value={config.teleop.translationStartVelocityUmS} onChange={(value) => updateTeleop({ translationStartVelocityUmS: Number(value ?? 300) })} />
            <InputNumber min={1} value={config.teleop.translationMaxVelocityUmS} onChange={(value) => updateTeleop({ translationMaxVelocityUmS: Number(value ?? 4000) })} />
          </Space.Compact>
        </Form.Item>
        <Form.Item label="Rotation speed deg/s">
          <Space.Compact>
            <InputNumber min={0} step={0.05} value={config.teleop.rotationStartVelocityDegS} onChange={(value) => updateTeleop({ rotationStartVelocityDegS: Number(value ?? 0.25) })} />
            <InputNumber min={1} step={0.1} value={config.teleop.rotationMaxVelocityDegS} onChange={(value) => updateTeleop({ rotationMaxVelocityDegS: Number(value ?? 3) })} />
          </Space.Compact>
        </Form.Item>
        <Form.Item label="Profile acc/dec s">
          <Space.Compact>
            <InputNumber min={0.001} step={0.01} value={config.teleop.motionProfileAccSec} onChange={(value) => updateTeleop({ motionProfileAccSec: Number(value ?? 0.05) })} />
            <InputNumber min={0.001} step={0.01} value={config.teleop.motionProfileDecSec} onChange={(value) => updateTeleop({ motionProfileDecSec: Number(value ?? 0.05) })} />
          </Space.Compact>
        </Form.Item>
      </Form>
      <div className="teleop-switch-row">
        {semanticAxes.map((axis, axisIndex) => (
          <span key={axis}>
            <small>{axis}</small>
            <InputNumber min={0} step={0.05} value={axisOutputScale[axisIndex] ?? 1} onChange={(value) => setAxisOutputScale(axisIndex, Number(value ?? 1))} />
            <Switch checked={enabledAxes[axisIndex] ?? true} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => setEnabledAxis(axisIndex, value)} />
          </span>
        ))}
      </div>
      <div className="teleop-switch-row">
        <span>
          <small>Swap hands</small>
          <Switch checked={config.teleop.swapHands} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => updateTeleop({ swapHands: value })} />
        </span>
        <span>
          <small>重力补偿</small>
          <Switch checked={gravityCompensation} checkedChildren="开" unCheckedChildren="关" onChange={setGravityEnabled} />
        </span>
        <span>
          <small>力反馈使能</small>
          <Switch checked={forceFeedback} checkedChildren="开" unCheckedChildren="关" onChange={setForceFeedback} />
        </span>
        <span>
          <small>Require clutch</small>
          <Switch checked={config.teleop.requireClutch} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => updateTeleop({ requireClutch: value })} />
        </span>
        <span>
          <small>TCP fallback</small>
          <InputNumber min={1} max={65535} value={config.teleop.tcpFallbackPort} onChange={(value) => updateTeleop({ tcpFallbackPort: Number(value ?? 12345) })} />
        </span>
      </div>
    </HardwareConfigCard>
  )
}

function manualAxisUnit(axis: ManualControlAxis) {
  return manualAxisOrder.indexOf(axis) < 3 ? 'um' : '°'
}

function manualAxisSoftKey(axis: ManualControlAxis): keyof ArmSoftLimitConfig {
  return axis === 'X' ? 'x' : axis === 'Y' ? 'y' : axis === 'Z' ? 'z' : axis === 'Roll' ? 'roll' : axis === 'Pitch' ? 'pitch' : 'yaw'
}

function formatManualAction(action: ManualControlAction) {
  if (action.type === 'arm-axis') {
    const side = action.side === 'left' ? '左臂' : '右臂'
    return `${side} ${action.axis} ${action.delta >= 0 ? '+' : ''}${action.delta.toFixed(action.unit === 'um' ? 1 : 3)}${action.unit}`
  }
  const side = action.side === 'left' ? '左夹爪' : '右夹爪'
  const commandText: Record<ManualGripperCommand, string> = {
    enable: '使能',
    disable: '断使能',
    open: '打开',
    close: '闭合',
    home: '回零',
    target: `目标 ${action.targetMm.toFixed(1)}mm`,
    stop: '停止',
  }
  return `${side} ${commandText[action.command]}`
}

function ManualArmControl({
  side,
  positions,
  config,
  manualControl,
  nowMs,
  motionEnabled,
  motionAxisEnabled,
  selectManualAxis,
  setManualAxisStep,
  setManualSpeedMode,
  issueManualAxisMove,
  triggerEmergencyStop,
  injectLog,
}: {
  side: RobotSide
  positions: number[]
  config: AppConfig
  manualControl: ManualControlState
  nowMs: number
  motionEnabled: boolean | null | undefined
  motionAxisEnabled?: Array<boolean | null>
  selectManualAxis: (side: RobotSide, axis: ManualControlAxis) => void
  setManualAxisStep: (unit: 'um' | '°', value: number) => void
  setManualSpeedMode: (mode: ManualSpeedMode) => void
  issueManualAxisMove: (side: RobotSide, axis: ManualControlAxis, direction: -1 | 1) => void
  triggerEmergencyStop: () => void
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const selectedAxis = manualControl.selectedSide === side ? manualControl.selectedAxis : 'X'
  const axisIndex = manualAxisOrder.indexOf(selectedAxis)
  const axisKey = manualAxisSoftKey(selectedAxis)
  const unit = manualAxisUnit(selectedAxis)
  const position = positions[sideSpec.stateOffset + axisIndex] ?? 0
  const limits = side === 'left' ? config.motion.leftSoftLimits[axisKey] : config.motion.rightSoftLimits[axisKey]
  const displayLimits = {
    min: displaySoftLimitValue(limits.min, axisIndex),
    max: displaySoftLimitValue(limits.max, axisIndex),
  }
  const stepValue = unit === 'um' ? manualControl.axisStepUm : manualControl.axisStepDeg
  const softMargin = Math.min(Math.abs(position - displayLimits.min), Math.abs(displayLimits.max - position))
  const profile = side === 'left' ? config.motion.leftProfile : config.motion.rightProfile
  const group = axisIndex < 3 ? profile.translation : profile.rotation
  const busyKey = `${side}-${selectedAxis}`
  const busyUntil = manualControl.axisBusyUntil[busyKey] ?? 0
  const axisBusy = busyUntil > nowMs
  const busyText = `${Math.max(0, (busyUntil - nowMs) / 1000).toFixed(1)}s`
  const speedUnit = axisIndex < 3 ? 'um/s' : '°/s'
  const originValid = side === 'left' ? config.motion.origin.leftValid : config.motion.origin.rightValid
  const originHint = originValid ? '相对采集零点' : '未设置零点，显示 HAL 绝对位置'
  const [pendingMotionAction, setPendingMotionAction] = useState<'enable' | 'disable' | 'stop' | null>(null)
  const [optimisticEnabled, setOptimisticEnabled] = useState<boolean | null>(null)
  useEffect(() => {
    if (optimisticEnabled !== null && motionEnabled === optimisticEnabled) {
      const timer = window.setTimeout(() => setOptimisticEnabled(null), 0)
      return () => window.clearTimeout(timer)
    }
    return undefined
  }, [motionEnabled, optimisticEnabled])
  const effectiveMotionEnabled = optimisticEnabled ?? motionEnabled ?? null
  const selectedAxisEnabled = motionAxisEnabled?.[axisIndex] ?? effectiveMotionEnabled
  const knownAxisEnabled = motionAxisEnabled?.filter((value) => value !== null && value !== undefined) ?? []
  const partialMotionEnabled = effectiveMotionEnabled !== true && knownAxisEnabled.some((value) => value === true)
  const motionReady = mockMode || selectedAxisEnabled !== false
  const nextMotionAction = effectiveMotionEnabled === true ? 'disable' : 'enable'
  const toggleMotionEnabled = async () => {
    setPendingMotionAction(nextMotionAction)
    try {
      if (nextMotionAction === 'enable') {
        await enableMotionSide(side)
      } else {
        await disableMotionSide(side)
      }
      setOptimisticEnabled(nextMotionAction === 'enable')
      injectLog(
        'INFO',
        `${sideSpec.shortLabel} manual ${nextMotionAction === 'enable' ? 'enable' : 'disable'} requested`,
        '[HAL]',
      )
    } catch (error) {
      injectLog(
        'ERROR',
        `${sideSpec.shortLabel} manual ${nextMotionAction === 'enable' ? 'enable' : 'disable'} failed: ${String(error)}`,
        '[HAL]',
      )
    } finally {
      setPendingMotionAction(null)
    }
  }
  const stopMotion = async () => {
    setPendingMotionAction('stop')
    try {
      await stopMotionSide(side)
      injectLog('WARNING', `${sideSpec.shortLabel} manual stop requested`, '[HAL]')
    } catch (error) {
      injectLog('ERROR', `${sideSpec.shortLabel} manual stop failed: ${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingMotionAction(null)
    }
  }

  return (
    <article className={`manual-arm-card ${manualControl.selectedSide === side ? 'manual-card-active' : ''}`}>
      <div className="manual-card-head">
        <div>
          <Typography.Title level={3}>{sideSpec.shortLabel}手动控制</Typography.Title>
          <Typography.Text type="secondary">Card {side === 'left' ? config.motion.leftCardNo : config.motion.rightCardNo} · {sideSpec.axisOrder.join(' / ')}</Typography.Text>
        </div>
        <Space wrap>
          <Tag color={effectiveMotionEnabled === true ? 'success' : effectiveMotionEnabled === false || partialMotionEnabled ? 'warning' : 'default'}>
            {effectiveMotionEnabled === true ? '已使能' : partialMotionEnabled ? '部分使能' : effectiveMotionEnabled === false ? '未使能' : '使能未知'}
          </Tag>
          <Tag color={originValid ? 'success' : 'warning'}>{originValid ? '零点已设置' : '零点未设置'}</Tag>
          <Tag>检测通过</Tag>
          <Tag color="processing">{manualControl.speedMode}</Tag>
        </Space>
      </div>

      <div className="manual-arm-layout">
        <div className={`manual-axis-visual manual-axis-${axisKey}`}>
          <div className="manual-axis-rails">
            <span className="axis-rail axis-rail-x" />
            <span className="axis-rail axis-rail-y" />
            <span className="axis-rail axis-rail-z" />
            <span className="axis-wrist-ring" />
          </div>
          <div className="manual-axis-chip-grid">
            {manualAxisOrder.map((axis) => {
              const active = manualControl.selectedSide === side && manualControl.selectedAxis === axis
              return (
                <Button key={axis} type={active ? 'primary' : 'default'} onClick={() => selectManualAxis(side, axis)}>
                  {axis}
                </Button>
              )
            })}
          </div>
        </div>

        <div className="manual-axis-controls">
          <div className="manual-readout-row">
            <MetricBox label="当前轴" value={selectedAxis} />
            <MetricBox label="当前位置" value={`${position.toFixed(unit === 'um' ? 1 : 3)} ${unit}`} hint={originHint} tone={originValid ? 'neutral' : 'warn'} />
            <MetricBox label="软限位余量" value={`${softMargin.toFixed(unit === 'um' ? 0 : 2)} ${unit}`} tone={softMargin < (unit === 'um' ? 500 : 2) ? 'warn' : 'ok'} />
            <MetricBox label="最大速度" value={`${group.maxSpeed} ${speedUnit}`} />
          </div>
          <Form layout="vertical" className="manual-command-form manual-command-form-arm">
            <Form.Item label={`目标增量 ${unit}`}>
              <InputNumber min={0} step={unit === 'um' ? 10 : 0.1} value={stepValue} onChange={(value) => setManualAxisStep(unit, Number(value ?? 0))} />
            </Form.Item>
            <Form.Item label="速度档位">
              <Select value={manualControl.speedMode} options={speedModeOptions} onChange={setManualSpeedMode} />
            </Form.Item>
            <Form.Item label="软限位范围">
              <Input
                value={`${formatSoftLimitValue(displayLimits.min, axisIndex)} ~ ${formatSoftLimitValue(displayLimits.max, axisIndex)} ${unit}`}
                readOnly
              />
            </Form.Item>
          </Form>
          <div className="manual-action-row">
            <Button disabled={axisBusy || !motionReady} onClick={() => issueManualAxisMove(side, selectedAxis, -1)}>
              {axisBusy ? busyText : `-${stepValue}${unit}`}
            </Button>
            <Button type="primary" disabled={axisBusy || !motionReady} onClick={() => issueManualAxisMove(side, selectedAxis, 1)}>
              {axisBusy ? busyText : `+${stepValue}${unit}`}
            </Button>
            <Button icon={<Square size={15} />} loading={pendingMotionAction === 'stop'} onClick={() => void stopMotion()}>
              停止
            </Button>
            <Button icon={<Activity size={15} />} onClick={() => injectLog('INFO', `${sideSpec.shortLabel} manual self-check requested`, '[HAL]')}>
              检测
            </Button>
            <Button
              icon={<PlugZap size={15} />}
              danger={nextMotionAction === 'disable'}
              loading={pendingMotionAction !== null}
              onClick={() => void toggleMotionEnabled()}
            >
              {nextMotionAction === 'enable' ? '使能' : '断使能'}
            </Button>
            <Button danger icon={<ShieldAlert size={15} />} onClick={triggerEmergencyStop}>
              急停
            </Button>
          </div>
        </div>
      </div>
    </article>
  )
}

function ManualGripperControl({
  side,
  config,
  updateConfig,
  currentMm,
  issueManualGripperMove,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  currentMm: number
  issueManualGripperMove: (side: RobotSide, command: ManualGripperCommand, targetMm?: number) => void
}) {
  const sideSpec = armHardwareSpecs[side]
  const portKey = side === 'left' ? 'leftPort' : 'rightPort'
  const targetKey = side === 'left' ? 'targetLeftMm' : 'targetRightMm'
  const slaveKey = side === 'left' ? 'leftSlaveId' : 'rightSlaveId'
  const enabledKey = side === 'left' ? 'leftEnabled' : 'rightEnabled'
  const gripperEnabled = Boolean(config.gripper[enabledKey])
  const setTarget = (value: number) => updateConfig({ gripper: { ...config.gripper, [targetKey]: value } })
  const currentText = formatGripperPosition(currentMm)
  const jawMm = safeGripperPosition(currentMm)
  return (
    <article className="manual-gripper-card">
      <div className="manual-card-head">
        <div>
          <Typography.Title level={3}>{sideSpec.shortLabel}夹爪手动控制</Typography.Title>
          <Typography.Text type="secondary">{config.gripper[portKey]} · 从站 {config.gripper[slaveKey]} · EPG006</Typography.Text>
        </div>
        <Space wrap>
          <Tag color={config.gripper[enabledKey] ? 'success' : 'warning'}>{config.gripper[enabledKey] ? '已使能' : '未使能'}</Tag>
          <Tag>0-26 mm</Tag>
        </Space>
      </div>
      <div className="manual-gripper-body">
        <div className="manual-gripper-visual">
          <span className="gripper-jaw gripper-jaw-left" style={{ transform: `translateX(${-Math.min(34, jawMm * 1.2)}px)` }} />
          <span className="gripper-jaw gripper-jaw-right" style={{ transform: `translateX(${Math.min(34, jawMm * 1.2)}px)` }} />
          <b>{currentText}</b>
        </div>
        <div className="manual-gripper-controls">
          <div className="manual-readout-row">
            <MetricBox label="目标开合" value={`${config.gripper[targetKey].toFixed(1)} mm`} />
            <MetricBox label="命令力限制" value={`≤ ${config.gripper.commandForceLimitN.toFixed(1)} N`} />
            <MetricBox label="力/力矩传感" value="待确认" hint="手册未给出 EPG006 反馈接口" tone="warn" />
          </div>
          <Slider min={0} max={config.gripper.strokeMm} step={0.1} value={config.gripper[targetKey]} onChange={(value) => setTarget(Number(value))} />
          <Form layout="vertical" className="manual-command-form">
            <Form.Item label="目标开合 mm">
              <InputNumber min={0} max={config.gripper.strokeMm} step={0.1} value={config.gripper[targetKey]} onChange={(value) => setTarget(Number(value ?? 0))} />
            </Form.Item>
            <Form.Item label="命令力限制 N">
              <InputNumber min={0} max={8} value={config.gripper.commandForceLimitN} onChange={(value) => updateConfig({ gripper: { ...config.gripper, commandForceLimitN: Number(value ?? 8) } })} />
            </Form.Item>
          </Form>
          <div className="manual-action-row">
            <Button type={gripperEnabled ? 'default' : 'primary'} icon={<PlugZap size={15} />} onClick={() => issueManualGripperMove(side, gripperEnabled ? 'disable' : 'enable')}>
              {gripperEnabled ? '断使能' : '使能'}
            </Button>
            <Button disabled={!gripperEnabled} onClick={() => issueManualGripperMove(side, 'target', config.gripper[targetKey])}>执行目标</Button>
            <Button disabled={!gripperEnabled} onClick={() => issueManualGripperMove(side, 'open')}>打开</Button>
            <Button disabled={!gripperEnabled} onClick={() => issueManualGripperMove(side, 'close')}>闭合</Button>
            <Button disabled={!gripperEnabled} icon={<RotateCcw size={15} />} onClick={() => issueManualGripperMove(side, 'home')}>回零</Button>
            <Button icon={<Square size={15} />} onClick={() => issueManualGripperMove(side, 'stop')}>停止</Button>
          </div>
        </div>
      </div>
    </article>
  )
}

function ManualMemoryRow({
  memory,
  replaying,
  replayManualMemory,
  pauseManualReplay,
  deleteManualMemory,
}: {
  memory: ManualControlMemory
  replaying: boolean
  replayManualMemory: (id: number) => void
  pauseManualReplay: () => void
  deleteManualMemory: (id: number) => void
}) {
  return (
    <div className="manual-memory-row">
      <div>
        <b>{memory.name}</b>
        <span>{memory.actions.length} steps · {(memory.durationMs / 1000).toFixed(1)} s</span>
      </div>
      <Space>
        <Button size="small" icon={replaying ? <Pause size={14} /> : <Play size={14} />} onClick={() => (replaying ? pauseManualReplay() : replayManualMemory(memory.id))}>
          {replaying ? '暂停' : '回放'}
        </Button>
        <Button size="small" icon={<Trash2 size={14} />} onClick={() => deleteManualMemory(memory.id)} />
      </Space>
    </div>
  )
}

function ManualReplayPanel({
  manualControl,
  startManualRecording,
  stopManualRecording,
  saveManualMemory,
  replayManualMemory,
  pauseManualReplay,
  deleteManualMemory,
}: {
  manualControl: ManualControlState
  startManualRecording: () => void
  stopManualRecording: () => void
  saveManualMemory: (name?: string) => void
  replayManualMemory: (id: number) => void
  pauseManualReplay: () => void
  deleteManualMemory: (id: number) => void
}) {
  const [memoryName, setMemoryName] = useState('')
  const saveMemory = () => {
    saveManualMemory(memoryName)
    setMemoryName('')
  }
  return (
    <article className="manual-replay-panel">
      <div className="manual-card-head">
        <div>
          <Typography.Title level={3}>动作记忆与回放</Typography.Title>
          <Typography.Text type="secondary">记录网页端下发的轴动作和左右夹爪动作；回放时仍按硬件安全限幅逐条执行。</Typography.Text>
        </div>
        <Tag color={manualControl.recording ? 'error' : manualControl.replayingMemoryId ? 'processing' : 'default'}>
          {manualControl.recording ? '记录中' : manualControl.replayingMemoryId ? '回放队列' : '待命'}
        </Tag>
      </div>
      <div className="manual-recorder-row">
        <Input placeholder="动作记忆名称" value={memoryName} onChange={(event) => setMemoryName(event.target.value)} />
        <Button type="primary" icon={<Activity size={15} />} disabled={manualControl.recording} onClick={startManualRecording}>
          开始记录
        </Button>
        <Button icon={<Square size={15} />} disabled={!manualControl.recording} onClick={stopManualRecording}>
          停止记录
        </Button>
        <Button icon={<Save size={15} />} disabled={manualControl.draftActions.length === 0} onClick={saveMemory}>
          保存动作记忆
        </Button>
      </div>
      <div className="manual-replay-layout">
        <div className="manual-action-feed">
          <b>本次记录</b>
          {manualControl.draftActions.length === 0 ? (
            <span className="manual-empty">还没有记录动作</span>
          ) : (
            manualControl.draftActions.slice(-8).map((action) => <span key={action.id}>{formatManualAction(action)}</span>)
          )}
        </div>
        <div className="manual-memory-list">
          <b>动作记忆</b>
          {manualControl.memories.length === 0 ? (
            <span className="manual-empty">保存后可在这里选择并回放</span>
          ) : (
            manualControl.memories.map((memory) => (
              <ManualMemoryRow
                key={memory.id}
                memory={memory}
                replaying={manualControl.replayingMemoryId === memory.id}
                replayManualMemory={replayManualMemory}
                pauseManualReplay={pauseManualReplay}
                deleteManualMemory={deleteManualMemory}
              />
            ))
          )}
        </div>
      </div>
    </article>
  )
}

function ManualControlPanel({
  positions,
  grippers,
  config,
  updateConfig,
  manualControl,
  nowMs,
  motionEnabled,
  motionAxisEnabled,
  selectManualAxis,
  setManualAxisStep,
  setManualSpeedMode,
  issueManualAxisMove,
  issueManualGripperMove,
  triggerEmergencyStop,
  startManualRecording,
  stopManualRecording,
  saveManualMemory,
  replayManualMemory,
  pauseManualReplay,
  deleteManualMemory,
  injectLog,
}: {
  positions: number[]
  grippers: number[]
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  manualControl: ManualControlState
  nowMs: number
  motionEnabled: TelemetryFrame['motionEnabled']
  motionAxisEnabled: TelemetryFrame['motionAxisEnabled']
  selectManualAxis: (side: RobotSide, axis: ManualControlAxis) => void
  setManualAxisStep: (unit: 'um' | '°', value: number) => void
  setManualSpeedMode: (mode: ManualSpeedMode) => void
  issueManualAxisMove: (side: RobotSide, axis: ManualControlAxis, direction: -1 | 1) => void
  issueManualGripperMove: (side: RobotSide, command: ManualGripperCommand, targetMm?: number) => void
  triggerEmergencyStop: () => void
  startManualRecording: () => void
  stopManualRecording: () => void
  saveManualMemory: (name?: string) => void
  replayManualMemory: (id: number) => void
  pauseManualReplay: () => void
  deleteManualMemory: (id: number) => void
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  return (
    <section id="manual" className="manual-control-page">
      <div className="manual-page-summary">
        <MetricBox label="当前选择" value={`${manualControl.selectedSide === 'left' ? '左臂' : '右臂'} ${manualControl.selectedAxis}`} />
        <MetricBox label="平移步长" value={`${manualControl.axisStepUm} um`} />
        <MetricBox label="旋转步长" value={`${manualControl.axisStepDeg} °`} />
        <MetricBox label="记录动作" value={`${manualControl.draftActions.length} steps`} tone={manualControl.recording ? 'warn' : 'neutral'} />
      </div>
      <div className="manual-control-grid">
        {sideOrder.map((side) => (
          <ManualArmControl
            key={side}
            side={side}
            positions={positions}
            config={config}
            manualControl={manualControl}
            nowMs={nowMs}
            motionEnabled={motionEnabled?.[side] ?? null}
            motionAxisEnabled={motionAxisEnabled?.[side] ?? undefined}
            selectManualAxis={selectManualAxis}
            setManualAxisStep={setManualAxisStep}
            setManualSpeedMode={setManualSpeedMode}
            issueManualAxisMove={issueManualAxisMove}
            triggerEmergencyStop={triggerEmergencyStop}
            injectLog={injectLog}
          />
        ))}
        {sideOrder.map((side, index) => (
          <ManualGripperControl
            key={side}
            side={side}
            config={config}
            updateConfig={updateConfig}
            currentMm={grippers[index] ?? -1}
            issueManualGripperMove={issueManualGripperMove}
          />
        ))}
        <ManualReplayPanel
          manualControl={manualControl}
          startManualRecording={startManualRecording}
          stopManualRecording={stopManualRecording}
          saveManualMemory={saveManualMemory}
          replayManualMemory={replayManualMemory}
          pauseManualReplay={pauseManualReplay}
          deleteManualMemory={deleteManualMemory}
        />
      </div>
    </section>
  )
}

export function SettingsView() {
  const config = useTelemetryStore((state) => state.config)
  const frame = useTelemetryStore((state) => state.frame)
  const history = useTelemetryStore((state) => state.history)
  const updateConfig = useTelemetryStore((state) => state.updateConfig)
  const injectLog = useTelemetryStore((state) => state.sendBackendCommandLog)
  const setDangerOverride = useTelemetryStore((state) => state.setDangerOverride)
  const acknowledgeSafety = useTelemetryStore((state) => state.acknowledgeSafety)
  const manualControl = useTelemetryStore((state) => state.manualControl)
  const selectManualAxis = useTelemetryStore((state) => state.selectManualAxis)
  const setManualAxisStep = useTelemetryStore((state) => state.setManualAxisStep)
  const setManualSpeedMode = useTelemetryStore((state) => state.setManualSpeedMode)
  const issueManualAxisMove = useTelemetryStore((state) => state.issueManualAxisMove)
  const issueManualGripperMove = useTelemetryStore((state) => state.issueManualGripperMove)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const startManualRecording = useTelemetryStore((state) => state.startManualRecording)
  const stopManualRecording = useTelemetryStore((state) => state.stopManualRecording)
  const saveManualMemory = useTelemetryStore((state) => state.saveManualMemory)
  const replayManualMemory = useTelemetryStore((state) => state.replayManualMemory)
  const pauseManualReplay = useTelemetryStore((state) => state.pauseManualReplay)
  const deleteManualMemory = useTelemetryStore((state) => state.deleteManualMemory)
  const parameterSnapshots = useTelemetryStore((state) => state.parameterSnapshots)
  const saveParameterSnapshot = useTelemetryStore((state) => state.saveParameterSnapshot)
  const applyParameterSnapshot = useTelemetryStore((state) => state.applyParameterSnapshot)
  const deleteParameterSnapshot = useTelemetryStore((state) => state.deleteParameterSnapshot)
  const location = useLocation()
  const focusHash = location.hash.replace('#', '')
  const targetTab = tabForHardwareHash(focusHash)
  const [snapshotDraft, setSnapshotDraft] = useState<{ scope: ParameterSnapshotScope; name: string } | null>(null)
  const [manualClockMs, setManualClockMs] = useState(() => Date.now())

  const openSnapshotModal = (scope: ParameterSnapshotScope) => setSnapshotDraft({ scope, name: defaultSnapshotName(scope) })
  const commitSnapshot = () => {
    if (!snapshotDraft) return
    const name = snapshotDraft.name.trim()
    if (!name) return
    saveParameterSnapshot(snapshotDraft.scope, name)
    setSnapshotDraft(null)
  }
  const snapshotMenu = (scope: ParameterSnapshotScope): MenuProps => {
    const scopedSnapshots = parameterSnapshots.filter((item) => item.scope === scope)
    return {
      items: scopedSnapshots.length > 0
        ? scopedSnapshots.map((snapshot) => ({
            key: snapshot.id,
            label: (
              <span className="snapshot-menu-label">
                <span className="snapshot-menu-copy">
                  <b>{snapshot.name}</b>
                  <small>{formatSnapshotTime(snapshot.createdAt)}</small>
                </span>
                <Button
                  aria-label={`删除 ${snapshot.name}`}
                  danger
                  icon={<Trash2 size={13} />}
                  size="small"
                  type="text"
                  onClick={(event) => {
                    event.preventDefault()
                    event.stopPropagation()
                    deleteParameterSnapshot(snapshot.id)
                  }}
                />
              </span>
            ),
          }))
        : [{ key: 'empty', label: '暂无快照', disabled: true }],
      onClick: ({ key }) => {
        if (key === 'empty') return
        applyParameterSnapshot(String(key))
      },
    }
  }

  useEffect(() => {
    if (!focusHash) return
    const timer = window.setTimeout(() => {
      document.getElementById(focusHash)?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
    }, 120)
    return () => window.clearTimeout(timer)
  }, [focusHash])

  useEffect(() => {
    const timer = window.setInterval(() => setManualClockMs(Date.now()), 200)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="view-stack hardware-settings-view">
      <section className="page-header">
        <div>
          <Typography.Title level={2}>硬件设置</Typography.Title>
          <Typography.Text type="secondary">参数保存到后端 config.json；手动控制命令经 Backend/HAL 下发到已连接硬件。</Typography.Text>
        </div>
        <Space wrap>
          {focusHash && <Tag color="processing">当前聚焦：{hashLabels[focusHash] ?? focusHash}</Tag>}
          <Dropdown menu={snapshotMenu('all')} trigger={['click']}>
            <Button icon={<RefreshCw size={16} />}>
              选择硬件快照
            </Button>
          </Dropdown>
          <Button type="primary" icon={<Save size={16} />} onClick={() => openSnapshotModal('all')}>
            保存硬件快照
          </Button>
        </Space>
      </section>

      <Tabs
        key={focusHash || 'settings-tabs'}
        defaultActiveKey={targetTab}
        items={[
          {
            key: 'config',
            label: '硬件配置',
            children: (
              <section className="hardware-settings-page">
                <div className="hardware-focus-strip">
                  <MetricBox label="平台轴数" value="12 轴 + 2 夹爪" />
                  <MetricBox label="运动控制卡" value="2× LTDMC / DMC3000" hint="Card 1 左，Card 0 右" />
                  <MetricBox label="相机" value="AR0234 + 2xIMX258" hint="按原始比例预览" />
                  <MetricBox label="力觉" value="2× ATI Nano-17" hint="显示 mN / mN·m" />
                </div>
                <div className="hardware-settings-grid">
                  <HalCard config={config} updateConfig={updateConfig} focusHash={focusHash} injectLog={injectLog} />
                  <SafetyCard
                    config={config}
                    updateConfig={updateConfig}
                    focusHash={focusHash}
                    dangerIndex={frame.dangerIndex}
                    setDangerOverride={setDangerOverride}
                    acknowledgeSafety={acknowledgeSafety}
                    injectLog={injectLog}
                  />
                  {sideOrder.map((side) => (
                    <MotionCard
                      key={side}
                      side={side}
                      config={config}
                      updateConfig={updateConfig}
                      focusHash={focusHash}
                      positions={frame.jointPositions}
                      motionEnabled={frame.motionEnabled?.[side] ?? null}
                      motionAxisEnabled={frame.motionAxisEnabled?.[side] ?? undefined}
                      injectLog={injectLog}
                      triggerEmergencyStop={triggerEmergencyStop}
                      snapshotMenu={snapshotMenu}
                      openSnapshotModal={openSnapshotModal}
                    />
                  ))}
                  {cameraOrder.map((cameraKey) => (
                    <CameraCard
                      key={cameraKey}
                      cameraKey={cameraKey}
                      camera={cameraByKey(frame.cameras, cameraKey)}
                      config={config}
                      updateConfig={updateConfig}
                      focusHash={focusHash}
                      injectLog={injectLog}
                    />
                  ))}
                  {sideOrder.map((side) => (
                    <ForceSensorCard
                      key={side}
                      side={side}
                      config={config}
                      updateConfig={updateConfig}
                      focusHash={focusHash}
                      values={side === 'left' ? frame.forceLeft : frame.forceRight}
                      history={history}
                      injectLog={injectLog}
                    />
                  ))}
                  {sideOrder.map((side, index) => (
                    <GripperCard
                      key={side}
                      side={side}
                      config={config}
                      updateConfig={updateConfig}
                      focusHash={focusHash}
                      currentMm={frame.gripperPositions[index] ?? -1}
                      issueManualGripperMove={issueManualGripperMove}
                      injectLog={injectLog}
                    />
                  ))}
                  {sideOrder.map((side) => (
                    <TeleopHandCard
                      key={side}
                      side={side}
                      config={config}
                      updateConfig={updateConfig}
                      focusHash={focusHash}
                      frame={frame}
                      injectLog={injectLog}
                    />
                  ))}
                  <StorageCard config={config} updateConfig={updateConfig} focusHash={focusHash} />
                  <PicoVisionCard config={config} updateConfig={updateConfig} focusHash={focusHash} injectLog={injectLog} />
                </div>
              </section>
            ),
          },
          {
            key: 'manual',
            label: '手动控制',
            children: (
              <ManualControlPanel
                positions={frame.jointPositions}
                grippers={frame.gripperPositions}
                config={config}
                updateConfig={updateConfig}
                manualControl={manualControl}
                nowMs={manualClockMs}
                motionEnabled={frame.motionEnabled}
                motionAxisEnabled={frame.motionAxisEnabled}
                selectManualAxis={selectManualAxis}
                setManualAxisStep={setManualAxisStep}
                setManualSpeedMode={setManualSpeedMode}
                issueManualAxisMove={issueManualAxisMove}
                issueManualGripperMove={issueManualGripperMove}
                triggerEmergencyStop={triggerEmergencyStop}
                startManualRecording={startManualRecording}
                stopManualRecording={stopManualRecording}
                saveManualMemory={saveManualMemory}
                replayManualMemory={replayManualMemory}
                pauseManualReplay={pauseManualReplay}
                deleteManualMemory={deleteManualMemory}
                injectLog={injectLog}
              />
            ),
          },
        ]}
      />

      <Modal
        title={snapshotDraft ? snapshotModalTitle(snapshotDraft.scope) : '保存参数快照'}
        open={Boolean(snapshotDraft)}
        onCancel={() => setSnapshotDraft(null)}
        onOk={commitSnapshot}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !snapshotDraft?.name.trim() }}
      >
        <Form layout="vertical">
          <Form.Item label="快照名称">
            <Input
              autoFocus
              value={snapshotDraft?.name ?? ''}
              onChange={(event) => setSnapshotDraft((current) => current ? { ...current, name: event.target.value } : current)}
              onPressEnter={commitSnapshot}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
