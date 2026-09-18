/*
 * 运动控制卡 + 轴映射 + 旋转工作限；从 SettingsView 按域拆出。
 * 先看：MotionCard → AxisMappingTable → RotationWorkLimitPanel。
 */
import {
  captureMotionOrigin,
  homeMotionSide,
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
  MotionOriginConfig,
  ParameterSnapshotScope,
  RotationWorkLimitSideConfig,
} from '../../types'
import { HardwareConfigCard, MetricBox, commandLog, type PendingComparison } from './shared'
import { formatSnapshotTime } from './motionHelpers'

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
                <td className="numeric-cell">{pulse.toFixed(axis.axis === 'X' || axis.axis === 'Z' ? 4 : 3)}</td>
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
  const [pendingMotionAction, setPendingMotionAction] = useState<'home' | null>(null)
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
 const handleHome = async () => {
    const reason = controlSafetyBlockReason(useTelemetryStore.getState())
    if (reason) {
      injectLog('WARNING', `${operatorLabel}回硬件零点受阻：${reason}`, '[HAL]')
      return
    }
    setPendingMotionAction('home')
    try {
      await homeMotionSide(hardwareSide)
      commandLog(injectLog, '[HAL]', `${operatorLabel}回硬件零点完成（未写入工作原点）`)
    } catch (error) {
      injectLog('ERROR', `${operatorLabel}回硬件零点失败：${commandErrorMessage(error)}`, '[HAL]')
    } finally {
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
 const requestHome = () => {
    const reason = controlSafetyBlockReason(useTelemetryStore.getState())
    if (reason) {
      injectLog('WARNING', `${operatorLabel}回硬件零点受阻：${reason}`, '[HAL]')
      return
    }
    requestComparison({
      title: `${operatorLabel}回硬件零点`,
      tone: 'danger',
      impact: `将通过 HAL 调用 ${operatorLabel} LTDMC HOME 回零流程；本动作不会写入工作原点记录。`,
      expected: '确认前请确认工作区安全；确认后只移动硬件轴，不更改 homeReference、工作原点或软限位。',
      current: [
        { label: '使能状态', value: motionStateText },
        { label: '当前位置', value: sidePositionsText || '--' },
      ],
      proposed: [
        { label: '目标动作', value: '硬件HOME（不写入）' },
        { label: '命令接口', value: 'motion.home_side' },
      ],
      confirmText: '确认回硬件零点',
      onConfirm: handleHome,
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
          <UiButton
            icon={<RotateCcw size={15} />}
            loading={pendingMotionAction === 'home'}
            disabled={Boolean(controlBlockReason)}
            title={controlBlockReason ?? undefined}
            onClick={requestHome}
          >
            回硬件零点
          </UiButton>
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
