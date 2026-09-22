import { describe, expect, it } from 'vitest'
import {
  gripperCommandProgressLabel,
  gripperFeedbackHealth,
  gripperFeedbackLabel,
  gripperRequestedEnabled,
  gripperRequestLabel,
  initialGripperCommandProgress,
} from './gripperDisplay'
import { defaultConfig } from './data'
import type { TelemetryFrame, TelemetryLinkStatus } from './types'

function baseFrame(overrides: Partial<TelemetryFrame> = {}): TelemetryFrame {
  return {
    timestamp: Date.now(),
    elapsedSec: 0,
    jointPositions: Array(12).fill(0),
    gripperPositions: [0, 0],
    motionEnabled: { left: null, right: null },
    motionAxisEnabled: { left: Array(6).fill(null), right: Array(6).fill(null) },
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
    ...overrides,
  }
}

const liveLink: TelemetryLinkStatus = { state: 'live', lastFrameReceivedAt: Date.now() }
const offlineLink: TelemetryLinkStatus = { state: 'offline', lastFrameReceivedAt: null }

describe('夹爪展示语义拆分', () => {
  it('请求启停来自配置，不依赖反馈', () => {
    const disabled = structuredClone(defaultConfig)
    disabled.gripper.leftEnabled = false
    expect(gripperRequestedEnabled(disabled, 'left')).toBe(false)
    expect(gripperRequestLabel('disabled')).toBe('已请求断使能')
    const enabled = structuredClone(defaultConfig)
    enabled.gripper.leftEnabled = true
    expect(gripperRequestedEnabled(enabled, 'left')).toBe(true)
    expect(gripperRequestLabel('enabled')).toBe('已请求使能')
  })

  it.each([
    { link: offlineLink, expected: 'unknown' },
    { link: liveLink, frame: baseFrame({ halOk: false }), expected: 'unknown' },
    { link: liveLink, frame: baseFrame({ gripperStatus: undefined }), expected: 'unknown' },
    {
      link: liveLink,
      frame: baseFrame({ gripperStatus: { running: true, sides: { left: { ok: false } } } }),
      expected: 'error',
    },
    {
      link: liveLink,
      frame: baseFrame({ gripperStatus: { running: true, sides: { left: { ok: true } } } }),
      expected: 'ok',
    },
    {
      // 历史成功但控制器未运行 → 待确认，不能写成当前健康或已使能
      link: liveLink,
      frame: baseFrame({ gripperStatus: { running: false, sides: { left: { ok: true } } } }),
      expected: 'pending',
    },
    {
      link: liveLink,
      frame: baseFrame({ gripperStatus: { running: true, sides: { left: { ok: null } } } }),
      expected: 'pending',
    },
  ])('反馈健康：$expected', ({ link, frame, expected }) => {
    expect(gripperFeedbackHealth(frame ?? baseFrame(), link, 'left')).toBe(expected)
  })

  it('命令进度文案与请求、反馈分离', () => {
    const idle = initialGripperCommandProgress()
    expect(gripperCommandProgressLabel(idle)).toBe('')
    expect(gripperCommandProgressLabel({ ...idle, phase: 'sending', command: 'enable' })).toBe('正在发送使能')
    expect(gripperCommandProgressLabel({ ...idle, phase: 'accepted', message: '命令已接受' })).toBe('命令已接受')
    expect(gripperCommandProgressLabel({ ...idle, phase: 'failed', message: '命令失败：x' })).toBe('命令失败：x')
  })

  it('原生命令仅入队时待确认，执行回报正常后才显示正常，断连后撤销反馈', () => {
    const queued = baseFrame({
      gripperStatus: {
        nativeManaged: true,
        running: true,
        sides: { left: { ok: true, message: 'queued native gripper command', lastCommandTs: Date.now() } },
      },
    })
    expect(gripperFeedbackLabel(gripperFeedbackHealth(queued, liveLink, 'left'))).toBe('反馈待确认')

    const completed = baseFrame({
      gripperStatus: {
        ...queued.gripperStatus,
        sides: { left: { ok: true, message: 'runWithParam succeeded', lastCommandTs: Date.now() + 1 } },
      },
    })
    expect(gripperFeedbackLabel(gripperFeedbackHealth(completed, liveLink, 'left'))).toBe('反馈正常')
    expect(gripperFeedbackHealth(completed, offlineLink, 'left')).toBe('unknown')
    expect(gripperFeedbackHealth(completed, { ...liveLink, state: 'stale' }, 'left')).toBe('unknown')

    const failed = baseFrame({
      gripperStatus: { running: true, sides: { left: { ok: false, message: 'Jodell worker response timeout' } } },
    })
    expect(gripperFeedbackLabel(gripperFeedbackHealth(failed, liveLink, 'left'))).toBe('反馈异常')
  })
})
