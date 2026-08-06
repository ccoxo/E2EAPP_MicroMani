import type {
  AppConfig,
  CameraTelemetry,
  ForceSideStatus,
  GripperSideStatus,
  TelemetryFrame,
  TelemetryLinkStatus,
} from './types'

export type HardwareStatusTone = 'ok' | 'warn' | 'error' | 'unknown'

export interface HardwareStatusRow {
  key: string
  name: string
  value: string
  tone: HardwareStatusTone
}

export const telemetryStaleAfterMs = 2_000

export function telemetryLinkIsLive(link: TelemetryLinkStatus) {
  return link.state === 'live'
}

export function telemetryLinkLabel(link: TelemetryLinkStatus, now = Date.now()) {
  if (link.state === 'connecting') return '连接中'
  if (link.state === 'offline') return '遥测中断'
  if (link.state === 'stale') {
    const ageMs = link.lastFrameReceivedAt === null ? 0 : Math.max(0, now - link.lastFrameReceivedAt)
    return `遥测停滞 · ${(ageMs / 1000).toFixed(1)} s`
  }
  const ageMs = link.lastFrameReceivedAt === null ? 0 : Math.max(0, now - link.lastFrameReceivedAt)
  return `实时 · ${(ageMs / 1000).toFixed(1)} s`
}

function configuredHalPort(config: AppConfig) {
  try {
    const url = new URL(config.hal.baseUrl)
    if (url.port) return url.port
    return url.protocol === 'https:' ? '443' : '80'
  } catch {
    return '未配置'
  }
}

function unknownRows(frame: TelemetryFrame, config: AppConfig): HardwareStatusRow[] {
  const port = configuredHalPort(config)
  const forceIsHkvl = config.force.source === 'hkvl_serial' || frame.forceStatus?.source === 'hkvl_serial'
  return [
    { key: 'hal', name: 'HAL Service', value: `${port} · 状态未知`, tone: 'unknown' },
    { key: 'force-left', name: forceIsHkvl ? 'HKVL 左臂' : 'ATI 左臂', value: '--', tone: 'unknown' },
    { key: 'force-right', name: forceIsHkvl ? 'HKVL 右臂' : 'ATI 右臂', value: '--', tone: 'unknown' },
    { key: 'camera-global', name: '相机 全局', value: '--', tone: 'unknown' },
    { key: 'camera-wrist-left', name: '相机 左腕', value: '--', tone: 'unknown' },
    { key: 'camera-wrist-right', name: '相机 右腕', value: '--', tone: 'unknown' },
    { key: 'omega7', name: 'Omega.7 左/右', value: '--', tone: 'unknown' },
    { key: 'gripper', name: '夹爪 左/右', value: '--', tone: 'unknown' },
  ]
}

function forceRow(
  key: 'force-left' | 'force-right',
  name: string,
  configuredValue: string,
  side: ForceSideStatus | undefined,
): HardwareStatusRow {
  const port = side?.port || configuredValue
  const value = side?.sampleHz === undefined ? port : `${port} · ${Math.round(side.sampleHz)} Hz`
  if (!side) return { key, name, value, tone: 'unknown' }
  if (side.connected === false || side.healthy === false) return { key, name, value, tone: 'error' }
  if (side.healthy === true) return { key, name, value, tone: 'ok' }
  return { key, name, value, tone: 'warn' }
}

function cameraRow(key: string, name: string, camera: CameraTelemetry | undefined): HardwareStatusRow {
  if (!camera) return { key, name, value: '--', tone: 'error' }
  const value = `${camera.fps.toFixed(1)} Hz`
  if (camera.health === 'error') return { key, name, value, tone: 'error' }
  if (camera.health !== 'ok') return { key, name, value, tone: 'unknown' }
  return { key, name, value, tone: camera.fps >= 25 ? 'ok' : 'warn' }
}

function omegaRow(frame: TelemetryFrame): HardwareStatusRow {
  const hands = frame.teleopHands
  if (hands.length < 2) return { key: 'omega7', name: 'Omega.7 左/右', value: '--', tone: 'unknown' }
  const ready = hands.filter((hand) => hand.connected && hand.lastReadOk).length
  const logicalDisconnected = hands.some((hand) => hand.message.toLowerCase().includes('logical'))
  const physicallyDisconnected = hands.some((hand) => !hand.connected && !hand.message.toLowerCase().includes('logical'))
  const tone: HardwareStatusTone = physicallyDisconnected
    ? 'error'
    : logicalDisconnected || hands.some((hand) => !hand.lastReadOk)
      ? 'warn'
      : 'ok'
  return {
    key: 'omega7',
    name: 'Omega.7 左/右',
    value: logicalDisconnected && ready === 0 ? '逻辑未连接' : `读取 ${ready}/2`,
    tone,
  }
}

function gripperRow(frame: TelemetryFrame): HardwareStatusRow {
  const status = frame.gripperStatus
  const sides: Array<GripperSideStatus | undefined> = [status?.sides?.left, status?.sides?.right]
  if (sides.some((side) => side?.ok === false)) {
    const ready = sides.filter((side) => side?.ok === true).length
    return { key: 'gripper', name: '夹爪 左/右', value: `反馈 ${ready}/2`, tone: 'error' }
  }
  if (status?.running === true && sides.every((side) => side?.ok === true)) {
    return { key: 'gripper', name: '夹爪 左/右', value: '反馈 2/2', tone: 'ok' }
  }
  return {
    key: 'gripper',
    name: '夹爪 左/右',
    value: status?.running ? `反馈 ${sides.filter((side) => side?.ok === true).length}/2 · 待确认` : '未激活 · 未验证',
    tone: 'warn',
  }
}

export function deriveHardwareStatusRows(
  frame: TelemetryFrame,
  config: AppConfig,
  link: TelemetryLinkStatus,
): HardwareStatusRow[] {
  if (!telemetryLinkIsLive(link)) return unknownRows(frame, config)

  const forceIsHkvl = config.force.source === 'hkvl_serial' || frame.forceStatus?.source === 'hkvl_serial'
  const leftForce = frame.forceStatus?.sides?.left
  const rightForce = frame.forceStatus?.sides?.right

  return [
    {
      key: 'hal',
      name: 'HAL Service',
      value: configuredHalPort(config),
      tone: frame.halOk ? 'ok' : 'error',
    },
    forceRow(
      'force-left',
      forceIsHkvl ? 'HKVL 左臂' : 'ATI 左臂',
      forceIsHkvl ? config.force.serial.leftPort : config.force.leftIp,
      leftForce,
    ),
    forceRow(
      'force-right',
      forceIsHkvl ? 'HKVL 右臂' : 'ATI 右臂',
      forceIsHkvl ? config.force.serial.rightPort : config.force.rightIp,
      rightForce,
    ),
    cameraRow('camera-global', '相机 全局', frame.cameras.find((camera) => camera.key === 'global')),
    cameraRow('camera-wrist-left', '相机 左腕', frame.cameras.find((camera) => camera.key === 'wrist_left')),
    cameraRow('camera-wrist-right', '相机 右腕', frame.cameras.find((camera) => camera.key === 'wrist_right')),
    omegaRow(frame),
    gripperRow(frame),
  ]
}
