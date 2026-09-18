import { describe, expect, it } from 'vitest'
import {
  boolArrayEqual,
  camerasEqual,
  hardwareStatusFrameEqual,
  hardwareStatusFrameSlice,
  numberArrayEqual,
  preCheckFrameEqual,
  preCheckFrameSlice,
  teleopHandFrameEqual,
  teleopHandFrameSlice,
} from './frameSelectors'
import type { TelemetryFrame } from '../types'

function baseFrame(): TelemetryFrame {
  return {
    timestamp: 1,
    elapsedSec: 0,
    jointPositions: Array(12).fill(0),
    gripperPositions: [0, 0],
    motionEnabled: { left: false, right: false },
    motionAxisEnabled: { left: Array(6).fill(false), right: Array(6).fill(false) },
    forceLeft: Array(6).fill(0),
    forceRight: Array(6).fill(0),
    dangerIndex: 0,
    recording: false,
    episodeCount: 0,
    frameCount: 0,
    halOk: true,
    wsOk: true,
    cameras: [],
    teleopHands: [],
    queueDepth: { left: 0, right: 0 },
    resource: { uiFps: 60, wsHz: 30, cpuPct: 1, memMb: 100 },
    processStatus: [],
  }
}

describe('frame 切片相等性', () => {
  it('数组比较：内容相同视为相等', () => {
    expect(numberArrayEqual([1, 2], [1, 2])).toBe(true)
    expect(numberArrayEqual([1, 2], [1, 3])).toBe(false)
    expect(boolArrayEqual([true, null], [true, null])).toBe(true)
    expect(boolArrayEqual([true, null], [false, null])).toBe(false)
  })

  it('硬件状态切片：仅当相关字段引用变化时才不等', () => {
    const a = baseFrame()
    const b = { ...a, timestamp: 2, jointPositions: Array(12).fill(9), dangerIndex: 0.5 }
    expect(hardwareStatusFrameEqual(hardwareStatusFrameSlice(a), hardwareStatusFrameSlice(b))).toBe(true)
    const c = { ...b, halOk: false }
    expect(hardwareStatusFrameEqual(hardwareStatusFrameSlice(a), hardwareStatusFrameSlice(c))).toBe(false)
  })

  it('PreCheck 切片含轴使能；关节变化不触发', () => {
    const a = baseFrame()
    const b = { ...a, jointPositions: Array(12).fill(99) }
    expect(preCheckFrameEqual(preCheckFrameSlice(a), preCheckFrameSlice(b))).toBe(true)
    const c = { ...a, motionEnabled: { left: true, right: false } }
    expect(preCheckFrameEqual(preCheckFrameSlice(a), preCheckFrameSlice(c))).toBe(false)
  })

  it('遥操作切片忽略力与相机', () => {
    const a = baseFrame()
    const b = {
      ...a,
      forceLeft: Array(6).fill(1),
      cameras: [{ key: 'global' as const, label: '全局', fps: 30, timestampSkewMs: 0, frameAgeMs: 1, health: 'ok' as const }],
    }
    expect(teleopHandFrameEqual(teleopHandFrameSlice(a), teleopHandFrameSlice(b))).toBe(true)
  })

  it('相机列表按健康字段比较', () => {
    const cam = { key: 'global' as const, label: '全局', fps: 30, timestampSkewMs: 0, frameAgeMs: 20, health: 'ok' as const }
    expect(camerasEqual([cam], [{ ...cam }])).toBe(true)
    expect(camerasEqual([cam], [{ ...cam, timestampSkewMs: 99 }])).toBe(false)
    expect(camerasEqual([cam], [{ ...cam, fps: 10 }])).toBe(false)
  })
})
