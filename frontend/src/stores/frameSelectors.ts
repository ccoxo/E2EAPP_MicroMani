/*
 * 阅读导航 02｜前端契约与状态
 * 职责：提供 frame 切片的细粒度订阅与浅比较，降低整帧替换引起的重渲染。
 * 先看：useFrameField → numberArrayEqual。
 */
import { useRef, useSyncExternalStore } from 'react'
import { useTelemetryStore } from './telemetry'
import type { TelemetryFrame } from '../types'

/** 数值数组浅比较：内容相同则不触发重渲染。 */
export function numberArrayEqual(a: readonly number[], b: readonly number[]) {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false
  }
  return true
}

/** 布尔/可空布尔数组比较。 */
export function boolArrayEqual(a: readonly (boolean | null)[], b: readonly (boolean | null)[]) {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false
  }
  return true
}

/** 浅比较可选对象字段；缺字段视为不等，避免 undefined 吞掉真实变更。 */
export function shallowEqualRecord<T extends object>(a: T | undefined, b: T | undefined) {
  if (a === b) return true
  if (!a || !b) return false
  const keysA = Object.keys(a) as Array<keyof T>
  const keysB = Object.keys(b) as Array<keyof T>
  if (keysA.length !== keysB.length) return false
  for (const key of keysA) {
    if (a[key] !== b[key]) return false
  }
  return true
}

/** 订阅 frame 的单个切片；默认 Object.is，可传自定义相等性。 */
export function useFrameField<T>(
  selector: (frame: TelemetryFrame) => T,
  isEqual: (a: T, b: T) => boolean = Object.is,
): T {
  const cache = useRef<{ value: T } | null>(null)
  const subscribe = useTelemetryStore.subscribe
  const getSnapshot = () => {
    const next = selector(useTelemetryStore.getState().frame)
    if (cache.current && isEqual(cache.current.value, next)) {
      return cache.current.value
    }
    cache.current = { value: next }
    return next
  }
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

/** 硬件状态行只需派生输入。 */
export type HardwareStatusFrameSlice = Pick<
  TelemetryFrame,
  | 'halOk'
  | 'wsOk'
  | 'cameras'
  | 'forceStatus'
  | 'gripperStatus'
  | 'teleopHands'
>

export function hardwareStatusFrameSlice(frame: TelemetryFrame): HardwareStatusFrameSlice {
  return {
    halOk: frame.halOk,
    wsOk: frame.wsOk,
    cameras: frame.cameras,
    forceStatus: frame.forceStatus,
    gripperStatus: frame.gripperStatus,
    teleopHands: frame.teleopHands,
  }
}

export function hardwareStatusFrameEqual(a: HardwareStatusFrameSlice, b: HardwareStatusFrameSlice) {
  return a.halOk === b.halOk
    && a.wsOk === b.wsOk
    && a.cameras === b.cameras
    && a.forceStatus === b.forceStatus
    && a.gripperStatus === b.gripperStatus
    && a.teleopHands === b.teleopHands
}

/** PreCheck 只需这些字段做步骤判定。 */
export type PreCheckFrameSlice = Pick<
  TelemetryFrame,
  | 'halOk'
  | 'wsOk'
  | 'cameras'
  | 'teleopHands'
  | 'forceLeft'
  | 'forceRight'
  | 'forceStatus'
  | 'motionEnabled'
  | 'motionAxisEnabled'
>

export function preCheckFrameSlice(frame: TelemetryFrame): PreCheckFrameSlice {
  return {
    halOk: frame.halOk,
    wsOk: frame.wsOk,
    cameras: frame.cameras,
    teleopHands: frame.teleopHands,
    forceLeft: frame.forceLeft,
    forceRight: frame.forceRight,
    forceStatus: frame.forceStatus,
    motionEnabled: frame.motionEnabled,
    motionAxisEnabled: frame.motionAxisEnabled,
  }
}

export function preCheckFrameEqual(a: PreCheckFrameSlice, b: PreCheckFrameSlice) {
  return a.halOk === b.halOk
    && a.wsOk === b.wsOk
    && a.cameras === b.cameras
    && a.teleopHands === b.teleopHands
    && a.forceLeft === b.forceLeft
    && a.forceRight === b.forceRight
    && a.forceStatus?.source === b.forceStatus?.source
    && a.forceStatus?.calibration?.state === b.forceStatus?.calibration?.state
    && a.forceStatus?.safety?.latched === b.forceStatus?.safety?.latched
    && a.motionEnabled === b.motionEnabled
    && a.motionAxisEnabled === b.motionAxisEnabled
}

/** 遥操作卡片需要主手与轴使能反馈。 */
export type TeleopHandFrameSlice = Pick<
  TelemetryFrame,
  'teleopHands' | 'motionEnabled' | 'motionAxisEnabled'
>

export function teleopHandFrameSlice(frame: TelemetryFrame): TeleopHandFrameSlice {
  return {
    teleopHands: frame.teleopHands,
    motionEnabled: frame.motionEnabled,
    motionAxisEnabled: frame.motionAxisEnabled,
  }
}

export function teleopHandFrameEqual(a: TeleopHandFrameSlice, b: TeleopHandFrameSlice) {
  return a.teleopHands === b.teleopHands
    && a.motionEnabled === b.motionEnabled
    && a.motionAxisEnabled === b.motionAxisEnabled
}

/** 相机预览列表：比较全部显示/健康字段，时钟偏差变化也必须刷新告警。 */
export function camerasEqual(
  a: TelemetryFrame['cameras'],
  b: TelemetryFrame['cameras'],
) {
  if (a === b) return true
  if (a.length !== b.length) return false
  return a.every((camera, index) => {
    const other = b[index]
    return camera === other || (
      camera.key === other.key
      && camera.label === other.label
      && camera.health === other.health
      && camera.fps === other.fps
      && camera.frameAgeMs === other.frameAgeMs
      && camera.timestampSkewMs === other.timestampSkewMs
      && camera.backend === other.backend
      && camera.workerActive === other.workerActive
    )
  })
}
