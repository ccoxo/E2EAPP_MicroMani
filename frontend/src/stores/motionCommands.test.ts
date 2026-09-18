import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { telemetryStaleAfterMs } from '../hardwareStatus'
import type { ManualControlSide, TelemetryFrame, TelemetryLinkStatus } from '../types'
import {
  createMotionCommandController,
  initialMotionCommand,
  motionCommandLabel,
  motionCommandTimeoutMs,
  motionDeviceState,
  type MotionCommands,
  type MotionCommandState,
} from './motionCommands'

function healthyFrame(): TelemetryFrame {
  return {
    timestamp: Date.now(),
    elapsedSec: 0,
    jointPositions: Array.from({ length: 12 }, () => 0),
    gripperPositions: [0, 0],
    motionEnabled: { left: false, right: false },
    motionAxisEnabled: {
      left: Array.from({ length: 6 }, () => false),
      right: Array.from({ length: 6 }, () => false),
    },
    forceLeft: Array.from({ length: 6 }, () => 0),
    forceRight: Array.from({ length: 6 }, () => 0),
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

function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<unknown>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

function fixture() {
  const snapshot: {
    frame: TelemetryFrame
    telemetryLink: TelemetryLinkStatus
    motionCommand: MotionCommands
  } = {
    frame: healthyFrame(),
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    motionCommand: { left: initialMotionCommand(), right: initialMotionCommand() },
  }
  const requests: Array<ReturnType<typeof deferred>> = []
  const send = vi.fn<(side: ManualControlSide, enabled: boolean) => Promise<unknown>>(() => {
    const request = deferred()
    requests.push(request)
    return request.promise
  })
  const write = vi.fn((side: ManualControlSide, command: MotionCommandState) => {
    snapshot.motionCommand = { ...snapshot.motionCommand, [side]: command }
  })
  let blockedReason: string | null = null
  const controller = createMotionCommandController(() => snapshot, write, send, () => blockedReason)
  function frameWithAxes(side: ManualControlSide, axes: Array<boolean | null>, aggregate: boolean | null = null) {
    return {
      ...snapshot.frame,
      timestamp: snapshot.frame.timestamp + 1,
      motionAxisEnabled: { ...snapshot.frame.motionAxisEnabled, [side]: axes },
      motionEnabled: { ...snapshot.frame.motionEnabled, [side]: aggregate },
    }
  }
  function receive(frame: TelemetryFrame, commit = true) {
    controller.receiveFrame(frame)
    if (commit) {
      snapshot.frame = frame
      snapshot.telemetryLink = { state: 'live', lastFrameReceivedAt: Date.now() }
    }
  }
  return {
    snapshot, requests, send, write, controller, frameWithAxes, receive,
    setBlockedReason: (reason: string | null) => { blockedReason = reason },
  }
}

async function settleRequests() {
  await Promise.resolve()
  await Promise.resolve()
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(10_000)
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('运动使能命令与设备反馈', () => {
  it('初始 idle；API 成功只进入等待反馈，不改变设备态', async () => {
    const test = fixture()
    expect(test.snapshot.motionCommand.left.phase).toBe('idle')

    test.controller.setEnabled('left', true)
    expect(test.snapshot.motionCommand.left.phase).toBe('sending')
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'waitingConfirm', transportPending: false })
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('disabled')
  })

  it.each(['HTTP 错误', '业务拒绝'])('%s 进入 failed，清除排队使能且不伪造使能', async (failure) => {
    const test = fixture()
    test.controller.setEnabled('left', false)
    test.controller.setEnabled('left', true)
    if (failure === 'HTTP 错误') test.requests[0].reject(new Error('HTTP 503'))
    else test.requests[0].resolve({ ok: false, message: '后端拒绝请求' })
    await settleRequests()

    expect(test.snapshot.motionCommand.left).toMatchObject({
      phase: 'failed', queuedEnabled: null, transportPending: false,
    })
    expect(motionCommandLabel(test.snapshot.motionCommand.left)).toContain('排队使能未发送')
    expect(test.send).toHaveBeenCalledTimes(1)
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('disabled')
  })

  it('反馈先到时保存候选确认，响应成功后直接确认，不经过等待态', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    expect(test.snapshot.motionCommand.left.phase).toBe('sending')

    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'confirmed', transportPending: false })
    expect(test.write.mock.calls.map(([, command]) => command.phase)).not.toContain('waitingConfirm')
  })

  it('响应前反馈已改变时，不使用更早的匹配值确认', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.snapshot.motionCommand.left.phase).toBe('waitingConfirm')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('disabled')
  })

  it('匹配反馈已过期但 watchdog 尚未失效时，迟到的 API 成功不能确认', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    await vi.advanceTimersByTimeAsync(telemetryStaleAfterMs + 1)

    // 保留 live 标记，覆盖接收时间已过期但 watchdog 下一轮尚未运行的窗口。
    expect(test.snapshot.telemetryLink.state).toBe('live')
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'failed', transportPending: false })
    expect(test.write.mock.calls.map(([, command]) => command.phase)).not.toContain('confirmed')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('unknown')

    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    expect(test.snapshot.motionCommand.left.phase).toBe('failed')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('enabled')
  })

  it.each([
    { axes: [true, null, null, null, null, null], aggregate: true, expected: 'partial' },
    { axes: [true, false, true, true, true, true], aggregate: false, expected: 'partial' },
    { axes: [null, null, null, null, null, null], aggregate: true, expected: 'unknown' },
  ])('不由部分轴或误导性聚合值确认使能：$expected', async ({ axes, aggregate, expected }) => {
    const test = fixture()
    test.controller.setEnabled('right', true)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('right', axes, aggregate))

    expect(test.snapshot.motionCommand.right.phase).toBe('waitingConfirm')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'right')).toBe(expected)
  })

  it('右侧断使能反馈全 null 时不误判失能，最终只报告未获得执行确认', async () => {
    const test = fixture()
    test.controller.setEnabled('right', false)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('right', Array<null>(6).fill(null), false))

    expect(test.snapshot.motionCommand.right.phase).toBe('waitingConfirm')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'right')).toBe('unknown')
    await vi.advanceTimersByTimeAsync(motionCommandTimeoutMs)
    expect(test.snapshot.motionCommand.right).toMatchObject({ phase: 'timeout', message: '未获得执行确认' })
  })

  it('请求前收到但未提交的匹配帧不能确认新请求，重复时间戳也不能确认', async () => {
    const test = fixture()
    const cachedFrame = test.frameWithAxes('left', Array<boolean>(6).fill(true), true)
    test.receive(cachedFrame, false)
    test.controller.setEnabled('left', true)
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    // 模拟 UI 在请求发出后才提交此前已收到的帧。
    test.snapshot.frame = cachedFrame
    test.receive(cachedFrame)
    expect(test.snapshot.motionCommand.left.phase).toBe('waitingConfirm')

    test.receive({ ...cachedFrame, timestamp: cachedFrame.timestamp + 1 })
    expect(test.snapshot.motionCommand.left.phase).toBe('confirmed')
  })

  it('已确认仅描述本次命令，后续设备态继续跟随遥测', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    expect(test.snapshot.motionCommand.left.phase).toBe('confirmed')

    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))
    expect(test.snapshot.motionCommand.left.phase).toBe('confirmed')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('disabled')
  })
})

describe('逐侧 HTTP 顺序与超时', () => {
  it.each([true, false])('相同意图去重，相反意图等待旧 HTTP 完成：首请求 %s', async (enabled) => {
    const test = fixture()
    test.controller.setEnabled('left', enabled)
    test.controller.setEnabled('left', enabled)
    test.controller.setEnabled('left', !enabled)
    expect(test.send).toHaveBeenCalledTimes(1)
    expect(test.snapshot.motionCommand.left.queuedEnabled).toBe(!enabled)
    expect(motionCommandLabel(test.snapshot.motionCommand.left)).toContain('排队中')

    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.send.mock.calls).toEqual([['left', enabled], ['left', !enabled]])
    expect(test.snapshot.motionCommand.left).toMatchObject({
      phase: 'sending', targetEnabled: !enabled, queuedEnabled: null, transportPending: true,
    })
  })

  it('相反请求排队后再次选择当前方向，按最新意图取消后续请求', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.controller.setEnabled('left', false)
    test.controller.setEnabled('left', true)
    expect(test.snapshot.motionCommand.left.queuedEnabled).toBeNull()

    test.requests[0].resolve({ ok: true })
    await settleRequests()
    expect(test.send).toHaveBeenCalledTimes(1)
  })

  it('两侧 HTTP 通道互不阻塞', () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.controller.setEnabled('right', false)

    expect(test.send.mock.calls).toEqual([['left', true], ['right', false]])
    expect(test.snapshot.motionCommand.left.transportPending).toBe(true)
    expect(test.snapshot.motionCommand.right.transportPending).toBe(true)
  })

  it('候选反馈先到也不能让相反请求越过未完成的 HTTP', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    test.controller.setEnabled('left', false)
    expect(test.send).toHaveBeenCalledTimes(1)

    test.requests[0].resolve({ ok: true })
    await settleRequests()
    expect(test.send.mock.calls).toEqual([['left', true], ['left', false]])
    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'sending', targetEnabled: false })
  })

  it('展示超时不释放 HTTP，相反请求等待旧响应后才发送', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    const firstId = test.snapshot.motionCommand.left.requestId
    await vi.advanceTimersByTimeAsync(motionCommandTimeoutMs)
    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'timeout', transportPending: true })

    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))
    test.controller.setEnabled('left', false)
    expect(test.send).toHaveBeenCalledTimes(1)
    expect(test.snapshot.motionCommand.left.queuedEnabled).toBe(false)

    test.requests[0].resolve({ ok: true })
    await settleRequests()
    expect(test.send.mock.calls).toEqual([['left', true], ['left', false]])
    expect(test.snapshot.motionCommand.left.requestId).toBeGreaterThan(firstId)
    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'sending', targetEnabled: false })

    test.requests[1].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))
    expect(test.snapshot.motionCommand.left.phase).toBe('confirmed')
  })

  it('超时后的迟到响应和反馈不能把 timeout 恢复为等待或确认', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    await vi.advanceTimersByTimeAsync(motionCommandTimeoutMs)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.snapshot.motionCommand.left).toMatchObject({
      phase: 'timeout', message: '未获得执行确认', transportPending: false,
    })
    expect(test.send).toHaveBeenCalledTimes(1)
  })
})

describe('运动遥测失效与恢复', () => {
  it.each(['offline', 'stale', 'halUnavailable'] as const)('%s 撤销已确认展示并使设备状态未知', async (reason) => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))

    if (reason === 'halUnavailable') {
      test.receive({ ...test.snapshot.frame, timestamp: test.snapshot.frame.timestamp + 1, halOk: false })
    } else {
      test.snapshot.telemetryLink = { ...test.snapshot.telemetryLink, state: reason }
      test.controller.invalidate('遥测不可用')
    }

    expect(test.snapshot.motionCommand.left.phase).toBe('idle')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('unknown')
  })

  it('链路仍标记 live 但接收时间过期时，设备态未知且不发送命令', async () => {
    const test = fixture()
    await vi.advanceTimersByTimeAsync(telemetryStaleAfterMs + 1)
    test.controller.setEnabled('left', true)

    expect(test.send).not.toHaveBeenCalled()
    expect(test.snapshot.motionCommand.left.phase).toBe('failed')
    expect(motionDeviceState(test.snapshot.frame, test.snapshot.telemetryLink, 'left')).toBe('unknown')
  })

  it('断连保留排队断使能，重连后请求仍等待旧 HTTP，旧响应不能覆盖当前请求', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.controller.setEnabled('left', false)
    test.snapshot.telemetryLink = { ...test.snapshot.telemetryLink, state: 'offline' }
    test.controller.invalidate('遥测连接中断')
    expect(test.snapshot.motionCommand.left).toMatchObject({
      phase: 'failed', queuedEnabled: false, transportPending: true,
    })

    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))
    test.controller.setEnabled('left', false)
    expect(test.send).toHaveBeenCalledTimes(1)
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.send.mock.calls).toEqual([['left', true], ['left', false]])
    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'sending', targetEnabled: false })
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    test.requests[1].resolve({ ok: true })
    await settleRequests()
    expect(test.snapshot.motionCommand.left.phase).toBe('waitingConfirm')
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))
    expect(test.snapshot.motionCommand.left.phase).toBe('confirmed')
  })
})

describe('急停门闩与异常状态下的断使能', () => {
  it.each([true, false])('急停撤销排队使能，HTTP 返回时再次检查门闩：同步撤销 %s', async (synchronouslyBlock) => {
    const test = fixture()
    test.controller.setEnabled('left', false)
    test.controller.setEnabled('left', true)
    test.setBlockedReason('操作员已请求急停')
    if (synchronouslyBlock) test.controller.blockEnabling('操作员已请求急停')
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.send.mock.calls).toEqual([['left', false]])
    expect(test.snapshot.motionCommand.left.queuedEnabled).toBeNull()
  })

  it('急停后迟到成功不能使用此前匹配反馈确认使能', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    test.setBlockedReason('操作员已请求急停')
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.snapshot.motionCommand.left.phase).toBe('failed')
    expect(test.write.mock.calls.map(([, command]) => command.phase)).not.toContain('confirmed')
    test.setBlockedReason(null)
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    expect(test.snapshot.motionCommand.left.phase).toBe('failed')
  })

  it('在 UI 提交前处理入帧安全锁存，禁止该帧确认使能', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    test.receive({
      ...test.frameWithAxes('left', Array<boolean>(6).fill(true), true),
      forceStatus: { safety: { latched: true } },
    }, false)

    expect(test.snapshot.frame.forceStatus?.safety?.latched).not.toBe(true)
    expect(test.snapshot.motionCommand.left.phase).toBe('failed')
    expect(test.write.mock.calls.map(([, command]) => command.phase)).not.toContain('confirmed')
  })

  it('门闩阻止新使能，但断使能仍发送并按新反馈确认', async () => {
    const test = fixture()
    test.setBlockedReason('急停保护尚未确认解除')
    test.controller.setEnabled('left', true)
    expect(test.send).not.toHaveBeenCalled()
    test.controller.setEnabled('left', false)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(false), false))

    expect(test.send.mock.calls).toEqual([['left', false]])
    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'confirmed', targetEnabled: false })
  })

  it.each(['offline', 'stale', 'halUnavailable'] as const)('%s 仍可请求断使能，但没有反馈不能确认', async (reason) => {
    const test = fixture()
    if (reason === 'halUnavailable') test.snapshot.frame.halOk = false
    else test.snapshot.telemetryLink.state = reason
    test.controller.setEnabled('right', false)
    expect(test.send.mock.calls).toEqual([['right', false]])
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    expect(test.snapshot.motionCommand.right.phase).toBe('waitingConfirm')
    await vi.advanceTimersByTimeAsync(motionCommandTimeoutMs)
    expect(test.snapshot.motionCommand.right.phase).toBe('timeout')
  })

  it.each(['HTTP 失败', '成功'] as const)('急停与断连保留排队断使能，旧 HTTP %s 后仍发送', async (result) => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.controller.setEnabled('left', false)
    test.setBlockedReason('操作员已请求急停')
    test.controller.blockEnabling('操作员已请求急停')
    test.snapshot.telemetryLink.state = 'offline'
    test.controller.invalidate('遥测断连')
    expect(test.snapshot.motionCommand.left.queuedEnabled).toBe(false)
    if (result === 'HTTP 失败') test.requests[0].reject(new Error('HTTP 503'))
    else test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.send.mock.calls).toEqual([['left', true], ['left', false]])
    expect(test.snapshot.motionCommand.left).toMatchObject({ phase: 'sending', targetEnabled: false })
  })

  it('急停期间重复点使能不会取消已经排队的断使能', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    test.controller.setEnabled('left', false)
    test.setBlockedReason('操作员已请求急停')
    test.controller.setEnabled('left', true)
    expect(test.snapshot.motionCommand.left.queuedEnabled).toBe(false)
    test.requests[0].resolve({ ok: true })
    await settleRequests()
    expect(test.send.mock.calls).toEqual([['left', true], ['left', false]])
  })

  it('确认解除门闩后的显式同向重试不会被失效的旧 HTTP 吞掉', async () => {
    const test = fixture()
    test.controller.setEnabled('left', true)
    const oldId = test.snapshot.motionCommand.left.requestId
    test.setBlockedReason('操作员已请求急停')
    test.controller.blockEnabling('操作员已请求急停')
    test.setBlockedReason(null)
    test.controller.setEnabled('left', true)
    expect(test.send).toHaveBeenCalledTimes(1)
    test.requests[0].resolve({ ok: true })
    await settleRequests()

    expect(test.send.mock.calls).toEqual([['left', true], ['left', true]])
    expect(test.snapshot.motionCommand.left.requestId).toBeGreaterThan(oldId)
    expect(test.snapshot.motionCommand.left.phase).toBe('sending')
    test.requests[1].resolve({ ok: true })
    await settleRequests()
    test.receive(test.frameWithAxes('left', Array<boolean>(6).fill(true), true))
    expect(test.snapshot.motionCommand.left.phase).toBe('confirmed')
  })
})
