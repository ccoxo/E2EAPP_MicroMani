/*
 * 阅读导航 01｜入口与界面
 * 职责：组合平台、双臂和硬件状态概览，提供设备与设置导航。
 * 先看：diagnosticState → cameraByKey → forceMagnitude → stateText。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { Bot, Camera, Cpu, Gamepad2, Hand, MapPinned, RadioTower, ShieldCheck, Waves } from 'lucide-react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { CameraPreview } from '../components/CameraPreview'
import { AxisGroupChart, ForceChart } from '../components/Charts'
import { MetricPill } from '../components/MetricPill'
import { UiSpace, UiTag, UiText } from '../components/ui'
import { armHardwareSpecs, forceSensorModelLabel, forceSensorUnitLabel, hardwareSideForOperatorSide } from '../data'
import { numberArrayEqual, useFrameField } from '../stores/frameSelectors'
import { useTelemetryStore } from '../stores/telemetry'
import { teleopHandState, teleopHandValue, teleopPairState, teleopPairValue } from '../teleopStatus'
import type { ConnectionState, DiagnosticItem, TelemetryFrame, TelemetrySample } from '../types'

const semanticAxes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const forceChannels = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz']
/** 计算对应的业务值或展示值。 */
function diagnosticState(diagnostics: DiagnosticItem[], key: string): ConnectionState {
  return diagnostics.find((item) => item.key === key)?.status ?? 'pending'
}
/** 计算对应的业务值或展示值。 */
function forceMagnitude(values: number[]) {
  return Math.sqrt(values.slice(0, 3).reduce((sum, value) => sum + value * value, 0))
}
/** 格式化对应数值用于界面展示。 */
function stateText(state: ConnectionState) {
  if (state === 'ok') return '正常'
  if (state === 'warn') return '注意'
  if (state === 'error') return '故障'
  if (state === 'checking') return '检查中'
  return '待确认'
}
/** 格式化对应数值用于界面展示。 */
function stateTone(state: ConnectionState): 'success' | 'warning' | 'error' | 'processing' | 'muted' {
  if (state === 'ok') return 'success'
  if (state === 'warn') return 'warning'
  if (state === 'error') return 'error'
  if (state === 'checking') return 'processing'
  return 'muted'
}
/** 格式化对应数值用于界面展示。 */
function formatAxisValue(value: number, index: number) {
  return index < 3 ? `${value.toFixed(1)} µm` : `${value.toFixed(2)}°`
}
/** 格式化对应数值用于界面展示。 */
function formatForceValue(value: number, index: number) {
  return index < 3 ? `${(value * 1000).toFixed(0)}` : `${(value * 1000).toFixed(1)}`
}
/** 格式化对应数值用于界面展示。 */
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
/** 渲染当前界面单元，并连接所需数据。 */
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
        <UiTag>{stateText(state)}</UiTag>
      </span>
      <b>{value}</b>
      <small>{detail}</small>
    </button>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
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
/** 渲染当前界面单元，并连接所需数据。 */
function GlobalHardwarePanel({ diagnostics }: { diagnostics: DiagnosticItem[] }) {
  const navigate = useNavigate()
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const wsHz = useTelemetryStore((state) => state.frame.resource.wsHz)
  const forceStatus = useTelemetryStore((state) => state.frame.forceStatus)
  const globalCamera = useTelemetryStore((state) => state.frame.cameras.find((camera) => camera.key === 'global'))
  const teleopHands = useTelemetryStore((state) => state.frame.teleopHands)
  const omegaState = teleopPairState({ teleopHands } as TelemetryFrame, diagnostics)
  const halState: ConnectionState = halOk ? 'ok' : 'error'
  const safetyLatched = Boolean(forceStatus?.safety?.latched)
  const safetyState: ConnectionState = safetyLatched || dangerIndex > 0.85 ? 'error' : dangerIndex > 0.6 ? 'warn' : 'ok'
 /** 描述当前方法的功能边界。 */
 const go = (hash: string) => navigate(`/settings#${hash}`)

  return (
    <section className="hardware-panel global-hardware-panel">
      <div className="hardware-section-title">
        <span><ShieldCheck size={17} />全局硬件状态</span>
        <UiSpace size={6} wrap>
          <MetricPill state={halState} label="HAL" />
          <MetricPill state={globalCamera?.health ?? 'pending'} label="全局相机" />
          <MetricPill state={omegaState} label="主手" />
          <MetricPill state={safetyState} label={`Safety ${safetyLatched ? 'LOCK' : dangerIndex.toFixed(2)}`} />
        </UiSpace>
      </div>
      <div className="global-hardware-layout">
        {globalCamera && <CameraPreview camera={globalCamera} compact onClick={() => go('camera-global')} />}
        <div className="global-hardware-grid">
          <HardwareStatusButton
            label="HAL 通信"
            state={halState}
            value="HalServer.exe"
            detail={`运动状态 ${wsHz}Hz，硬件急停状态可读`}
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
            value={teleopPairValue({ teleopHands } as TelemetryFrame)}
            detail="Force Dimension SDK / USB 设备状态"
            icon={<Gamepad2 size={15} />}
            onClick={() => go('teleop-left')}
          />
          <HardwareStatusButton
            label="安全链路"
            state={safetyState}
            value={safetyLatched ? '安全锁存' : `danger_index ${dangerIndex.toFixed(2)}`}
            detail="外部急停、软限位和力觉保护汇总"
            icon={<ShieldCheck size={15} />}
            onClick={() => go('safety')}
          />
        </div>
      </div>
    </section>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
function ArmHardwarePanel({
  side,
  history,
  diagnostics,
}: {
  side: 'left' | 'right'
  history: TelemetrySample[]
  diagnostics: DiagnosticItem[]
}) {
  const navigate = useNavigate()
  const forceSource = useTelemetryStore((state) => state.config.force.source)
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const forceStatus = useTelemetryStore((state) => state.frame.forceStatus)
  const teleopHands = useTelemetryStore((state) => state.frame.teleopHands)
  const forceLeft = useFrameField((frame) => frame.forceLeft, numberArrayEqual)
  const forceRight = useFrameField((frame) => frame.forceRight, numberArrayEqual)
  const jointPositions = useFrameField((frame) => frame.jointPositions, numberArrayEqual)
  const gripperPositions = useFrameField((frame) => frame.gripperPositions, numberArrayEqual)
  const wristCamera = useTelemetryStore((state) =>
    state.frame.cameras.find((camera) => camera.key === (side === 'left' ? 'wrist_right' : 'wrist_left')),
  )
  const hardwareSide = hardwareSideForOperatorSide(side)
  const isHardwareLeft = hardwareSide === 'left'
  const sideSpec = armHardwareSpecs[hardwareSide]
  const label = isHardwareLeft ? '左机械臂' : '右机械臂'
  const cardNo = `Card ${sideSpec.cardNo}`
  const axisOrder = sideSpec.axisOrder.join(' / ')
  const axisOffset = sideSpec.stateOffset
  const forceValues = isHardwareLeft ? forceLeft : forceRight
  const forceState = diagnosticState(diagnostics, isHardwareLeft ? 'ati-left' : 'ati-right')
  const gripperState = diagnosticState(diagnostics, 'gripper')
  const teleopState = teleopHandState({ teleopHands } as TelemetryFrame, diagnostics, side)
  const motionState: ConnectionState = halOk ? 'ok' : 'error'
  const forceNorm = forceMagnitude(forceValues)
  const safetyState: ConnectionState = forceStatus?.safety?.latched || forceNorm > 2.5 || dangerIndex > 0.65 ? 'warn' : 'ok'
  const gripperValue = formatGripperValue(gripperPositions[isHardwareLeft ? 0 : 1])
  const forceModel = forceSensorModelLabel(forceSource)
  const forceUnit = forceSensorUnitLabel(forceSource)
 /** 描述当前方法的功能边界。 */
 const go = (hash: string) => navigate(`/settings#${hash}`)

  return (
    <section className="hardware-panel arm-hardware-panel">
      <div className="hardware-section-title">
        <span><Bot size={17} />{label}</span>
        <UiSpace size={6} wrap>
          <UiTag tone={stateTone(motionState)}>运动控制卡 {cardNo}</UiTag>
          <MetricPill state={forceState} label={forceModel} />
          <MetricPill state={safetyState} label="安全" />
        </UiSpace>
      </div>

      <div className="device-chip-row">
        <DeviceChip label="运动控制卡" state={motionState} value={cardNo} icon={<Cpu size={14} />} onClick={() => go(side === 'left' ? 'motion-left' : 'motion-right')} />
        <DeviceChip label="物理轴号" state={motionState} value={axisOrder} icon={<MapPinned size={14} />} onClick={() => go(side === 'left' ? 'motion-left' : 'motion-right')} />
        <DeviceChip label={forceModel} state={forceState} value={forceUnit.split(' / ')[0]} icon={<Waves size={14} />} onClick={() => go(side === 'left' ? 'force-left' : 'force-right')} />
        <DeviceChip label="夹爪" state={gripperState} value={gripperValue} icon={<Hand size={14} />} onClick={() => go(side === 'left' ? 'gripper-left' : 'gripper-right')} />
        <DeviceChip label="主手" state={teleopState} value={teleopHandValue({ teleopHands } as TelemetryFrame, side)} icon={<Gamepad2 size={14} />} onClick={() => go(side === 'left' ? 'teleop-left' : 'teleop-right')} />
      </div>

      <div className="arm-hardware-layout">
        <div className="arm-camera-block">
          {wristCamera && <CameraPreview camera={wristCamera} compact onClick={() => go(side === 'left' ? 'camera-left' : 'camera-right')} />}
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
                      <em>{formatAxisValue(jointPositions[axisOffset + axisIndex], axisIndex)}</em>
                    </span>
                  )
                })}
              </div>
            ))}
          </div>
          <div className="axis-chart-pair">
            <button className="axis-chart-card" type="button" onClick={() => go(side === 'left' ? 'motion-left' : 'motion-right')}>
              <UiText strong>平移轴位置 · µm</UiText>
              <AxisGroupChart history={history} side={hardwareSide} group="translation" />
            </button>
            <button className="axis-chart-card" type="button" onClick={() => go(side === 'left' ? 'motion-left' : 'motion-right')}>
              <UiText strong>旋转轴角度 · °</UiText>
              <AxisGroupChart history={history} side={hardwareSide} group="rotation" />
            </button>
          </div>
        </div>
      </div>

      <div className="arm-force-layout">
        <button className="force-chart-card" type="button" onClick={() => go(side === 'left' ? 'force-left' : 'force-right')}>
          <UiText strong>{forceModel} 六维力觉 · {forceSource === 'hkvl_serial' ? 'N' : 'mN'}</UiText>
          <ForceChart history={history} side={hardwareSide} height={116} />
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
          <UiTag tone={stateTone(forceState)}>{stateText(forceState)}</UiTag>
        </div>
      </div>
    </section>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export function DashboardView() {
  const history = useTelemetryStore((state) => state.history)
  const diagnostics = useTelemetryStore((state) => state.diagnostics)

  return (
    <div className="view-stack hardware-dashboard-page">
      <GlobalHardwarePanel diagnostics={diagnostics} />
      <section className="arm-hardware-grid">
        <ArmHardwarePanel side="right" history={history} diagnostics={diagnostics} />
        <ArmHardwarePanel side="left" history={history} diagnostics={diagnostics} />
      </section>
    </div>
  )
}
