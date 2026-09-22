import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogEntry } from '../types'

let store: typeof import('./telemetry').useTelemetryStore
let api: typeof import('../api')

class Socket {
  static instances: Socket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor() { Socket.instances.push(this) }
  close() {}
  emitLog(entry: LogEntry) { this.onmessage?.({ data: JSON.stringify({ type: 'log', data: entry }) }) }
}

function log(patch: Partial<LogEntry> = {}): LogEntry {
  return { id: 1, ts: 1000, channel: '[BACKEND]', level: 'INFO', msg: '配置已保存并应用', ...patch }
}

function connect() {
  store.getState().startBackend()
  return Socket.instances.at(-1)!
}

beforeEach(async () => {
  vi.resetModules()
  vi.useFakeTimers()
  vi.stubEnv('MODE', 'development')
  window.sessionStorage.clear()
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
  api = await import('../api')
  store = (await import('./telemetry')).useTelemetryStore
})

afterEach(() => {
  store.getState().stopBackend()
  store.getState().stopMock()
  vi.restoreAllMocks()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  window.sessionStorage.clear()
})

describe('日志摄入去重', () => {
  it('重连重放完整缓存时不挤占5000条容量且保持日志数组引用', () => {
    const first = connect()
    const entries = Array.from({ length: 5000 }, (_, index) => log({ id: index + 1, ts: index + 1000 }))
    store.setState({ logs: entries })
    store.getState().stopBackend()
    const reconnected = connect()
    expect(reconnected).not.toBe(first)
    for (const entry of entries) reconnected.emitLog({ ...entry })
    expect(store.getState().logs).toBe(entries)
    expect(store.getState().logs).toHaveLength(5000)
  })

  it.each([
    { id: 2 },
    { ts: 1001 },
    { channel: '[HAL]' as const },
    { level: 'WARNING' as const },
    { msg: '配置应用失败' },
  ])('完整标识中的任一字段变化都作为新事件保留：%j', (patch) => {
    const socket = connect()
    const first = log()
    const next = log(patch)
    store.setState({ logs: [first] })
    socket.emitLog(next)
    expect(store.getState().logs).toEqual([first, next])
  })

  it('本地和后端同编号日志都保留，真正的新日志仍受容量限制', () => {
    const socket = connect()
    const local = store.getState().logs[0]
    const remote = log({ id: local.id, ts: local.ts, msg: '后端会话初始化' })
    socket.emitLog(remote)
    expect(store.getState().logs).toEqual([local, remote])

    const entries = Array.from({ length: 5000 }, (_, index) => log({ id: index + 1 }))
    store.setState({ logs: entries })
    const newest = log({ id: 5001 })
    socket.emitLog(newest)
    expect(store.getState().logs).toHaveLength(5000)
    expect(store.getState().logs[0]).toBe(entries[1])
    expect(store.getState().logs.at(-1)).toEqual(newest)
  })
})

describe('设置操作日志来源', () => {
  it('真实模式等待后端回传，不先写一份带backend command后缀的副本', async () => {
    const socket = connect()
    const request = vi.spyOn(api, 'sendSettingsLogCommand').mockResolvedValue({ ok: true, data: {}, ts: 1000 })
    const before = store.getState().logs
    store.getState().sendBackendCommandLog('INFO', '配置已保存并应用', '[HAL]')
    expect(request).toHaveBeenCalledWith('[HAL]', '配置已保存并应用', 'INFO')
    expect(store.getState().logs).toBe(before)
    await Promise.resolve()
    const returned = log({ channel: '[HAL]' })
    socket.emitLog(returned)
    expect(store.getState().logs).toEqual([...before, returned])
  })

  it('请求失败仍产生本地ERROR，不写入尚未由后端记录的成功日志', async () => {
    vi.spyOn(api, 'sendSettingsLogCommand').mockRejectedValue(new Error('网络不可用'))
    const before = store.getState().logs
    store.getState().sendBackendCommandLog('INFO', '配置已保存并应用', '[HAL]')
    await Promise.resolve()
    expect(store.getState().logs).toHaveLength(before.length + 1)
    expect(store.getState().logs.at(-1)).toMatchObject({
      level: 'ERROR', channel: '[BACKEND]', msg: 'settings log command failed: Error: 网络不可用',
    })
  })

  it('测试模式仍直接显示本地日志，不请求后端', async () => {
    vi.resetModules()
    vi.stubEnv('MODE', 'test')
    api = await import('../api')
    store = (await import('./telemetry')).useTelemetryStore
    const request = vi.spyOn(api, 'sendSettingsLogCommand')
    store.getState().sendBackendCommandLog('INFO', '配置已保存并应用', '[HAL]')
    expect(store.getState().logs.at(-1)).toMatchObject({
      level: 'INFO', channel: '[HAL]', msg: '配置已保存并应用 · test fixture',
    })
    expect(request).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })
})
