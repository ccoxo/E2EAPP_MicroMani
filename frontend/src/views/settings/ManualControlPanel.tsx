/*
 * 手动点动 / 夹爪 / 动作回放面板；从 SettingsView 按域拆出。
 * 先看：ManualControlPanel → ManualArmControl → ManualReplayPanel。
 */
import {
  Activity,
  Pause,
  Play,
  PlugZap,
  Save,
  ShieldAlert,
  Square,
  Trash2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  UiButton,
  UiField,
  UiInput,
  UiNumber,
  UiSelect,
  UiSpace,
  UiTag,
  UiText,
  UiTitle,
} from '../../components/ui'
import { useMotionEnable } from '../../hooks/useMotionEnable'
import {
  armHardwareSpecs,
  hardwareSideForOperatorSide,
  operatorSideForHardwareSide,
  operatorSideLabel,
  type RobotSide,
} from '../../data'
import { manualAxisStepLimitFromPulse, manualAxisStepLimitPulse } from '../../manualMotionLimits'
import { manualMaxVelocity } from '../../manualSpeed'
import { mockMode, stopMotionSide } from '../../api'
import type {
  AppConfig,
  ArmSoftLimitConfig,
  LogEntry,
  ManualControlAction,
  ManualControlAxis,
  ManualControlMemory,
  ManualControlState,
  ManualGripperCommand,
  ManualSpeedMode,
  RotationWorkLimitSideConfig,
} from '../../types'
import { MetricBox, type PendingComparison } from './shared'
import { ManualGripperControl } from './GripperCards'

const sideOrder: RobotSide[] = ['left', 'right']

const defaultRotationWorkLimits: RotationWorkLimitSideConfig = {
  roll: { min: -100, max: 100 },
  pitch: { min: -100, max: 100 },
  yaw: { min: -7, max: 7 },
}

function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

const TRANSLATION_SOFT_LIMIT_DISABLED_MIN = -1000000000
const TRANSLATION_SOFT_LIMIT_DISABLED_MAX = 1000000000


function displaySoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value : value / 1000
}
/** Convert displayed rotation degrees back to stored millidegrees. */
/** 格式化对应数值用于界面展示。 */
function formatSoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value.toFixed(0) : value.toFixed(3)
}
/** Return the mechanical soft-limit object for the selected hardware side. */
function softLimitConfigForSide(config: AppConfig, side: RobotSide) {
  return side === 'left' ? config.motion.leftSoftLimits : config.motion.rightSoftLimits
}
/** Rotation work limits are optional; fall back to permissive defaults. */
function rotationWorkLimitsForSide(config: AppConfig, side: RobotSide): RotationWorkLimitSideConfig {
  return config.motion.rotationWorkLimits?.[side] ?? defaultRotationWorkLimits
}
/** Prefer signed pulse-per-unit values so UI deltas match hardware direction. */
function signedPulsePerUnit(config: AppConfig, side: RobotSide, axisIndex: number) {
  const kinematics = config.motion.kinematics
  const signed = side === 'left' ? kinematics.leftSignedPulsePerUnit : kinematics.rightSignedPulsePerUnit
  const fallback = side === 'left' ? kinematics.leftPulsePerUnit : kinematics.rightPulsePerUnit
  const value = Number(signed?.[axisIndex] ?? fallback?.[axisIndex] ?? 0)
  return Number.isFinite(value) && value !== 0 ? value : 0
}
/** 计算对应的业务值或展示值。 */
function pulseToAxisUi(config: AppConfig, side: RobotSide, axisIndex: number, pulse: number) {
  const pulsePerUnit = signedPulsePerUnit(config, side, axisIndex)
  if (!pulsePerUnit) return null
  const value = Number(pulse) / pulsePerUnit
  return axisIndex < 3 ? value * 1000 : value
}
/** 计算对应的业务值或展示值。 */
function originAxisUi(config: AppConfig, side: RobotSide, axisIndex: number) {
  const origin = config.motion.origin
  const valid = side === 'left' ? origin.leftValid : origin.rightValid
  const pulses = side === 'left' ? origin.leftPulse : origin.rightPulse
  if (!valid || !Array.isArray(pulses) || pulses.length <= axisIndex) return null
  return pulseToAxisUi(config, side, axisIndex, Number(pulses[axisIndex]))
}
/** Combine mechanical limits with work-origin-relative rotation limits. */
function effectiveAxisLimitUi(config: AppConfig, side: RobotSide, axisKey: keyof ArmSoftLimitConfig, axisIndex: number) {
  if (axisIndex < 3) {
    return {
      min: TRANSLATION_SOFT_LIMIT_DISABLED_MIN,
      max: TRANSLATION_SOFT_LIMIT_DISABLED_MAX,
      blocked: false,
    }
  }
  const mechanical = softLimitConfigForSide(config, side)[axisKey]
  const absolute = {
    min: displaySoftLimitValue(mechanical.min, axisIndex),
    max: displaySoftLimitValue(mechanical.max, axisIndex),
    blocked: false,
  }
  if (!config.motion.rotationWorkLimits?.enabled) return absolute
  const originUi = originAxisUi(config, side, axisIndex)
  if (originUi === null) return { ...absolute, blocked: true }
  const workLimit = rotationWorkLimitsForSide(config, side)[axisKey as keyof RotationWorkLimitSideConfig]
  return {
    min: Math.max(absolute.min, originUi + workLimit.min),
    max: Math.min(absolute.max, originUi + workLimit.max),
    blocked: false,
  }
}
/** 格式化对应数值用于界面展示。 */
function displayAxisLimitForTelemetry(config: AppConfig, side: RobotSide, axisKey: keyof ArmSoftLimitConfig, axisIndex: number) {
  const effective = effectiveAxisLimitUi(config, side, axisKey, axisIndex)
  const originUi = originAxisUi(config, side, axisIndex)
  if (originUi === null) return effective
  return {
    min: effective.min - originUi,
    max: effective.max - originUi,
    blocked: effective.blocked,
  }
}
/** 格式化对应数值用于界面展示。 */

const manualAxisOrder: ManualControlAxis[] = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const speedModeOptions: { value: ManualSpeedMode; label: string }[] = [
  { value: 'fine', label: '精调' },
  { value: 'medium', label: '中速' },
  { value: 'coarse', label: '粗调' },
]
/** 渲染当前界面单元，并连接所需数据。 */

function manualAxisUnit(axis: ManualControlAxis) {
  return manualAxisOrder.indexOf(axis) < 3 ? 'um' : '°'
}
/** 计算或执行手动控制的对应逻辑。 */
function manualAxisSoftKey(axis: ManualControlAxis): keyof ArmSoftLimitConfig {
  return axis === 'X' ? 'x' : axis === 'Y' ? 'y' : axis === 'Z' ? 'z' : axis === 'Roll' ? 'roll' : axis === 'Pitch' ? 'pitch' : 'yaw'
}
/** 计算或执行手动控制的对应逻辑。 */
function manualAxisPulsePerUiUnit(config: AppConfig, side: RobotSide, axisIndex: number) {
  const kinematics = config.motion.kinematics
  const signed = side === 'left' ? kinematics.leftSignedPulsePerUnit : kinematics.rightSignedPulsePerUnit
  const unsigned = side === 'left' ? kinematics.leftPulsePerUnit : kinematics.rightPulsePerUnit
  const pulsePerUnit = Math.abs(Number(signed?.[axisIndex] ?? unsigned?.[axisIndex] ?? 0))
  if (!Number.isFinite(pulsePerUnit) || pulsePerUnit <= 0) return 0
  return axisIndex < 3 ? pulsePerUnit / 1000 : pulsePerUnit
}
/** 计算或执行手动控制的对应逻辑。 */
function manualAxisStepLimit(config: AppConfig, side: RobotSide, axisIndex: number, speedMode: ManualSpeedMode) {
  const pulsePerUiUnit = manualAxisPulsePerUiUnit(config, side, axisIndex)
  return manualAxisStepLimitFromPulse(pulsePerUiUnit, axisIndex >= 3, speedMode)
}

/** 计算对应的业务值或展示值。 */
function clampManualAxisStep(value: number, limit: number) {
  return Math.min(Math.max(0, value), limit)
}
/** 格式化对应数值用于界面展示。 */
function formatManualStepValue(value: number, unit: string) {
  if (!Number.isFinite(value)) return '-'
  return unit === 'um' ? value.toFixed(0) : value.toFixed(3)
}
/** 格式化对应数值用于界面展示。 */
function formatManualAction(action: ManualControlAction) {
  const operatorSide = operatorSideForHardwareSide(action.side)
  if (action.type === 'arm-axis') {
    const side = operatorSideLabel(operatorSide)
    return `${side} ${action.axis} ${action.delta >= 0 ? '+' : ''}${action.delta.toFixed(action.unit === 'um' ? 1 : 3)}${action.unit}`
  }
  const side = operatorSide === 'left' ? '左夹爪' : '右夹爪'
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
/** 渲染当前界面单元，并连接所需数据。 */
export function ManualArmControl({
  side,
  positions,
  config,
  manualControl,
  nowMs,
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
  selectManualAxis: (side: RobotSide, axis: ManualControlAxis) => void
  setManualAxisStep: (unit: 'um' | '°', value: number) => void
  setManualSpeedMode: (mode: ManualSpeedMode) => void
  issueManualAxisMove: (side: RobotSide, axis: ManualControlAxis, direction: -1 | 1) => void
  triggerEmergencyStop: () => void
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const hardwareSide = hardwareSideForOperatorSide(side)
  const sideSpec = armHardwareSpecs[hardwareSide]
  const motionCardNo = hardwareSide === 'left' ? config.motion.leftCardNo : config.motion.rightCardNo
  const operatorLabel = operatorSideLabel(side)
  const selectedAxis = manualControl.selectedSide === hardwareSide ? manualControl.selectedAxis : 'X'
  const axisIndex = manualAxisOrder.indexOf(selectedAxis)
  const axisKey = manualAxisSoftKey(selectedAxis)
  const unit = manualAxisUnit(selectedAxis)
  const position = positions[sideSpec.stateOffset + axisIndex] ?? 0
  const displayLimits = displayAxisLimitForTelemetry(config, hardwareSide, axisKey, axisIndex)
  const translationSoftLimitDisabled = axisIndex < 3
  const stepValue = unit === 'um' ? manualControl.axisStepUm : manualControl.axisStepDeg
  const stepLimit = manualAxisStepLimit(config, hardwareSide, axisIndex, manualControl.speedMode)
  const boundedStepValue = clampManualAxisStep(stepValue, stepLimit)
  const manualAxisBlocked = displayLimits.blocked
  const manualAxisBlockedText = 'work_origin_missing'
  const softMargin = manualAxisBlocked || translationSoftLimitDisabled ? 0 : Math.min(Math.abs(position - displayLimits.min), Math.abs(displayLimits.max - position))
  const profile = hardwareSide === 'left' ? config.motion.leftProfile : config.motion.rightProfile
  const group = axisIndex < 3 ? profile.translation : profile.rotation
  const effectiveMaxSpeed = manualMaxVelocity(group.maxSpeed, axisIndex < 3 ? 20000 : 30, manualControl.speedMode)
  const busyKey = `${hardwareSide}-${selectedAxis}`
  const busyUntil = manualControl.axisBusyUntil[busyKey] ?? 0
  const axisBusy = busyUntil > nowMs
  const busyText = `${Math.max(0, (busyUntil - nowMs) / 1000).toFixed(1)}s`
  const stepLimitHint =
    axisIndex >= 3 && manualControl.speedMode === 'coarse'
      ? `${manualAxisStepLimitPulse} pulse · ≈2° 分段执行`
      : `${manualAxisStepLimitPulse} pulse`
  const speedUnit = axisIndex < 3 ? 'um/s' : '°/s'
  const originValid = hardwareSide === 'left' ? config.motion.origin.leftValid : config.motion.origin.rightValid
  const originHint = originValid ? '相对工作原点' : '未记录工作原点，显示 HAL 绝对位置'
  const [pendingMotionAction, setPendingMotionAction] = useState<'stop' | null>(null)
  const motionEnable = useMotionEnable(hardwareSide)
  const selectedAxisEnabled = motionEnable.axes?.[axisIndex]
  const motionReady = mockMode || (motionEnable.canEnable && selectedAxisEnabled === true)
 /** 停止对应流程。 */
 const stopMotion = async () => {
    setPendingMotionAction('stop')
    try {
      await stopMotionSide(hardwareSide)
      injectLog('WARNING', `${operatorLabel} manual stop requested`, '[HAL]')
    } catch (error) {
      injectLog('ERROR', `${operatorLabel} manual stop failed: ${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingMotionAction(null)
    }
  }

  return (
    <article className={`manual-arm-card ${manualControl.selectedSide === hardwareSide ? 'manual-card-active' : ''}`}>
      <div className="manual-card-head">
        <div>
          <UiTitle level={3}>{operatorLabel}手动控制</UiTitle>
          <UiText secondary>Card {motionCardNo} · {sideSpec.axisOrder.join(' / ')}</UiText>
        </div>
        <UiSpace wrap>
          <UiTag tone={motionEnable.deviceState === 'enabled' ? 'success' : motionEnable.deviceState === 'unknown' ? 'muted' : 'warning'}>
            {motionEnable.deviceLabel}
          </UiTag>
          {motionEnable.commandLabel && <span role="status" aria-label={`${operatorLabel}手动控制使能进度`}>{motionEnable.commandLabel}</span>}
          <UiTag tone={originValid ? 'success' : 'warning'}>{originValid ? '工作原点已设置' : '工作原点未设置'}</UiTag>
          <UiTag>以实时设备反馈为准</UiTag>
          <UiTag tone="processing">{manualControl.speedMode}</UiTag>
        </UiSpace>
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
              const active = manualControl.selectedSide === hardwareSide && manualControl.selectedAxis === axis
              return (
                <UiButton key={axis} variant={active ? 'primary' : 'default'} onClick={() => selectManualAxis(hardwareSide, axis)}>
                  {axis}
                </UiButton>
              )
            })}
          </div>
        </div>

        <div className="manual-axis-controls">
          <div className="manual-readout-row">
            <MetricBox label="当前轴" value={selectedAxis} />
            <MetricBox label="相对工作原点" value={`${position.toFixed(unit === 'um' ? 1 : 3)} ${unit}`} hint={originHint} tone={originValid ? 'neutral' : 'warn'} />
            <MetricBox
              label="软限位余量"
              value={translationSoftLimitDisabled ? '已取消' : manualAxisBlocked ? manualAxisBlockedText : `${softMargin.toFixed(unit === 'um' ? 0 : 2)} ${unit}`}
              tone={manualAxisBlocked ? 'warn' : 'ok'}
            />
            <MetricBox
              label="最大速度"
              value={`${effectiveMaxSpeed.toFixed(axisIndex < 3 ? 0 : 2)} ${speedUnit}`}
              hint={`${manualControl.speedMode} · 配置 ${group.maxSpeed} ${speedUnit}`}
            />
            <MetricBox
              label="单次上限"
              value={`${formatManualStepValue(stepLimit, unit)} ${unit}`}
              hint={stepLimitHint}
            />
          </div>
          <div className="manual-command-form manual-command-form-arm ui-form">
            <UiField label={`目标增量 ${unit}`}>
              <UiNumber
                min={0}
                max={Number.isFinite(stepLimit) ? stepLimit : undefined}
                step={unit === 'um' ? 10 : 0.1}
                value={boundedStepValue}
                onChange={(value) => setManualAxisStep(unit, clampManualAxisStep(Number(value ?? 0), stepLimit))}
              />
            </UiField>
            <UiField label="速度档位">
              <UiSelect value={manualControl.speedMode} options={speedModeOptions} onChange={setManualSpeedMode} />
            </UiField>
            <UiField label="软限位范围">
              <input
                className="ui-input"
                readOnly
                value={translationSoftLimitDisabled ? 'XYZ 软件限位已取消，仅保留机械限位 / 急停' : manualAxisBlocked ? manualAxisBlockedText : `${formatSoftLimitValue(displayLimits.min, axisIndex)} ~ ${formatSoftLimitValue(displayLimits.max, axisIndex)} ${unit}`}
              />
            </UiField>
          </div>
          <div className="manual-action-row">
            <UiButton disabled={axisBusy || !motionReady || manualAxisBlocked} onClick={() => issueManualAxisMove(hardwareSide, selectedAxis, -1)}>
              {axisBusy ? busyText : `-${boundedStepValue}${unit}`}
            </UiButton>
            <UiButton variant="primary" disabled={axisBusy || !motionReady || manualAxisBlocked} onClick={() => issueManualAxisMove(hardwareSide, selectedAxis, 1)}>
              {axisBusy ? busyText : `+${boundedStepValue}${unit}`}
            </UiButton>
            <UiButton icon={<Square size={15} />} loading={pendingMotionAction === 'stop'} onClick={() => void stopMotion()}>
              停止
            </UiButton>
            <UiButton icon={<Activity size={15} />} onClick={() => injectLog('INFO', `${operatorLabel} manual self-check requested`, '[HAL]')}>
              检查
            </UiButton>
            <UiButton
              icon={<PlugZap size={15} />}
              danger={!motionEnable.nextEnabled}
              onClick={() => motionEnable.setMotionEnabled(hardwareSide, motionEnable.nextEnabled)}
            >
              {motionEnable.nextEnabled ? '使能' : '断使能'}
            </UiButton>
            <UiButton danger icon={<ShieldAlert size={15} />} onClick={triggerEmergencyStop}>
              急停
            </UiButton>
          </div>
        </div>
      </div>
    </article>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export function ManualMemoryRow({
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
      <UiSpace>
        <UiButton icon={replaying ? <Pause size={14} /> : <Play size={14} />} onClick={() => (replaying ? pauseManualReplay() : replayManualMemory(memory.id))}>
          {replaying ? '暂停' : '回放'}
        </UiButton>
        <UiButton icon={<Trash2 size={14} />} onClick={() => deleteManualMemory(memory.id)} />
      </UiSpace>
    </div>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export function ManualReplayPanel({
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
 /** 描述当前方法的功能边界。 */
 const saveMemory = () => {
    saveManualMemory(memoryName)
    setMemoryName('')
  }
  return (
    <article className="manual-replay-panel">
      <div className="manual-card-head">
        <div>
          <UiTitle level={3}>动作记忆与回放</UiTitle>
          <UiText secondary>记录网页端下发的轴动作和左右夹爪动作；回放时仍按硬件安全限幅逐条执行。</UiText>
        </div>
        <UiTag tone={manualControl.recording ? 'error' : manualControl.replayingMemoryId ? 'processing' : 'default'}>
          {manualControl.recording ? '记录中' : manualControl.replayingMemoryId ? '回放队列' : '待命'}
        </UiTag>
      </div>
      <div className="manual-recorder-row">
        <UiInput placeholder="动作记忆名称" value={memoryName} onChange={(event) => setMemoryName(event.target.value)} />
        <UiButton variant="primary" icon={<Activity size={15} />} disabled={manualControl.recording} onClick={startManualRecording}>
          开始记录
        </UiButton>
        <UiButton icon={<Square size={15} />} disabled={!manualControl.recording} onClick={stopManualRecording}>
          停止记录
        </UiButton>
        <UiButton icon={<Save size={15} />} disabled={manualControl.draftActions.length === 0} onClick={saveMemory}>
          保存动作记忆
        </UiButton>
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
/** 渲染当前界面单元，并连接所需数据。 */
export function ManualControlPanel({
  positions,
  grippers,
  config,
  updateConfig,
  manualControl,
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
  requestComparison,
}: {
  positions: number[]
  grippers: number[]
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  manualControl: ManualControlState
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
  requestComparison: (comparison: PendingComparison) => void
}) {
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [])
  const selectedOperatorSide = operatorSideForHardwareSide(manualControl.selectedSide)
  return (
    <section id="manual" className="manual-control-page">
      <div className="hardware-panel" style={{ paddingBottom: 12 }}>
        <div className="hardware-section-title">
          <span>
            <Activity size={17} />
            手动控制
          </span>
          <UiText secondary style={{ fontSize: 12 }}>
            左右臂点动 · 夹爪开合 · 动作录制与回放
          </UiText>
        </div>
        <div className="manual-page-summary" style={{ paddingTop: 10 }}>
          <MetricBox label="当前选择" value={`${operatorSideLabel(selectedOperatorSide)} ${manualControl.selectedAxis}`} />
          <MetricBox label="平移步长" value={`${manualControl.axisStepUm} um`} />
          <MetricBox label="旋转步长" value={`${manualControl.axisStepDeg} °`} />
          <MetricBox label="记录动作" value={`${manualControl.draftActions.length} steps`} tone={manualControl.recording ? 'warn' : 'neutral'} />
        </div>
        <div className="manual-control-grid">
          {sideOrder.map((side) => (
            (() => {
              return (
                <ManualArmControl
                  key={side}
                  side={side}
                  positions={positions}
                  config={config}
                  manualControl={manualControl}
                  nowMs={nowMs}
                  selectManualAxis={selectManualAxis}
                  setManualAxisStep={setManualAxisStep}
                  setManualSpeedMode={setManualSpeedMode}
                  issueManualAxisMove={issueManualAxisMove}
                  triggerEmergencyStop={triggerEmergencyStop}
                  injectLog={injectLog}
                />
              )
            })()
          ))}
          {sideOrder.map((side) => (
            (() => {
              const hardwareSide = hardwareSideForOperatorSide(side)
              return (
                <ManualGripperControl
                  key={side}
                  side={side}
                  config={config}
                  updateConfig={updateConfig}
                  currentMm={grippers[hardwareSide === 'left' ? 0 : 1] ?? -1}
                  issueManualGripperMove={issueManualGripperMove}
                  requestComparison={requestComparison}
                />
              )
            })()
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
      </div>
    </section>
  )
}
