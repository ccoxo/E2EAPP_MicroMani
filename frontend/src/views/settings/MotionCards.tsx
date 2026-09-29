/*
 * 运动控制卡 + 轴映射 + 旋转工作限；从 SettingsView 按域拆出。
 * 先看：MotionCard → AxisMappingTable → RotationWorkLimitPanel。
 */
import {
  captureMotionOrigin,
  fetchMotionOrigin,
  homeMotionSide,
  referencePositiveLimitSide,
  returnHardwareReferenceSide,
  restorePreviousMotionOrigin,
  type MotionOriginCaptureDrift,
  type MotionPreviousRestoreStatus,
} from '../../api'
import { Cpu, Crosshair, PlugZap, RefreshCw, RotateCcw, Save, ShieldAlert, Usb } from 'lucide-react'
import { useState } from 'react'
import {
  UiButton,
  UiField,
  UiNumber,
  UiSelect,
  UiSpace,
  UiSwitch,
  UiTag,
  UiText,
} from '../../components/ui'
import { useMotionEnable } from '../../hooks/useMotionEnable'
import { useTelemetryStore } from '../../stores/telemetry'
import { controlSafetyBlockReason } from '../../utils/controlSafety'
import { ParameterSnapshotMenu, type SnapshotMenuData } from './ParameterSnapshotMenu'
import {
  armHardwareSpecs,
  axisHardwareSpecs,
  hardwareChannelLabel,
  hardwareSideForOperatorSide,
  motionCardModelByNo,
  operatorSideForHardwareSide,
  operatorSideLabel,
  type RobotSide,
} from '../../data'
import type {
  AppConfig,
  ArmMotionProfile,
  ArmSoftLimitConfig,
  LogEntry,
  ManualControlAxis,
  MotionOriginConfig,
  ParameterSnapshotScope,
  RotationWorkLimitSideConfig,
} from '../../types'
import { HardwareConfigCard, MetricBox, type PendingComparison } from './shared'
import { commandLog } from './sharedHelpers'
import { formatSnapshotTime } from './motionHelpers'

const REFERENCE_AXES: ManualControlAxis[] = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']

const TRANSLATION_SOFT_LIMIT_DISABLED_MIN = -1000000000
const TRANSLATION_SOFT_LIMIT_DISABLED_MAX = 1000000000

function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function originDriftFromError(error: unknown): MotionOriginCaptureDrift | null {
  const apiError = error as import('../../api').ApiCommandError
  return apiError?.code === 'ORIGIN_DRIFT_CONFIRM_REQUIRED' && apiError.drift ? apiError.drift : null
}

function formatOriginDrift(drift: MotionOriginCaptureDrift) {
  const items = drift.sides.flatMap((side) =>
    side.axes.map((axis) => {
      const operatorSide = operatorSideForHardwareSide(side.side)
      const sideLabel = operatorSide === 'left' ? '左' : '右'
      const precision = axis.unit === 'um' ? 0 : 3
      return `${sideLabel}.${axis.axis} ${axis.absDeltaUi.toFixed(precision)} ${axis.unit}`
    }),
  )
  if (items.length === 0) return '未超过阈值'
  return items.length > 4 ? `${items.slice(0, 4).join('；')}；另 ${items.length - 4} 项` : items.join('；')
}

function formatPulseList(values: number[]) {
  return values
    .slice(0, 6)
    .map((value) => {
      const numeric = Number(value)
      if (!Number.isFinite(numeric)) return '0'
      return Number.isInteger(numeric) ? numeric.toFixed(0) : numeric.toFixed(3).replace(/\.?0+$/, '')
    })
    .join(',')
}

function formatWorkOriginPosition(origin: MotionOriginConfig) {
  return `工作原点位置：左[${formatPulseList(origin.leftPulse)}] 右[${formatPulseList(origin.rightPulse)}]`
}

function motionSnapshotScope(side: RobotSide): ParameterSnapshotScope {
  return side === 'left' ? 'motion-left' : 'motion-right'
}

const softLimitRows = [
  { key: 'x', label: 'X', unit: 'µm' },
  { key: 'y', label: 'Y', unit: 'µm' },
  { key: 'z', label: 'Z', unit: 'µm' },
  { key: 'roll', label: 'Roll', unit: '°' },
  { key: 'pitch', label: 'Pitch', unit: '°' },
  { key: 'yaw', label: 'Yaw', unit: '°' },
] as const
const rotationLimitRows = softLimitRows.slice(3)
const defaultRotationWorkLimits: RotationWorkLimitSideConfig = {
  roll: { min: -100, max: 100 },
  pitch: { min: -100, max: 100 },
  yaw: { min: -7, max: 7 },
}

/** 格式化对应数值用于界面展示。 */

function formatAxisValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? `${value.toFixed(1)} µm` : `${value.toFixed(3)}°`
}
/** 格式化对应数值用于界面展示。 */
function displaySoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value : value / 1000
}
/** Convert displayed rotation degrees back to stored millidegrees. */
function configSoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value : value * 1000
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
 /** 描述当前方法的功能边界。 */
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
 /** 描述当前方法的功能边界。 */
 const updateLimit = (axis: keyof ArmSoftLimitConfig, bound: 'min' | 'max', value: number) => {
    onLimitChange({
      ...limits,
      [axis]: {
        ...limits[axis],
        [bound]: value,
      },
    })
  }
 /** 描述当前方法的功能边界。 */
 const renderProfileInput = (
    group: keyof ArmMotionProfile,
    field: keyof ArmMotionProfile[keyof ArmMotionProfile],
    step = 1,
  ) => (
    <UiNumber
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
            <th>相对工作原点位置</th>
            <th>脉冲当量</th>
            <th>初始速度</th>
            <th>最大速度</th>
            <th>加速时间</th>
            <th>减速时间</th>
            <th>绝对软限位下限</th>
            <th>绝对软限位上限</th>
          </tr>
        </thead>
        <tbody>
          {axisHardwareSpecs.map((axis, index) => {
            const pulse = side === 'left' ? axis.leftPulsePerUnit : axis.rightPulsePerUnit
            const group = index < 3 ? 'translation' : 'rotation'
            const axisKey = softLimitRows[index].key
            const minLimit = displaySoftLimitValue(limits[axisKey].min, index)
            const maxLimit = displaySoftLimitValue(limits[axisKey].max, index)
            const translationSoftLimitDisabled = index < 3
            return (
              <tr key={axis.axis} className={axis.warning ? 'axis-row-warning' : ''}>
                <td><b>{axis.axis}</b></td>
                <td>axis {sideSpec.axisOrder[index]}</td>
                <td>
                  <b className="axis-model">{axis.model}</b>
                  <span className="axis-travel">{axis.travel}</span>
                </td>
                <td className="numeric-cell">{formatAxisValue(positions[sideSpec.stateOffset + index] ?? 0, index)}</td>
                <td className="numeric-cell">{pulse.toFixed(axis.axis === 'X' || axis.axis === 'Z' || (side === 'right' && axis.axis === 'Yaw') ? 4 : 3)}</td>
                <td>{renderProfileInput(group, 'startSpeed', group === 'translation' ? 0.1 : 0.01)}</td>
                <td>{renderProfileInput(group, 'maxSpeed', group === 'translation' ? 0.1 : 0.01)}</td>
                <td>{renderProfileInput(group, 'accTimeSec', 0.01)}</td>
                <td>{renderProfileInput(group, 'decTimeSec', 0.01)}</td>
                <td>
                  {translationSoftLimitDisabled ? (
                    <UiTag>已取消</UiTag>
                  ) : (
                    <UiNumber
                      className="axis-map-input axis-limit-input"
                      step={0.1}
                      value={minLimit}
                      onChange={(value) => updateLimit(axisKey, 'min', configSoftLimitValue(Number(value ?? 0), index))}
                    />
                  )}
                </td>
                <td>
                  {translationSoftLimitDisabled ? (
                    <UiTag tone="processing">机械限位 / 急停</UiTag>
                  ) : (
                    <span className="axis-limit-field">
                      <UiNumber
                        className="axis-map-input axis-limit-input"
                        step={0.1}
                        value={maxLimit}
                        onChange={(value) => updateLimit(axisKey, 'max', configSoftLimitValue(Number(value ?? 0), index))}
                      />
                      <span className="axis-unit">{axis.unit}</span>
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="axis-map-note">
        <UiTag>平移单位 um，速度 um/s</UiTag>
        <UiTag>旋转界面单位 °，配置存储 mdeg</UiTag>
        <UiTag>LTDMC profile 使用初始速度、最大速度、加速时间、减速时间</UiTag>
        <UiTag tone="processing">XYZ 软件软限位已取消，仅保留机械限位 / 急停</UiTag>
      </div>
    </div>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
function RotationWorkLimitPanel({
  side,
  config,
  updateConfig,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
}) {
  const root = config.motion.rotationWorkLimits ?? {
    enabled: true,
    left: defaultRotationWorkLimits,
    right: defaultRotationWorkLimits,
  }
  const enabled = Boolean(root.enabled)
  const sideLimits = rotationWorkLimitsForSide(config, side)
  const sideOriginValid = side === 'left' ? config.motion.origin.leftValid : config.motion.origin.rightValid
  /** 描述当前方法的功能边界。 */
  const updateRoot = (patch: Partial<AppConfig['motion']['rotationWorkLimits']>) =>
    updateConfig({
      motion: {
        ...config.motion,
        rotationWorkLimits: {
          ...root,
          ...patch,
          left: patch.left ?? root.left ?? defaultRotationWorkLimits,
          right: patch.right ?? root.right ?? defaultRotationWorkLimits,
        },
      },
    })
  /** 描述当前方法的功能边界。 */
  const updateLimit = (axis: keyof RotationWorkLimitSideConfig, bound: 'min' | 'max', value: number) => {
    updateRoot({
      [side]: {
        ...sideLimits,
        [axis]: {
          ...sideLimits[axis],
          [bound]: value,
        },
      },
    })
  }
  return (
    <div className="rotation-work-panel">
      <div className="hardware-subtitle-row">
        <b>旋转工作窗口</b>
        <UiSpace wrap>
          <UiTag tone={sideOriginValid ? 'success' : 'warning'}>{sideOriginValid ? 'Origin ready' : 'Origin missing'}</UiTag>
          <UiSwitch
            checked={enabled}
            checkedChildren="On"
            unCheckedChildren="Off"
            onChange={(checked) => updateRoot({ enabled: checked })}
          />
        </UiSpace>
      </div>
      <table className="rotation-work-table">
        <thead>
          <tr>
            <th>Axis</th>
            <th>Origin abs</th>
            <th>Work min</th>
            <th>Work max</th>
            <th>Mechanical abs</th>
            <th>Effective abs</th>
          </tr>
        </thead>
        <tbody>
          {rotationLimitRows.map((row, rowIndex) => {
            const axisIndex = rowIndex + 3
            const axisKey = row.key as keyof RotationWorkLimitSideConfig
            const originUi = originAxisUi(config, side, axisIndex)
            const mechanical = softLimitConfigForSide(config, side)[axisKey]
            const mechanicalMin = displaySoftLimitValue(mechanical.min, axisIndex)
            const mechanicalMax = displaySoftLimitValue(mechanical.max, axisIndex)
            const effective = effectiveAxisLimitUi(config, side, axisKey, axisIndex)
            return (
              <tr key={row.key}>
                <td><b>{row.label}</b></td>
                <td className="numeric-cell">{originUi === null ? '-' : `${originUi.toFixed(3)}°`}</td>
                <td>
                  <UiNumber
                    className="axis-map-input axis-limit-input"
                    disabled={!enabled}
                    step={0.1}
                    value={sideLimits[axisKey].min}
                    onChange={(value) => updateLimit(axisKey, 'min', Number(value ?? 0))}
                  />
                </td>
                <td>
                  <UiNumber
                    className="axis-map-input axis-limit-input"
                    disabled={!enabled}
                    step={0.1}
                    value={sideLimits[axisKey].max}
                    onChange={(value) => updateLimit(axisKey, 'max', Number(value ?? 0))}
                  />
                </td>
                <td className="numeric-cell">{mechanicalMin.toFixed(3)} ~ {mechanicalMax.toFixed(3)}°</td>
                <td className="numeric-cell">
                  {effective.blocked ? <UiTag tone="error">work_origin_missing</UiTag> : `${effective.min.toFixed(3)} ~ ${effective.max.toFixed(3)}°`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export function MotionCard({
  side,
  config,
  updateConfig,
  focusHash,
  positions,
  injectLog,
  triggerEmergencyStop,
  snapshotMenu,
  openSnapshotModal,
  requestComparison,
  previousRestoreStatus,
  refreshMotionOriginStatus,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  positions: number[]
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
  triggerEmergencyStop: () => void
  snapshotMenu: (scope: ParameterSnapshotScope) => SnapshotMenuData
  openSnapshotModal: (scope: ParameterSnapshotScope) => void
  requestComparison: (comparison: PendingComparison) => void
  previousRestoreStatus: MotionPreviousRestoreStatus | null
  refreshMotionOriginStatus: () => Promise<void>
}) {
  const hardwareSide = hardwareSideForOperatorSide(side)
  const sideSpec = armHardwareSpecs[hardwareSide]
  const operatorLabel = operatorSideLabel(side)
  const id = `motion-${side}`
  const snapshotScope = motionSnapshotScope(hardwareSide)
  const configCardNo = hardwareSide === 'left' ? config.motion.leftCardNo : config.motion.rightCardNo
  const cardModel = motionCardModelByNo[configCardNo] ?? 'DMC'
  const profileKey = hardwareSide === 'left' ? 'leftProfile' : 'rightProfile'
  const softLimitKey = hardwareSide === 'left' ? 'leftSoftLimits' : 'rightSoftLimits'
 /** 描述当前方法的功能边界。 */
 const updateCardNo = (cardNo: number) =>
    updateConfig({ motion: { ...config.motion, [hardwareSide === 'left' ? 'leftCardNo' : 'rightCardNo']: cardNo } })
 /** 描述当前方法的功能边界。 */
 const updateProfile = (nextProfile: ArmMotionProfile) =>
    updateConfig({ motion: { ...config.motion, [profileKey]: nextProfile } })
 /** 描述当前方法的功能边界。 */
 const updateSoftLimits = (nextLimits: ArmSoftLimitConfig) =>
    updateConfig({ motion: { ...config.motion, [softLimitKey]: nextLimits } })
  const motionOrigin = config.motion.origin
  const sideOriginValid = hardwareSide === 'left' ? motionOrigin.leftValid : motionOrigin.rightValid
  const originStatusText = sideOriginValid ? '已记录' : '未记录'
  const originScopeText =
    sideOriginValid
      ? motionOrigin.valid
        ? '双侧工作原点都已记录'
        : '仅当前侧工作原点已记录'
      : '当前侧工作原点未记录'
  const originUpdatedText = motionOrigin.updatedAt > 0 ? `最后更新 ${formatSnapshotTime(motionOrigin.updatedAt)}` : originScopeText
  const [pendingMotionAction, setPendingMotionAction] = useState<'return' | 'seek' | 'limit' | null>(null)
  const [referenceAction, setReferenceAction] = useState<'return' | 'seek' | 'limit' | null>(null)
  const [selectedAxes, setSelectedAxes] = useState<ManualControlAxis[]>([])
  const [referenceStatus, setReferenceStatus] = useState('')
  const reference = config.motion.homeReference
  const confirmedAxes = reference[hardwareSide === 'left' ? 'leftAxisConfirmed' : 'rightAxisConfirmed'] ?? []
  const referencePulses = reference[hardwareSide === 'left' ? 'leftPulse' : 'rightPulse']
  const unconfirmedSelection = selectedAxes.filter((axis) => confirmedAxes[REFERENCE_AXES.indexOf(axis)] !== true)
  const positiveLimitAxes: ManualControlAxis[] = hardwareSide === 'right' ? ['X', 'Z'] : ['X', 'Y']
  const invalidPositiveLimitSelection = selectedAxes.filter((axis) => !positiveLimitAxes.includes(axis))
  const openReferenceAction = (action: 'return' | 'seek' | 'limit') => {
    setReferenceAction(action)
    setSelectedAxes([])
    setReferenceStatus('')
  }
  const [pendingOriginAction, setPendingOriginAction] = useState<'capture' | 'restore' | null>(null)
  const motionEnable = useMotionEnable(hardwareSide)
  const controlBlockReason = useTelemetryStore((state) => controlSafetyBlockReason(state))
  const motionStateText = motionEnable.deviceLabel
  const sidePositionsText = positions
    .slice(sideSpec.stateOffset, sideSpec.stateOffset + 6)
    .map((value) => (Number.isFinite(value) ? value.toFixed(1) : '--'))
    .join(', ')
  const previousRestoreAvailable = Boolean(motionOrigin.previousValid || previousRestoreStatus?.available)
  const previousRestoreReady = previousRestoreStatus?.restorable === true
  const previousRestoreLabel = !previousRestoreAvailable
    ? '无备份'
    : previousRestoreStatus?.restorable
      ? '备份可恢复'
      : previousRestoreStatus
        ? '备份不可恢复'
        : '备份待校验'
  const previousRestoreMessage = previousRestoreAvailable ? previousRestoreStatus?.message : ''
 /** 处理对应的用户交互。 */
 // 服务端反馈只同步页面，不能经 updateConfig 再写回配置覆盖新的寻零结果。
 const syncMotionState = (patch: Pick<AppConfig, 'motion'>) => {
    useTelemetryStore.setState((state) => ({ config: { ...state.config, ...patch } }))
  }
 const handleReferenceAction = async (action: 'return' | 'seek' | 'limit', axes: ManualControlAxis[]) => {
    const label = action === 'seek' ? '\u673a\u68b0\u5bfb\u96f6' : action === 'limit' ? '\u6b63\u9650\u4f4d\u5efa\u53c2\u8003' : '\u8fd4\u56de\u673a\u68b0\u53c2\u8003\u70b9'
    let completionMessage = `${label}\u5b8c\u6210\uff1a${axes.join('\u3001')}`
    const reason = controlSafetyBlockReason(useTelemetryStore.getState())
    if (reason) {
      injectLog('WARNING', `${operatorLabel}${label}\u53d7\u963b\uff1a${reason}`, '[HAL]')
      setReferenceStatus(`${label}\u53d7\u963b\uff1a${reason}`)
      return
    }
    const generation = useTelemetryStore.getState().controlSafety.generation
    setPendingMotionAction(action)
    setReferenceStatus(`${action === 'seek' ? '\u6b63\u5728\u5bfb\u96f6' : action === 'limit' ? '\u6b63\u5728\u5bfb\u627e\u6b63\u9650\u4f4d' : '\u6b63\u5728\u8fd4\u56de'}\uff1a${axes.join('\u3001')}`)
    try {
      if (action === 'seek' || action === 'limit') {
        const nextConfirmed = REFERENCE_AXES.map((axis, index) => !axes.includes(axis) && confirmedAxes[index] === true)
        syncMotionState({ motion: { ...config.motion, homeReference: {
          ...reference, [hardwareSide === 'left' ? 'leftAxisConfirmed' : 'rightAxisConfirmed']: nextConfirmed,
        } } })
        const response = action === 'seek'
          ? await homeMotionSide(hardwareSide, axes)
          : await referencePositiveLimitSide(hardwareSide, axes)
        if (response.data?.homeReference) {
          const limitSources = response.data.homeReference[hardwareSide === 'left' ? 'leftAxisLimitReference' : 'rightAxisLimitReference'] ?? []
          const limitAxes = axes.filter((axis) => limitSources[REFERENCE_AXES.indexOf(axis)] === true)
          if (action === 'limit') completionMessage = `\u6b63\u9650\u4f4d\u53c2\u8003\u8bb0\u5f55\u5b8c\u6210\uff1a${limitAxes.join('\u3001')}`
          syncMotionState({ motion: {
            ...useTelemetryStore.getState().config.motion,
            homeReference: response.data.homeReference,
            ...(response.data.origin ? { origin: response.data.origin } : {}),
            ...(response.data.workOriginOffset ? { workOriginOffset: response.data.workOriginOffset } : {}),
          } })
        }
      } else {
        await returnHardwareReferenceSide(hardwareSide, axes)
      }
      const current = useTelemetryStore.getState()
      if (current.controlSafety.generation !== generation || controlSafetyBlockReason(current)) {
        throw new Error('\u64cd\u4f5c\u671f\u95f4\u53d1\u751f\u6025\u505c\u6216\u8fde\u63a5\u72b6\u6001\u53d8\u5316\uff0c\u8bf7\u6838\u9a8c\u6267\u884c\u7ed3\u679c')
      }
      setReferenceStatus(completionMessage)
      commandLog(injectLog, '[HAL]', `${operatorLabel}${completionMessage}`)
    } catch (error) {
      const message = `${label}\u5931\u8d25\uff1a${commandErrorMessage(error)}`
      setReferenceStatus(message)
      injectLog('ERROR', `${operatorLabel}${message}`, '[HAL]')
    } finally {
      if (action === 'seek' || action === 'limit') {
        try {
          const response = await fetchMotionOrigin()
          if (response.data?.homeReference) {
            syncMotionState({ motion: {
              ...useTelemetryStore.getState().config.motion,
              homeReference: response.data.homeReference,
              ...(response.data.origin ? { origin: response.data.origin } : {}),
              ...(response.data.workOriginOffset ? { workOriginOffset: response.data.workOriginOffset } : {}),
            } })
          }
          await refreshMotionOriginStatus()
        } catch (error) {
          injectLog('WARNING', `\u673a\u68b0\u53c2\u8003\u72b6\u6001\u5237\u65b0\u5931\u8d25\uff1a${commandErrorMessage(error)}`, '[HAL]')
        }
      }
      setPendingMotionAction(null)
    }
  }
 /** 处理对应的用户交互。 */
 const handleCaptureOrigin = async (confirmLargeDrift = false) => {
    setPendingOriginAction('capture')
    try {
      const response = await captureMotionOrigin(hardwareSide, confirmLargeDrift ? { confirmLargeDrift: true } : undefined)
      const responseMotion = response.data?.config?.motion
      const nextOrigin = response.data?.origin ?? responseMotion?.origin ?? {
        ...motionOrigin,
        leftValid: hardwareSide === 'left' ? true : motionOrigin.leftValid,
        rightValid: hardwareSide === 'right' ? true : motionOrigin.rightValid,
        valid:
          (hardwareSide === 'left' ? true : motionOrigin.leftValid) &&
          (hardwareSide === 'right' ? true : motionOrigin.rightValid),
        updatedAt: motionOrigin.updatedAt,
      }
      updateConfig({
        motion: responseMotion ?? {
          ...config.motion,
          origin: nextOrigin,
          homeReference: response.data?.homeReference ?? config.motion.homeReference,
          workOriginOffset: response.data?.workOriginOffset ?? config.motion.workOriginOffset,
        },
      })
      const drift = response.data?.originCaptureDrift
      const positionText = formatWorkOriginPosition(nextOrigin)
      await refreshMotionOriginStatus()
      commandLog(
        injectLog,
        '[HAL]',
        `${drift?.requiresConfirmation
          ? `${operatorLabel}工作原点已确认大漂移并写入`
          : `${operatorLabel}工作原点已记录`}：${positionText}`,
      )
    } catch (error) {
      const drift = originDriftFromError(error)
      if (!confirmLargeDrift && drift) {
        requestComparison({
          title: `${operatorLabel}工作原点漂移过大`,
          tone: 'danger',
          impact: '本次当前位置读数与已有工作原点记录差距超过保护阈值。未再次确认前，后端不会写入新的工作原点。',
          expected: `阈值：平移 ${drift.thresholds.translationUm.toFixed(0)} µm，旋转 ${drift.thresholds.rotationDeg.toFixed(3)}°。`,
          current: [
            { label: '当前状态', value: originStatusText },
            { label: '当前位置', value: sidePositionsText || '--' },
          ],
          proposed: [
            { label: '超限漂移', value: formatOriginDrift(drift) },
            { label: '写入策略', value: '二次确认后覆盖' },
          ],
          confirmText: '确认覆盖零点',
          onConfirm: () => handleCaptureOrigin(true),
        })
        return false
      }
      injectLog('ERROR', `${operatorLabel}工作原点记录失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingOriginAction(null)
    }
    return true
  }
  /** 处理对应的用户交互。 */
  const handleRestorePreviousOrigin = async () => {
    setPendingOriginAction('restore')
    try {
      const response = await restorePreviousMotionOrigin()
      const responseMotion = response.data?.config?.motion
      const nextOrigin = response.data?.origin ?? responseMotion?.origin ?? motionOrigin
      updateConfig({ motion: responseMotion ?? { ...config.motion, origin: nextOrigin } })
      await refreshMotionOriginStatus()
      commandLog(injectLog, '[HAL]', '已恢复上个工作原点')
    } catch (error) {
      injectLog('ERROR', `恢复上个工作原点失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
      setPendingOriginAction(null)
    }
  }
 /** 处理对应的用户交互。 */
 const requestReferenceAction = () => {
    if (!referenceAction || selectedAxes.length === 0 || pendingMotionAction) return
    if (referenceAction === 'return' && unconfirmedSelection.length > 0) return
    if (referenceAction === 'limit' && invalidPositiveLimitSelection.length > 0) return
    const action = referenceAction
    const axes = [...selectedAxes]
    const seeking = action === 'seek'
    const limitSeeking = action === 'limit'
    const label = seeking ? '\u673a\u68b0\u5bfb\u96f6' : limitSeeking ? '\u6b63\u9650\u4f4d\u5efa\u53c2\u8003' : '\u8fd4\u56de\u673a\u68b0\u53c2\u8003\u70b9'
    requestComparison({
      title: `${operatorLabel}${label}`,
      tone: 'danger',
      impact: seeking
        ? `\u4ec5\u5bf9 ${axes.join('\u3001')} \u9010\u8f74\u6267\u884c\u4e25\u683c ORG \u5bfb\u96f6\uff1b\u524d\u4e00\u8f74\u6210\u529f\u7ed3\u675f\u540e\u624d\u542f\u52a8\u4e0b\u4e00\u8f74\u3002\u5931\u8d25\u4e0d\u4f1a\u81ea\u52a8\u6539\u7528\u9650\u4f4d\u53c2\u8003\u3002`
        : limitSeeking
          ? `\u4ec5\u5bf9 ${axes.join('\u3001')} \u9010\u8f74\u5bfb\u627e\u5df2\u914d\u7f6e\u7684\u6b63\u9650\u4f4d\uff0c\u5fc5\u987b\u89c2\u5bdf\u5230 EL+ OFF->ON \u8fb9\u6cbf\u624d\u8bb0\u5f55\u53c2\u8003\uff0c\u4e0d\u4f1a\u5ba3\u79f0\u627e\u5230 ORG\u3002`
          : `\u4ec5\u5c06 ${axes.join('\u3001')} \u8fd4\u56de\u5df2\u786e\u8ba4\u7684\u673a\u68b0\u53c2\u8003\u70b9\uff0c\u4e0d\u5bfb\u96f6\u3001\u4e0d\u66f4\u65b0\u53c2\u8003\u8bb0\u5f55\u3002`,
      expected: seeking
        ? '\u786e\u8ba4\u6240\u9009\u8f74\u5bfb\u96f6\u8def\u5f84\u5b89\u5168\uff1b\u641c\u7d22\u53d7\u6bcf\u8f74\u6700\u5927\u884c\u7a0b\u548c\u8d85\u65f6\u9650\u5236\u3002'
        : limitSeeking
          ? '\u786e\u8ba4\u6240\u9009\u5e73\u79fb\u8f74\u5f53\u524d\u4e0d\u5728\u6b63\u9650\u4f4d\u4e0a\u3001\u6b63\u5411\u8def\u5f84\u65e0\u969c\u788d\uff1b\u5df2\u5728 EL+ \u4e0a\u7684\u8f74\u4f1a\u88ab\u62d2\u7edd\u3002'
          : '\u786e\u8ba4\u6240\u9009\u8f74\u8fd4\u56de\u8def\u5f84\u5b89\u5168\uff1b\u4efb\u4e00\u6240\u9009\u53c2\u8003\u672a\u786e\u8ba4\u3001\u5c5e\u4e8e\u65e7 HAL \u5b9e\u4f8b\u6216\u65cb\u8f6c\u9700\u8d85\u8fc7 180\u00b0 \u65f6\u6574\u6b21\u62d2\u7edd\u3002',
      current: [
        { label: '\u4f7f\u80fd\u72b6\u6001', value: motionStateText },
        { label: '\u5f53\u524d\u4f4d\u7f6e', value: sidePositionsText || '--' },
      ],
      proposed: [
        { label: '\u64cd\u4f5c\u8f74', value: axes.join('\u3001') },
        { label: seeking || limitSeeking ? '\u53c2\u8003\u66f4\u65b0\u8303\u56f4' : '\u76ee\u6807\u8109\u51b2', value: seeking || limitSeeking ? '\u4ec5\u6240\u9009\u8f74' : axes.map((axis) => `${axis}: ${referencePulses[REFERENCE_AXES.indexOf(axis)]}`).join('\u3001') },
      ],
      confirmText: `\u786e\u8ba4${label}`,
      onConfirm: () => handleReferenceAction(action, axes),
    })
  }
 /** 处理对应的用户交互。 */
 const requestCaptureOrigin = () =>
    requestComparison({
      title: `${operatorLabel}记录工作原点`,
      tone: 'warning',
      impact: `将当前${operatorLabel} HAL 脉冲记录为工作原点，不执行硬件 HOME。`,
      expected: '确认前请把从臂移动到期望工作原点；确认后只记录当前位置，不移动硬件。',
      current: [
        { label: '当前状态', value: originStatusText },
        { label: '当前位置', value: sidePositionsText || '--' },
      ],
      proposed: [
        { label: '当前状态', value: '已记录' },
        { label: '更新时间', value: '确认时写入' },
      ],
      confirmText: '确认记录工作原点',
      onConfirm: handleCaptureOrigin,
    })
 /** 处理对应的用户交互。 */
 const requestRestorePreviousOrigin = () => {
    if (!previousRestoreReady) {
      injectLog('WARNING', previousRestoreMessage || '上个工作原点备份暂不可恢复', '[HAL]')
      return
    }
    requestComparison({
      title: '恢复上个工作原点',
      tone: 'warning',
      impact: '将把当前工作原点记录替换为上一份备份记录。',
      expected: '确认后只切换工作原点记录，不会移动硬件。',
      current: [
        { label: '当前状态', value: originStatusText },
        { label: '范围', value: originScopeText },
      ],
      proposed: [
        { label: '当前状态', value: previousRestoreLabel },
        { label: '范围', value: '恢复上一份工作原点备份' },
      ],
      confirmText: '确认恢复',
      onConfirm: handleRestorePreviousOrigin,
    })
  }
  const rotationWindowLabel = configCardNo === 0
    ? `Roll -95~5° / Pitch ±30° · Yaw ±7°`
    : `Roll -5~95° / Pitch ±30° · Yaw ±7°`

  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Cpu size={20} />}
      title={`${operatorLabel}运动控制卡 · Card ${configCardNo}`}
      subtitle={`LTDMC/${cardModel} · ${sideSpec.configKey} · 6 轴串行控制`}
      state="ok"
      badges={
        <>
          <UiTag tone="processing">{sideSpec.axisOrder.join(',')}</UiTag>
          <UiTag tone="processing">{hardwareChannelLabel(hardwareSide)}</UiTag>
          <UiTag tone="warning">{rotationWindowLabel}</UiTag>
          <UiTag tone={motionEnable.deviceState === 'enabled' ? 'success' : motionEnable.deviceState === 'unknown' ? 'muted' : 'warning'}>
            {motionEnable.deviceLabel}
          </UiTag>
          {motionEnable.commandLabel && <span role="status" aria-label={`${operatorLabel}运动控制卡使能进度`}>{motionEnable.commandLabel}</span>}
        </>
      }
      actions={
        <UiSpace wrap>
          <UiButton icon={<PlugZap size={15} />} disabled={!motionEnable.canEnable} loading={motionEnable.busy && motionEnable.desired === true} onClick={() => motionEnable.setMotionEnabled(hardwareSide, true)}>
            使能全部
          </UiButton>
          <UiButton danger icon={<Usb size={15} />} loading={motionEnable.busy && motionEnable.desired === false} onClick={() => motionEnable.setMotionEnabled(hardwareSide, false)}>
            断使能
          </UiButton>
          <UiButton icon={<Crosshair size={15} />} loading={pendingOriginAction === 'capture'} onClick={requestCaptureOrigin}>
            记录工作原点
          </UiButton>
          <UiButton danger icon={<ShieldAlert size={15} />} onClick={triggerEmergencyStop}>
            急停
          </UiButton>
        </UiSpace>
      }
      wide
    >
      <div className="motion-card-snapshot-toolbar">
        <ParameterSnapshotMenu title="选择运动参数" menu={snapshotMenu(snapshotScope)} />
      </div>
      <div className="hardware-form-grid hardware-form-grid-compact ui-form">
        <UiField label="控制卡号">
          <UiNumber min={0} max={8} value={configCardNo} onChange={(value) => updateCardNo(Number(value ?? sideSpec.cardNo))} />
        </UiField>
        <UiField label="位置源">
          <UiSelect             value={config.motion.positionSource}
            onChange={(value: AppConfig['motion']['positionSource']) => updateConfig({ motion: { ...config.motion, positionSource: value } })}
            options={[{ value: 'dmc_get_position', label: 'dmc_get_position' }, { value: 'dmc_get_encoder', label: 'dmc_get_encoder（不建议）' }]}
          />
        </UiField>
        <UiField label="Motion Thread">
          <UiTag tone="processing">{config.motion.motionThreadHz} Hz</UiTag>
        </UiField>
        <UiField label="线程策略">
          <UiTag>串行化 LTDMC 调用</UiTag>
        </UiField>
        <UiField label="工作窗口">
          <UiTag tone="warning">{rotationWindowLabel}</UiTag>
        </UiField>
        <UiField label="参数映射">
          <UiTag>表内编辑</UiTag>
        </UiField>
      </div>
      <div className="motion-origin-panel">
        <div className="hardware-subtitle-row">
          <b>工作原点</b>
          <span>{originScopeText}</span>
        </div>
        <div className="hardware-metric-grid hardware-metric-grid-single">
          <MetricBox label="当前状态" value={originStatusText} hint={originUpdatedText} tone={sideOriginValid ? 'ok' : 'warn'} />
        </div>
        <UiText secondary>{formatWorkOriginPosition(motionOrigin)}</UiText>
        {hardwareSide === 'left' && previousRestoreAvailable && (
          <UiSpace wrap size={6}>
            <UiTag tone={previousRestoreReady ? 'success' : 'warning'}>{previousRestoreLabel}</UiTag>
            {previousRestoreMessage && (
              <UiTag tone={previousRestoreReady ? 'muted' : 'error'}>
                {previousRestoreMessage}
              </UiTag>
            )}
          </UiSpace>
        )}
        <UiSpace wrap className="motion-origin-actions">
          {hardwareSide === 'left' && (
            <UiButton
              icon={<RefreshCw size={15} />}
              loading={pendingOriginAction === 'restore'}
              disabled={!previousRestoreReady}
              onClick={requestRestorePreviousOrigin}
            >
              恢复上个工作原点
            </UiButton>
          )}
        </UiSpace>
      </div>
      <div className="motion-origin-panel">
        <div className="hardware-subtitle-row"><b>机械参考点</b><span>逐轴确认，仅作用于所选轴</span></div>
        <UiSpace wrap>
          {REFERENCE_AXES.map((axis, index) => (
            <UiTag key={axis} tone={confirmedAxes[index] === true ? 'success' : 'warning'}>
              {axis}：{confirmedAxes[index] === true ? '已确认' : '待确认'}
            </UiTag>
          ))}
        </UiSpace>
        <UiSpace wrap className="motion-origin-actions">
          <UiButton icon={<RotateCcw size={15} />} disabled={Boolean(controlBlockReason) || pendingMotionAction !== null}
            title={controlBlockReason ?? undefined} onClick={() => openReferenceAction('return')}>
            返回机械参考点
          </UiButton>
          <UiButton icon={<RefreshCw size={15} />} disabled={Boolean(controlBlockReason) || pendingMotionAction !== null}
            title={controlBlockReason ?? undefined} onClick={() => openReferenceAction('seek')}>
            机械寻零
          </UiButton>
          <UiButton icon={<Crosshair size={15} />} disabled={Boolean(controlBlockReason) || pendingMotionAction !== null}
            title={controlBlockReason ?? undefined} onClick={() => openReferenceAction('limit')}>
            {'\u6b63\u9650\u4f4d\u5efa\u53c2\u8003'}
          </UiButton>
        </UiSpace>
        {referenceAction && (
          <fieldset disabled={pendingMotionAction !== null}>
            <legend>{referenceAction === 'seek' ? '\u7ef4\u62a4\uff1a\u673a\u68b0\u5bfb\u96f6\u9009\u8f74' : referenceAction === 'limit' ? '\u7ef4\u62a4\uff1a\u6b63\u9650\u4f4d\u53c2\u8003\u9009\u8f74' : '\u8fd4\u56de\u673a\u68b0\u53c2\u8003\u70b9\u9009\u8f74'}</legend>
            <UiSpace wrap>
              {REFERENCE_AXES.map((axis) => (
                <label key={axis}>
                  <input type="checkbox" aria-label={axis} checked={selectedAxes.includes(axis)}
                    disabled={referenceAction === 'limit' && !positiveLimitAxes.includes(axis)}
                    onChange={(event) => setSelectedAxes(REFERENCE_AXES.filter((item) => item === axis ? event.target.checked : selectedAxes.includes(item)))} /> {axis}
                </label>
              ))}
              <UiButton onClick={() => setSelectedAxes(referenceAction === 'limit' ? [...positiveLimitAxes] : [...REFERENCE_AXES])}>
                {referenceAction === 'limit' ? '\u5168\u9009\u53ef\u7528\u8f74' : '\u5168\u9009\u516d\u8f74'}
              </UiButton>
              <UiButton onClick={() => setSelectedAxes([])}>{'\u6e05\u7a7a\u9009\u62e9'}</UiButton>
            </UiSpace>
            <p>{referenceAction === 'seek'
              ? '\u7ef4\u62a4\u64cd\u4f5c\uff1a\u6240\u9009\u8f74\u6309\u987a\u5e8f\u6267\u884c\u4e25\u683c ORG \u5bfb\u96f6\uff0c\u4e0d\u4f1a\u628a\u9650\u4f4d\u505c\u6b62\u5f53\u6210 HOME \u6210\u529f\u3002'
              : referenceAction === 'limit'
                ? `\u7ef4\u62a4\u64cd\u4f5c\uff1a\u53ea\u652f\u6301 ${positiveLimitAxes.join('\u3001')}\uff0c\u8981\u6c42 EL+ OFF->ON \u540e\u8bb0\u5f55\u6b63\u9650\u4f4d\u53c2\u8003\u3002`
                : '\u65e5\u5e38\u8fd4\u56de\uff1a\u4f7f\u7528\u5df2\u786e\u8ba4\u4e14\u5c5e\u4e8e\u5f53\u524d HAL \u5b9e\u4f8b\u7684\u53c2\u8003\u8bb0\u5f55\u3002'}</p>
            {referenceAction === 'return' && unconfirmedSelection.length > 0 && (
              <p role="alert">{'\u672a\u786e\u8ba4\u7684\u5df2\u9009\u8f74\uff1a'}{unconfirmedSelection.join('\u3001')}</p>
            )}
            {referenceAction === 'limit' && invalidPositiveLimitSelection.length > 0 && (
              <p role="alert">{'\u8fd9\u4e9b\u8f74\u672a\u914d\u7f6e\u6b63\u9650\u4f4d\u53c2\u8003\uff1a'}{invalidPositiveLimitSelection.join('\u3001')}</p>
            )}
            <UiButton disabled={Boolean(controlBlockReason) || selectedAxes.length === 0 || (referenceAction === 'return' && unconfirmedSelection.length > 0) || (referenceAction === 'limit' && invalidPositiveLimitSelection.length > 0)}
              onClick={requestReferenceAction}>
              {referenceAction === 'seek' ? '\u5ba1\u9605\u5bfb\u96f6\u52a8\u4f5c' : referenceAction === 'limit' ? '\u5ba1\u9605\u9650\u4f4d\u53c2\u8003\u52a8\u4f5c' : '\u5ba1\u9605\u8fd4\u56de\u52a8\u4f5c'}
            </UiButton>
            <UiButton onClick={() => { setReferenceAction(null); setSelectedAxes([]) }}>{'\u53d6\u6d88'}</UiButton>
          </fieldset>
        )}
        <p role="status" aria-label={`${operatorLabel}原点操作状态`}>{referenceStatus || '尚未执行原点操作'}</p>
      </div>
      <AxisMappingTable
        side={hardwareSide}
        positions={positions}
        profile={config.motion[profileKey]}
        limits={config.motion[softLimitKey]}
        onProfileChange={updateProfile}
        onLimitChange={updateSoftLimits}
      />
      <RotationWorkLimitPanel side={hardwareSide} config={config} updateConfig={updateConfig} />
      <div className="motion-card-snapshot-footer">
        <UiButton variant="primary" icon={<Save size={15} />} onClick={() => openSnapshotModal(snapshotScope)}>
          保存运动参数
        </UiButton>
      </div>
    </HardwareConfigCard>
  )
}
