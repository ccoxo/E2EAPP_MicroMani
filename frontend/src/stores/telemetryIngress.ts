import type { LogEntry, TelemetryFrame } from '../types'

export function isWireRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const nullableBoolean = (value: unknown) => value === null || typeof value === 'boolean'
const numericArray = (value: unknown, length: number) => Array.isArray(value) && value.length === length && value.every(finite)

/** 网络数据进入共享状态前检查主结构，避免一个坏字段使所有订阅者一起失败。 */
export function parseTelemetryFrame(value: unknown): TelemetryFrame {
  const fail = (field: string): never => { throw new Error(`遥测字段无效：${field}`) }
  if (!isWireRecord(value)) return fail('frame')
  for (const key of ['timestamp', 'elapsedSec', 'dangerIndex', 'episodeCount', 'frameCount']) {
    if (!finite(value[key])) fail(key)
  }
  for (const key of ['halOk', 'wsOk', 'recording']) {
    if (typeof value[key] !== 'boolean') fail(key)
  }
  for (const [key, length] of [['jointPositions', 12], ['gripperPositions', 2], ['forceLeft', 6], ['forceRight', 6]] as const) {
    if (!numericArray(value[key], length)) fail(key)
  }
  for (const [key, fields] of [
    ['resource', ['uiFps', 'wsHz', 'cpuPct', 'memMb']],
    ['queueDepth', ['left', 'right']],
  ] as const) {
    const group = value[key]
    if (!isWireRecord(group) || !fields.every(field => finite(group[field]))) fail(key)
  }
  if (!Array.isArray(value.cameras) || !value.cameras.every(camera => isWireRecord(camera)
    && ['global', 'wrist_left', 'wrist_right'].includes(String(camera.key))
    && typeof camera.label === 'string'
    && ['ok', 'warn', 'error', 'checking', 'pending'].includes(String(camera.health))
    && ['fps', 'timestampSkewMs', 'frameAgeMs'].every(key => finite(camera[key])))) fail('cameras')
  if (!Array.isArray(value.teleopHands) || !value.teleopHands.every(hand => isWireRecord(hand)
    && ['left', 'right'].includes(String(hand.side)) && numericArray(hand.pose, 6)
    && ['connected', 'calibrated', 'clutchPressed', 'gripperPressed', 'lastReadOk'].every(key => typeof hand[key] === 'boolean')
    && ['openId', 'deviceId'].every(key => finite(hand[key]))
    && ['serial', 'systemName', 'message'].every(key => typeof hand[key] === 'string')
    && nullableBoolean(hand.leftHanded) && (hand.gripperGapMm === null || finite(hand.gripperGapMm)))) fail('teleopHands')
  if (!Array.isArray(value.processStatus) || !value.processStatus.every(process => isWireRecord(process)
    && ['name', 'label', 'status'].every(key => typeof process[key] === 'string')
    && finite(process.cpuPct) && finite(process.memMb))) fail('processStatus')

  const motionEnabled = value.motionEnabled ?? { left: null, right: null }
  const motionAxisEnabled = value.motionAxisEnabled ?? { left: Array(6).fill(null), right: Array(6).fill(null) }
  const motionAxisEnabledConfirmed = value.motionAxisEnabledConfirmed ?? { left: Array(6).fill(false), right: Array(6).fill(false) }
  if (!isWireRecord(motionEnabled) || !['left', 'right'].every(side => nullableBoolean(motionEnabled[side]))) fail('motionEnabled')
  if (!isWireRecord(motionAxisEnabled) || !['left', 'right'].every(side => {
    const axes = motionAxisEnabled[side]
    return Array.isArray(axes) && axes.length === 6 && axes.every(nullableBoolean)
  })) fail('motionAxisEnabled')
  if (!isWireRecord(motionAxisEnabledConfirmed) || !['left', 'right'].every(side => {
    const axes = motionAxisEnabledConfirmed[side]
    return Array.isArray(axes) && axes.length === 6 && axes.every(value => typeof value === 'boolean')
  })) fail('motionAxisEnabledConfirmed')
  if (value.forceStatus !== undefined) {
    if (!isWireRecord(value.forceStatus)) fail('forceStatus')
    const safety = (value.forceStatus as Record<string, unknown>).safety
    if (safety !== undefined && (!isWireRecord(safety)
      || ['latched', 'canAcknowledge'].some(key => safety[key] !== undefined && typeof safety[key] !== 'boolean'))) fail('forceStatus.safety')
  }
  return { ...value, motionEnabled, motionAxisEnabled, motionAxisEnabledConfirmed } as unknown as TelemetryFrame
}

/** 即使同一帧其他字段损坏，明确的力急停仍应立即阻断控制。 */
export function wireFrameIsLatched(value: unknown): boolean {
  if (!isWireRecord(value) || !isWireRecord(value.forceStatus) || !isWireRecord(value.forceStatus.safety)) return false
  return value.forceStatus.safety.latched === true
}

export function parseLogEntry(value: unknown): LogEntry {
  if (!isWireRecord(value) || !finite(value.id) || !finite(value.ts)
    || typeof value.channel !== 'string' || typeof value.msg !== 'string'
    || !['DEBUG', 'INFO', 'WARNING', 'ERROR'].includes(String(value.level))) {
    throw new Error('日志字段无效')
  }
  return value as unknown as LogEntry
}
