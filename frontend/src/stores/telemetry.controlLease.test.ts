import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TelemetryFrame } from '../types'

let store: typeof import('./telemetry').useTelemetryStore
let time = 0
let fetchSpy: ReturnType<typeof vi.fn>

class Socket {
  static OPEN = 1
  static instances: Socket[] = []
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  send = vi.fn()
  close = vi.fn(() => { this.readyState = 3; this.onclose?.({ code: 1000 }) })
  url?: string
  constructor(url?: string) { this.url = url; Socket.instances.push(this) }
  emit(type: string, data: unknown) { this.onmessage?.({ data: JSON.stringify({ type, data }) }) }
}

function challenge(socket: Socket, id = 'n1') {
  socket.emit('safety_challenge', { sessionId: 's1', challengeId: id, ttlMs: 2000 })
}
function confirm(socket: Socket, status = 'active', id = 'n1') {
  socket.emit('control_lease', { sessionId: 's1', challengeId: id, status, ttlMs: 2500 })
}
async function live(socket: Socket, patch: Partial<TelemetryFrame> = {}) {
  socket.emit('telemetry', { ...store.getState().frame, wsOk: true, halOk: true, timestamp: Date.now(),
    motionAxisEnabled: { left: [true, true, true, true, true, true], right: [true, true, true, true, true, true] },
    forceStatus: { safety: { latched: false, canAcknowledge: true } }, ...patch })
  await vi.advanceTimersByTimeAsync(100)
}
const commands = () => fetchSpy.mock.calls.map(([url]) => new URL(String(url)).pathname).filter((path) => /\/api\/(motion|force|gripper)\//.test(path))

beforeEach(async () => {
  vi.resetModules()
  vi.useFakeTimers()
  vi.stubEnv('MODE', 'development')
  time = 0
  vi.spyOn(performance, 'now').mockImplementation(() => time)
  window.sessionStorage.clear()
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  fetchSpy = vi.fn().mockResolvedValue(new Response('{}', { status: 503 }))
  vi.stubGlobal('fetch', fetchSpy)
  store = (await import('./telemetry')).useTelemetryStore
})
afterEach(() => {
  store.getState().stopBackend()
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals()
  window.sessionStorage.clear()
  window.history.replaceState({}, '', '/')
})

it('观察页不续租，切换路由后重连仍保持只读', async () => {
  window.history.replaceState({}, '', '/?mode=observe')
  store.getState().startBackend()
  const first = Socket.instances.at(-1)!
  expect(first.url).toContain('mode=observe')
  for (let i = 0; i < 30; i++) { time += 100; await live(first) }
  expect(first.close).not.toHaveBeenCalled()
  expect(first.send).not.toHaveBeenCalled()
  store.getState().issueManualAxisMove('left', 'X', 1)
  expect(commands()).toEqual([])
  window.history.replaceState({}, '', '/settings')
  store.getState().stopBackend()
  store.getState().startBackend()
  expect(Socket.instances.at(-1)!.url).toContain('mode=observe')
})

it('控制权冲突后不自动重试抢占', async () => {
  store.getState().startBackend()
  Socket.instances.at(-1)!.onclose?.({ code: 1008 })
  await vi.advanceTimersByTimeAsync(16_000)
  expect(Socket.instances).toHaveLength(1)
  expect(store.getState().controlLease.reason).toContain('另一页面持有控制权')
})

it('重连后从后端恢复中断片段，不自动新建或结束会话', async () => {
  store.getState().startBackend()
  const socket = Socket.instances.at(-1)!
  socket.emit('record_status', { active: true, recording: false, safetyInterrupted: true,
    datasetName: 'retained', task: 'recover', frameCount: 42 })
  expect(store.getState().recordSession).toMatchObject({ phase: 'interrupted', datasetName: 'retained', recorderFrameCount: 42 })
  expect(fetchSpy.mock.calls.some(([url]) => /\/record\/session\/(finish|create)/.test(String(url)))).toBe(false)
})

describe('真实前端控制租约（HTTP 与 WS 均为离线替身）', () => {
  it('新鲜遥测不足以允许运动，必须收到同一挑战的执行侧租约确认', async () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    await live(socket)
    store.getState().issueManualAxisMove('left', 'X', 1)
    expect(commands()).toEqual([])
    challenge(socket)
    store.getState().issueManualAxisMove('left', 'X', 1)
    expect(commands()).toEqual([])
    confirm(socket)
    store.getState().issueManualAxisMove('left', 'X', 1)
    expect(commands()).toEqual(['/api/motion/manual_axis_move'])
  })

  it('ACK 也必须有新鲜租约，并保留显式操作者动作', async () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    await live(socket, { forceStatus: { safety: { latched: true, canAcknowledge: true } } })
    store.getState().acknowledgeSafety()
    expect(commands()).toEqual([])
    challenge(socket); confirm(socket)
    expect(commands()).toEqual([])
    store.getState().acknowledgeSafety()
    expect(commands()).toEqual(['/api/motion/safety/acknowledge'])
  })

  it('租约转 pending 立即撤销启动权并取消旧控制流程，但仍可断使能', async () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    await live(socket); challenge(socket); confirm(socket)
    const generation = store.getState().controlSafety.generation
    confirm(socket, 'pending')
    expect(store.getState().controlSafety.generation).toBeGreaterThan(generation)
    store.getState().issueManualAxisMove('left', 'X', 1)
    expect(commands()).toEqual([])
    store.getState().setMotionEnabled('left', false)
    expect(commands()).toEqual(['/api/motion/left/disable_all'])
  })

  it('WS error 后的旧挑战和正常遥测都不能复活旧连接', async () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    await live(socket); challenge(socket); confirm(socket)
    socket.onerror?.()
    challenge(socket, 'n2'); confirm(socket, 'active', 'n2')
    await live(socket)
    expect(socket.send).toHaveBeenCalledTimes(1)
    expect(store.getState().controlLease.status).toBe('expired')
    expect(store.getState().telemetryLink.state).toBe('offline')
  })

  it('暂停后积压挑战不能续租，关闭旧WS并保持控制拒绝', async () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    await live(socket); challenge(socket); confirm(socket)
    time = 2100
    challenge(socket, 'n2')
    expect(socket.close).toHaveBeenCalledTimes(1)
    expect(socket.send).toHaveBeenCalledTimes(1)
    expect(store.getState().controlLease.status).toBe('expired')
    store.getState().issueManualAxisMove('left', 'X', 1)
    expect(commands()).toEqual([])
  })

  it('即使显示订阅抛错，撤销动作也先关闭 WS 并写入拒绝控制状态', async () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    await live(socket); challenge(socket); confirm(socket)
    const unsubscribe = store.subscribe(() => { throw new Error('显示故障') })
    expect(() => store.getState().revokeControlLease('显示故障')).toThrow('显示故障')
    unsubscribe()
    expect(socket.close).toHaveBeenCalledTimes(1)
    expect(store.getState().controlLease.status).toBe('expired')
    expect(store.getState().backendWs).toBeNull()
  })
})
