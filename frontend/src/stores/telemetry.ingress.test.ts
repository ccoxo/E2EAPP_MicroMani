import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defaultConfig } from '../data'
import type { TelemetryFrame } from '../types'

let store: typeof import('./telemetry').useTelemetryStore
let requests: Array<{ path: string; resolve: (value: Response) => void }>
class Socket {
  static instances: Socket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor() { Socket.instances.push(this) }
  close() {}
  emit(type: string, data: unknown) { this.onmessage?.({ data: JSON.stringify({ type, data }) }) }
}
function frame(): TelemetryFrame {
  return { ...store.getState().frame, timestamp: Date.now(), halOk: true, wsOk: true }
}
async function settle() { await vi.advanceTimersByTimeAsync(100) }

beforeEach(async () => {
  vi.resetModules()
  vi.useFakeTimers()
  vi.stubEnv('MODE', 'development')
  window.sessionStorage.clear()
  Socket.instances = []
  requests = []
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => new Promise<Response>(resolve => {
    requests.push({ path: new URL(String(input)).pathname, resolve })
  })))
  store = (await import('./telemetry')).useTelemetryStore
})
afterEach(() => {
  store.getState().stopBackend()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  window.sessionStorage.clear()
})

describe('共享遥测入口的故障隔离', () => {
  it('连接构造失败只降级连接，不使应用启动抛错', () => {
    vi.stubGlobal('WebSocket', class { constructor() { throw new Error('连接地址无效') } })
    expect(() => store.getState().startBackend()).not.toThrow()
    expect(store.getState().backendWs).toBeNull()
    expect(store.getState().telemetryLink.state).toBe('offline')
    expect(store.getState().logs.at(-1)?.msg).toContain('连接地址无效')
  })

  it.each([
    { resource: null },
    { cameras: [null] },
    { jointPositions: [0] },
    { forceLeft: ['bad', 0, 0, 0, 0, 0] },
    { motionAxisEnabled: { left: true, right: [] } },
  ])('畸形帧不污染共享状态或延长健康时间：%j', async (patch) => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    const valid = frame()
    socket.emit('telemetry', valid)
    await settle()
    const previous = store.getState().frame
    const receivedAt = store.getState().telemetryLink.lastFrameReceivedAt
    socket.emit('telemetry', { ...valid, ...patch })
    await settle()
    expect(store.getState().frame).toEqual({ ...previous, wsOk: false })
    expect(store.getState().telemetryLink).toEqual({ state: 'stale', lastFrameReceivedAt: receivedAt })
    socket.emit('telemetry', valid)
    await settle()
    expect(store.getState().telemetryLink.state).toBe('live')
  })

  it('有明确力锁存的畸形帧仍保持保护意图，不能作为确认成功的反馈', async () => {
    store.getState().startBackend()
    Socket.instances.at(-1)!.emit('telemetry', { ...frame(), resource: null, forceStatus: { safety: { latched: true } } })
    await settle()
    expect(store.getState().controlSafety.emergencyRequested).toBe(true)
    expect(store.getState().frame.resource).not.toBeNull()
    expect(store.getState().telemetryLink.state).not.toBe('live')
  })

  it('畸形日志被隔离，下一条正常日志仍可进入', () => {
    store.getState().startBackend()
    const socket = Socket.instances.at(-1)!
    socket.emit('log', null)
    socket.emit('log', { id: 42, ts: Date.now(), channel: '[BACKEND]', level: 'INFO', msg: '正常消息' })
    expect(store.getState().logs.every(entry => entry && typeof entry.msg === 'string')).toBe(true)
    expect(store.getState().logs.at(-1)?.msg).toBe('正常消息')
  })

  it('连接停止后的旧配置响应不覆盖状态，也不续发 PICO 配置请求', async () => {
    store.getState().startBackend()
    const request = requests.find(item => item.path === '/api/settings')!
    store.getState().stopBackend()
    const current = store.getState().config
    request.resolve(new Response(JSON.stringify({ ...defaultConfig, storage: { ...defaultConfig.storage, dataset: '旧连接配置' } })))
    await settle()
    expect(store.getState().config).toBe(current)
    expect(requests.some(item => item.path === '/api/pico/network/auto-configure')).toBe(false)
  })

  it.each(['disconnect', 'edit'] as const)('PICO 检测迟到响应不会覆盖更新后的配置：%s', async (interruption) => {
    const original = store.getState().config
    const pending = store.getState().autoConfigurePicoNetwork()
    if (interruption === 'disconnect') store.getState().stopBackend()
    const current = { ...original, storage: { ...original.storage, dataset: '新配置' } }
    store.setState({ config: current })
    requests.find(item => item.path === '/api/pico/network/auto-configure')!.resolve(new Response(JSON.stringify({
      ok: true, data: { config: { ...defaultConfig, picoVision: { ...defaultConfig.picoVision, ip: '192.0.2.8' } }, network: { ifIndex: 1 } },
    })))
    await pending
    expect(store.getState().config.storage).toBe(current.storage)
    expect(store.getState().config.picoVision.ip).toBe(interruption === 'disconnect' ? current.picoVision.ip : '192.0.2.8')
  })
})
