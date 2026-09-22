/*
 * 运动软限位与工作限共用 helper；供 MotionCards 与 Settings 手动控制共用。
 */
import type { ApiCommandError, MotionOriginCaptureDrift } from '../../api'
import { operatorSideForHardwareSide, type RobotSide } from '../../data'
import type {
  AppConfig,
  ArmSoftLimitConfig,
  MotionOriginConfig,
  RotationWorkLimitSideConfig,
} from '../../types'

export const TRANSLATION_SOFT_LIMIT_DISABLED_MIN = -1000000000
export const TRANSLATION_SOFT_LIMIT_DISABLED_MAX = 1000000000

export function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

export function originDriftFromError(error: unknown): MotionOriginCaptureDrift | null {
  const apiError = error as ApiCommandError
  return apiError?.code === 'ORIGIN_DRIFT_CONFIRM_REQUIRED' && apiError.drift ? apiError.drift : null
}

export function formatOriginDrift(drift: MotionOriginCaptureDrift) {
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

export function formatPulseList(values: number[]) {
  return values
    .slice(0, 6)
    .map((value) => {
      const numeric = Number(value)
      if (!Number.isFinite(numeric)) return '0'
      return Number.isInteger(numeric) ? numeric.toFixed(0) : numeric.toFixed(3).replace(/\.?0+$/, '')
    })
    .join(',')
}

export function formatWorkOriginPosition(origin: MotionOriginConfig) {
  return `工作原点位置：左[${formatPulseList(origin.leftPulse)}] 右[${formatPulseList(origin.rightPulse)}]`
}

export function softLimitConfigForSide(config: AppConfig, side: RobotSide): ArmSoftLimitConfig {
  return side === 'left' ? config.motion.leftSoftLimits : config.motion.rightSoftLimits
}

export function rotationWorkLimitsForSide(config: AppConfig, side: RobotSide): RotationWorkLimitSideConfig {
  return config.motion.rotationWorkLimits?.[side] ?? defaultRotationWorkLimits
}

export function signedPulsePerUnit(config: AppConfig, side: RobotSide, axisIndex: number) {
  const kinematics = config.motion.kinematics
  const signed = side === 'left' ? kinematics.leftSignedPulsePerUnit : kinematics.rightSignedPulsePerUnit
  return Number(signed?.[axisIndex] ?? 0)
}

export function pulseToAxisUi(config: AppConfig, side: RobotSide, axisIndex: number, pulse: number) {
  const perUnit = signedPulsePerUnit(config, side, axisIndex)
  if (!Number.isFinite(perUnit) || perUnit === 0) return 0
  return axisIndex < 3 ? (pulse / perUnit) * 1000 : pulse / perUnit
}

export function originAxisUi(config: AppConfig, side: RobotSide, axisIndex: number) {
  const origin = config.motion.origin
  const pulse = side === 'left' ? origin.leftPulse[axisIndex] : origin.rightPulse[axisIndex]
  return pulseToAxisUi(config, side, axisIndex, Number(pulse ?? 0))
}

export function effectiveAxisLimitUi(
  config: AppConfig,
  side: RobotSide,
  axisKey: keyof ArmSoftLimitConfig,
  axisIndex: number,
) {
  const mechanical = softLimitConfigForSide(config, side)[axisKey]
  const originUi = originAxisUi(config, side, axisIndex)
  const min = Number(mechanical?.min ?? 0)
  const max = Number(mechanical?.max ?? 0)
  const blocked = axisIndex >= 3 && !config.motion.origin.valid
  return {
    min: originUi + min,
    max: originUi + max,
    blocked,
  }
}

export function displayAxisLimitForTelemetry(
  config: AppConfig,
  side: RobotSide,
  axisKey: keyof ArmSoftLimitConfig,
  axisIndex: number,
) {
  const effective = effectiveAxisLimitUi(config, side, axisKey, axisIndex)
  if (axisIndex < 3) {
    return {
      min: effective.min,
      max: effective.max,
      blocked: effective.blocked,
    }
  }
  return effective
}

export function formatAxisValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value.toFixed(1) : value.toFixed(3)
}

export function displaySoftLimitValue(value: number, semanticIndex: number) {
  return formatAxisValue(value, semanticIndex)
}

export function configSoftLimitValue(value: number, semanticIndex: number) {
  return semanticIndex < 3 ? value / 1000 : value
}

export function formatSoftLimitValue(value: number, semanticIndex: number) {
  return formatAxisValue(value, semanticIndex)
}

export function motionSnapshotScope(side: RobotSide) {
  return side === 'left' ? ('motion-left' as const) : ('motion-right' as const)
}

export function formatSnapshotTime(ts: number) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ts))
}

export const softLimitRows = [
  { key: 'x', label: 'X', unit: 'µm' },
  { key: 'y', label: 'Y', unit: 'µm' },
  { key: 'z', label: 'Z', unit: 'µm' },
  { key: 'roll', label: 'Roll', unit: '°' },
  { key: 'pitch', label: 'Pitch', unit: '°' },
  { key: 'yaw', label: 'Yaw', unit: '°' },
] as const

export const rotationLimitRows = softLimitRows.slice(3)

export const defaultRotationWorkLimits: RotationWorkLimitSideConfig = {
  roll: { min: -100, max: 100 },
  pitch: { min: -100, max: 100 },
  yaw: { min: -7, max: 7 },
}
