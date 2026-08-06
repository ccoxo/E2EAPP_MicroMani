import { describe, expect, it } from 'vitest'
import { defaultConfig } from './data'
import { deriveHardwareStatusRows } from './hardwareStatus'
import type { TelemetryFrame, TelemetryLinkStatus } from './types'

function liveLink(): TelemetryLinkStatus {
  return { state: 'live', lastFrameReceivedAt: 1_000 }
}

function healthyFrame(): TelemetryFrame {
  return {
    timestamp: 1_000,
    elapsedSec: 1,
    jointPositions: Array.from({ length: 12 }, () => 0),
    gripperPositions: [13, 26],
    motionEnabled: { left: false, right: false },
    motionAxisEnabled: {
      left: Array.from({ length: 6 }, () => false),
      right: Array.from({ length: 6 }, () => false),
    },
    forceLeft: Array.from({ length: 6 }, () => 0),
    forceRight: Array.from({ length: 6 }, () => 0),
    forceStatus: {
      source: 'hkvl_serial',
      sides: {
        left: { port: 'COM15', connected: true, healthy: true, sampleHz: 1009.4 },
        right: { port: 'COM14', connected: true, healthy: true, sampleHz: 1005.9 },
      },
    },
    gripperStatus: {
      nativeManaged: true,
      running: true,
      sides: {
        left: { ok: true, positionMm: 13, serial: { port: 'COM8', slaveId: 10 } },
        right: { ok: true, positionMm: 26, serial: { port: 'COM9', slaveId: 9 } },
      },
    },
    dangerIndex: 0,
    recording: false,
    episodeCount: 0,
    frameCount: 0,
    halOk: true,
    wsOk: true,
    cameras: [
      { key: 'global', label: 'Global Camera', fps: 30.2, timestampSkewMs: 0, frameAgeMs: 10, health: 'ok' },
      { key: 'wrist_left', label: 'Left Wrist Camera', fps: 30.1, timestampSkewMs: 0, frameAgeMs: 10, health: 'ok' },
      { key: 'wrist_right', label: 'Right Wrist Camera', fps: 20.2, timestampSkewMs: 0, frameAgeMs: 10, health: 'ok' },
    ],
    teleopHands: [
      { side: 'left', connected: true, calibrated: true, openId: 0, deviceId: 0, serial: '22025', systemName: 'Omega.7', leftHanded: true, pose: [0, 0, 0, 0, 0, 0], clutchPressed: false, gripperPressed: false, gripperGapMm: 10, lastReadOk: true, message: '' },
      { side: 'right', connected: true, calibrated: true, openId: 1, deviceId: 1, serial: '22821', systemName: 'Omega.7', leftHanded: false, pose: [0, 0, 0, 0, 0, 0], clutchPressed: false, gripperPressed: false, gripperGapMm: 10, lastReadOk: true, message: '' },
    ],
    queueDepth: { left: 0, right: 0 },
    resource: { uiFps: 60, wsHz: 30, cpuPct: 1, memMb: 100 },
    processStatus: [],
  }
}

describe('hardware status projection', () => {
  it('uses configured and live telemetry values without new probes', () => {
    const config = structuredClone(defaultConfig)
    config.hal.baseUrl = 'http://localhost:8091'
    config.force.source = 'hkvl_serial'

    const rows = deriveHardwareStatusRows(healthyFrame(), config, liveLink())

    expect(rows.find((row) => row.key === 'hal')).toMatchObject({ tone: 'ok', value: '8091' })
    expect(rows.find((row) => row.key === 'force-left')).toMatchObject({
      name: 'HKVL 左臂',
      tone: 'ok',
      value: 'COM15 · 1009 Hz',
    })
    expect(rows.find((row) => row.key === 'camera-wrist-right')).toMatchObject({ tone: 'warn', value: '20.2 Hz' })
    expect(rows.find((row) => row.key === 'omega7')).toMatchObject({ tone: 'ok', value: '读取 2/2' })
    expect(rows.find((row) => row.key === 'gripper')).toMatchObject({ tone: 'ok', value: '反馈 2/2' })
  })

  it('removes every live success state when telemetry is stale', () => {
    const rows = deriveHardwareStatusRows(healthyFrame(), defaultConfig, {
      state: 'stale',
      lastFrameReceivedAt: 1_000,
    })

    expect(rows.every((row) => row.tone === 'unknown')).toBe(true)
    expect(rows.find((row) => row.key === 'hal')?.value).toBe('8091 · 状态未知')
    expect(rows.filter((row) => row.key !== 'hal').every((row) => row.value === '--')).toBe(true)
  })

  it('does not report an idle gripper as healthy without side feedback', () => {
    const frame = healthyFrame()
    frame.gripperStatus = {
      nativeManaged: true,
      running: false,
      sides: {
        left: { ok: null, serial: { port: 'COM8', slaveId: 10 } },
        right: { ok: null, serial: { port: 'COM9', slaveId: 9 } },
      },
    }

    const rows = deriveHardwareStatusRows(frame, defaultConfig, liveLink())

    expect(rows.find((row) => row.key === 'gripper')).toMatchObject({
      tone: 'warn',
      value: '未激活 · 未验证',
    })
  })

  it('does not reuse successful gripper feedback after the native controller stops', () => {
    const frame = healthyFrame()
    frame.gripperStatus = {
      nativeManaged: true,
      running: false,
      sides: {
        left: { ok: true, positionMm: 13 },
        right: { ok: true, positionMm: 26 },
      },
    }

    const rows = deriveHardwareStatusRows(frame, defaultConfig, liveLink())

    expect(rows.find((row) => row.key === 'gripper')).toMatchObject({
      tone: 'warn',
      value: '未激活 · 未验证',
    })
  })
})
