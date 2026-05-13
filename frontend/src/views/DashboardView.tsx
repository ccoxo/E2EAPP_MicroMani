import { Space, Tag, Typography } from 'antd'
import { Bot, Camera, Cpu, Gamepad2, Hand, MapPinned, RadioTower, ShieldCheck, Waves } from 'lucide-react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { CameraPreview } from '../components/CameraPreview'
import { AxisGroupChart, ForceChart } from '../components/Charts'
import { MetricPill } from '../components/MetricPill'
import { armHardwareSpecs } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import type { CameraTelemetry, ConnectionState, DiagnosticItem, TelemetryFrame, TelemetrySample } from '../types'

const semanticAxes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const forceChannels = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz']

function diagnosticState(diagnostics: DiagnosticItem[], key: string): ConnectionState {
  return diagnostics.find((item) => item.key === key)?.status ?? 'pending'
}

function cameraByKey(cameras: CameraTelemetry[], key: CameraTelemetry['key']) {
  return cameras.find((camera) => camera.key === key)
}

function forceMagnitude(values: number[]) {
  return Math.sqrt(values.slice(0, 3).reduce((sum, value) => sum + value * value, 0))
}

function stateText(state: ConnectionState) {
  if (state === 'ok') return '正常'
  if (state === 'warn') return '注意'
  if (state === 'error') return '故障'
  if (state === 'checking') return '检查中'
  return '待确认'
}

function stateTone(state: ConnectionState) {
  if (state === 'ok') return 'success'
  if (state === 'warn') return 'warning'
  if (state === 'error') return 'error'
  if (state === 'checking') return 'processing'
  return 'default'
}

function formatAxisValue(value: number, index: number) {
  return index < 3 ? `${value.toFixed(1)} µm` : `${value.toFixed(2)}°`
}

function formatForceValue(value: number, index: number) {
  return index < 3 ? `${(value * 1000).toFixed(0)}` : `${(value * 1000).toFixed(1)}`
}

function formatGripperValue(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}mm` : '不可用'
}

const axisReadoutGroups = [
  { key: 'translation', label: '平移', start: 0, axes: semanticAxes.slice(0, 3) },
  { key: 'rotation', label: '旋转', start: 3, axes: semanticAxes.slice(3, 6) },
]

const forceReadoutGroups = [
  { key: 'force', label: '力 · mN', channels: forceChannels.slice(0, 3), start: 0 },
  { key: 'moment', label: '力矩 · mN·m', channels: forceChannels.slice(3, 6), start: 3 },
]

function HardwareStatusButton({
  label,
  state,
  value,
  detail,
  icon,
  onClick,
}: {
  label: string
  state: ConnectionState
  value: string
  detail: string
  icon: ReactNode
  onClick: () => void
}) {
  return (
    <button className={`hardware-status-button hardware-status-${state}`} type="button" onClick={onClick}>
      <span className="hardware-status-button-head">
        <span>{icon}{label}</span>
        <Tag>{stateText(state)}</Tag>
      </span>
      <b>{value}</b>
      <small>{detail}</small>
    </button>
  )
}

function DeviceChip({
  label,
  state,
  value,
  icon,
  onClick,
}: {
  label: string
  state: ConnectionState
  value: string
  icon: ReactNode
  onClick: () => void
}) {
  return (
    <button className={`device-chip device-chip-${state}`} type="button" onClick={onClick}>
      {icon}
      <span>{label}</span>
      <b>{value}</b>
    </button>
  )
}

function GlobalHardwarePanel({
  frame,
  diagnostics,
}: {
  frame: TelemetryFrame
  diagnostics: DiagnosticItem[]
}) {
  const navigate = useNavigate()
  const globalCamera = cameraByKey(frame.cameras, 'global')
  const omegaState = diagnosticState(diagnostics, 'omega7')
  const halState: ConnectionState = frame.halOk ? 'ok' : 'error'
  const safetyState: ConnectionState = frame.dangerIndex > 0.85 ? 'error' : frame.dangerIndex > 0.6 ? 'warn' : 'ok'
  const go = (hash: string) => navigate(`/settings#${hash}`)

  return (
    <section className="hardware-panel global-hardware-panel">
      <div className="hardware-section-title">
        <span><ShieldCheck size={17} />全局硬件状态</span>
        <Space size={6} wrap>
          <MetricPill state={halState} label="HAL" />
          <MetricPill state={globalCamera?.health ?? 'pending'} label="全局相机" />
          <MetricPill state={omegaState} label="主手" />
          <MetricPill state={safetyState} label={`Safety ${frame.dangerIndex.toFixed(2)}`} />
        </Space>
      </div>
      <div className="global-hardware-layout">
        {globalCamera && <CameraPreview camera={globalCamera} compact onClick={() => go('camera-global')} />}
        <div className="global-hardware-grid">
          <HardwareStatusButton
            label="HAL 通信"
            state={halState}
            value="HalServer.exe"
            detail={`运动状态 ${frame.resource.wsHz}Hz，硬件急停状态可读`}
            icon={<RadioTower size={15} />}
            onClick={() => go('hal')}
          />
          <HardwareStatusButton
            label="PICO-4 视觉"
            state="pending"
            value="视频推流待连接"
            detail="ADB 与 TCP 视频链路配置放到设置页"
            icon={<Camera size={15} />}
            onClick={() => go('teleop')}
          />
          <HardwareStatusButton
            label="双 Omega.7 主手"
            state={omegaState}
            value="左右主手枚举"
            detail="Force Dimension SDK / USB 设备状态"
            icon={<Gamepad2 size={15} />}
            onClick={() => go('teleop-left')}
          />
          <HardwareStatusButton
            label="安全链路"
            state={safetyState}
            value={`danger_index ${frame.dangerIndex.toFixed(2)}`}
            detail="外部急停、软限位和力觉保护汇总"
            icon={<ShieldCheck size={15} />}
            onClick={() => go('safety')}
          />
        </div>
      </div>
    </section>
  )
}

function ArmHardwarePanel({
  side,
  frame,
  history,
  diagnostics,
}: {
  side: 'left' | 'right'
  frame: TelemetryFrame
  history: TelemetrySample[]
  diagnostics: DiagnosticItem[]
}) {
  const navigate = useNavigate()
  const isLeft = side === 'left'
  const sideSpec = armHardwareSpecs[side]
  const label = isLeft ? '左机械臂' : '右机械臂'
  const cardNo = `Card ${sideSpec.cardNo}`
  const axisOrder = sideSpec.axisOrder.join(' / ')
  const axisOffset = sideSpec.stateOffset
  const wristCamera = cameraByKey(frame.cameras, isLeft ? 'wrist_left' : 'wrist_right')
  const forceValues = isLeft ? frame.forceLeft : frame.forceRight
  const forceState = diagnosticState(diagnostics, isLeft ? 'ati-left' : 'ati-right')
  const gripperState = diagnosticState(diagnostics, 'gripper')
  const teleopState = diagnosticState(diagnostics, 'omega7')
  const motionState: ConnectionState = frame.halOk ? 'ok' : 'error'
  const forceNorm = forceMagnitude(forceValues)
  const safetyState: ConnectionState = forceNorm > 2.5 || frame.dangerIndex > 0.65 ? 'warn' : 'ok'
  const gripperValue = formatGripperValue(frame.gripperPositions[isLeft ? 0 : 1])
  const go = (hash: string) => navigate(`/settings#${hash}`)

  return (
    <section className="hardware-panel arm-hardware-panel">
      <div className="hardware-section-title">
        <span><Bot size={17} />{label}</span>
        <Space size={6} wrap>
          <Tag color={stateTone(motionState)}>运动控制卡 {cardNo}</Tag>
          <MetricPill state={forceState} label="Nano-17" />
          <MetricPill state={safetyState} label="安全" />
        </Space>
      </div>

      <div className="device-chip-row">
        <DeviceChip label="运动控制卡" state={motionState} value={cardNo} icon={<Cpu size={14} />} onClick={() => go(isLeft ? 'motion-left' : 'motion-right')} />
        <DeviceChip label="物理轴号" state={motionState} value={axisOrder} icon={<MapPinned size={14} />} onClick={() => go(isLeft ? 'motion-left' : 'motion-right')} />
        <DeviceChip label="Nano-17" state={forceState} value="mN" icon={<Waves size={14} />} onClick={() => go(isLeft ? 'force-left' : 'force-right')} />
        <DeviceChip label="夹爪" state={gripperState} value={gripperValue} icon={<Hand size={14} />} onClick={() => go(isLeft ? 'gripper-left' : 'gripper-right')} />
        <DeviceChip label="主手" state={teleopState} value={isLeft ? '左 Omega.7' : '右 Omega.7'} icon={<Gamepad2 size={14} />} onClick={() => go(isLeft ? 'teleop-left' : 'teleop-right')} />
      </div>

      <div className="arm-hardware-layout">
        <div className="arm-camera-block">
          {wristCamera && <CameraPreview camera={wristCamera} compact onClick={() => go(isLeft ? 'camera-left' : 'camera-right')} />}
        </div>
        <div className="arm-axis-monitor">
          <div className="axis-readout-compact">
            {axisReadoutGroups.map((group) => (
              <div className="axis-readout-row" key={group.key}>
                <small>{group.label}</small>
                {group.axes.map((axis, groupIndex) => {
                  const axisIndex = group.start + groupIndex
                  return (
                    <span key={axis}>
                      <b>{axis}</b>
                      <em>{formatAxisValue(frame.jointPositions[axisOffset + axisIndex], axisIndex)}</em>
                    </span>
                  )
                })}
              </div>
            ))}
          </div>
          <div className="axis-chart-pair">
            <button className="axis-chart-card" type="button" onClick={() => go(isLeft ? 'motion-left' : 'motion-right')}>
              <Typography.Text strong>平移轴位置 · µm</Typography.Text>
              <AxisGroupChart history={history} side={side} group="translation" />
            </button>
            <button className="axis-chart-card" type="button" onClick={() => go(isLeft ? 'motion-left' : 'motion-right')}>
              <Typography.Text strong>旋转轴角度 · °</Typography.Text>
              <AxisGroupChart history={history} side={side} group="rotation" />
            </button>
          </div>
        </div>
      </div>

      <div className="arm-force-layout">
        <button className="force-chart-card" type="button" onClick={() => go(isLeft ? 'force-left' : 'force-right')}>
          <Typography.Text strong>Nano-17 六维力觉 · mN</Typography.Text>
          <ForceChart history={history} side={side} height={116} />
        </button>
        <div className="force-summary-card">
          <b>|F| {(forceNorm * 1000).toFixed(0)} mN</b>
          <div className="force-readout-compact force-readout-grouped">
            {forceReadoutGroups.map((group) => (
              <div className="force-readout-column" key={group.key}>
                <small>{group.label}</small>
                {group.channels.map((channel, groupIndex) => {
                  const channelIndex = group.start + groupIndex
                  return (
                    <span key={channel}>
                      <b>{channel}</b>
                      <em>{formatForceValue(forceValues[channelIndex], channelIndex)}</em>
                    </span>
                  )
                })}
              </div>
            ))}
          </div>
          <span>Fx/Fy/Fz: mN</span>
          <span>Mx/My/Mz: mN·m</span>
          <Tag color={stateTone(forceState)}>{stateText(forceState)}</Tag>
        </div>
      </div>
    </section>
  )
}

export function DashboardView() {
  const frame = useTelemetryStore((state) => state.frame)
  const history = useTelemetryStore((state) => state.history)
  const diagnostics = useTelemetryStore((state) => state.diagnostics)

  return (
    <div className="view-stack hardware-dashboard-page">
      <GlobalHardwarePanel frame={frame} diagnostics={diagnostics} />
      <section className="arm-hardware-grid">
        <ArmHardwarePanel side="left" frame={frame} history={history} diagnostics={diagnostics} />
        <ArmHardwarePanel side="right" frame={frame} history={history} diagnostics={diagnostics} />
      </section>
    </div>
  )
}
