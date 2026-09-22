import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defaultConfig } from '../data'
import type { TelemetryFrame } from '../types'

let store: typeof import('./telemetry').useTelemetryStore
let api: typeof import('../api')
let requests: Array<{ path: string; resolve: (response: Response) => void; reject: (error: Error) => void }>

class OfflineSocket {
  static OPEN = 1
  readyState = 1
  send = vi.fn()
  static instances: OfflineSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor() { OfflineSocket.instances.push(this) }
  close() {}
  emit(frame: TelemetryFrame) { this.onmessage?.({ data: JSON.stringify({ type: 'telemetry', data: frame }) }) }
}

async function settle() { await vi.advanceTimersByTimeAsync(0) }
async function respond(index: number, body: unknown = { ok: true }, status = 200) {
  requests[index].resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
  await settle()
}
function liveFrame(patch: Partial<TelemetryFrame> = {}): TelemetryFrame {
  return { ...store.getState().frame, timestamp: Date.now(), wsOk: true, halOk: true,
    motionAxisEnabled: { left: [true, true, true, true, true, true], right: [true, true, true, true, true, true] },
    motionEnabled: { left: true, right: true },
    forceStatus: { safety: { latched: false, canAcknowledge: true } }, ...patch }
}
async function connect() {
  store.getState().startBackend()
  await settle()
  const socket = OfflineSocket.instances.at(-1)!
  socket.onmessage?.({ data: JSON.stringify({ type: 'control_lease', data: { sessionId: 'safety-test', renewalOwner: 'backend', status: 'active' } }) })
  socket.emit(liveFrame())
  await vi.advanceTimersByTimeAsync(100)
  return socket
}

beforeEach(async () => {
  vi.resetModules()
  vi.useFakeTimers()
  vi.stubEnv('MODE', 'development')
  window.sessionStorage.clear()
  requests = []
  OfflineSocket.instances = []
  vi.stubGlobal('WebSocket', OfflineSocket)
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input)).pathname
    if (path === '/api/settings') return Promise.resolve(new Response(JSON.stringify(defaultConfig)))
    if (path === '/api/settings/snapshots') return Promise.resolve(new Response('[]'))
    if (path === '/api/hardware/status') return Promise.resolve(new Response('{}'))
    if (path === '/api/pico/network/auto-configure') return Promise.resolve(new Response(JSON.stringify({ ok: true, data: { config: defaultConfig, network: {} } })))
    return new Promise<Response>((resolve, reject) => requests.push({ path, resolve, reject }))
  }))
  store = (await import('./telemetry')).useTelemetryStore
  api = await import('../api')
  store.setState({ frame: liveFrame(), telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    // 本组覆盖 HTTP/急停时序，未建 WS 的用例显式提供已确认租约夹具。
    controlLease: { required: true, status: 'active', sessionId: 'safety-test', expiresAt: performance.now() + 2500, reason: '' },
  })
})

afterEach(() => {
  store.getState().stopBackend()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
})

describe('真实 HTTP 分支的急停保护（全部网络隔离）', () => {
  it('显示订阅异常不能阻止急停网络请求', async () => {
    const unsubscribe = store.subscribe(() => { throw new Error('显示订阅失败') })
    expect(() => store.getState().triggerEmergencyStop()).toThrow('显示订阅失败')
    unsubscribe()
    expect(requests[0].path).toBe('/api/motion/emergency_stop')
    expect(store.getState().controlSafety.emergencyRequested).toBe(true)
    await respond(0)
  })
  it('发送失败保留门闩且不伪造已停反馈，仍能再次急停与断使能', async () => {
    const frame = store.getState().frame
    store.getState().triggerEmergencyStop()
    expect(store.getState().frame).toBe(frame)
    expect(store.getState().controlSafety.emergencyRequested).toBe(true)
    store.getState().issueManualAxisMove('left', 'X', 1)
    store.getState().issueManualGripperMove('left', 'close')
    store.getState().setAutoRunning(true)
    store.getState().toggleRecordClutch()
    expect(requests).toHaveLength(1)
    requests[0].reject(new Error('连接失败'))
    await settle()
    expect(store.getState().controlSafety).toMatchObject({ emergencyRequested: true, emergencyPending: false, emergencyError: expect.stringContaining('连接失败') })
    store.setState({ telemetryLink: { state: 'offline', lastFrameReceivedAt: null } })
    store.getState().setMotionEnabled('left', false)
    store.getState().issueManualGripperMove('left', 'stop')
    store.getState().triggerEmergencyStop()
    expect(requests.map((request) => request.path)).toEqual([
      '/api/motion/emergency_stop', '/api/motion/left/disable_all', '/api/gripper/left/command', '/api/motion/emergency_stop',
    ])
    await Promise.all([respond(1), respond(2), respond(3)])
  })

  it('急停请求超时显示未确认，并保持禁止运动', async () => {
    store.getState().triggerEmergencyStop()
    await vi.advanceTimersByTimeAsync(5_001)
    expect(store.getState().controlSafety).toMatchObject({ emergencyPending: false, emergencyRequested: true, emergencyError: expect.stringContaining('超时') })
  })

  it('HTTP 200 的业务拒绝不会被当作急停成功', async () => {
    store.getState().triggerEmergencyStop()
    await respond(0, { ok: false, message: 'HAL 未接受' })
    expect(store.getState().controlSafety.emergencyError).toContain('HAL 未接受')
  })

  it('急停请求失败后的页面重新初始化仍保留急停意图', async () => {
    store.getState().triggerEmergencyStop()
    requests[0].reject(new Error('连接失败'))
    await settle()
    vi.resetModules()
    const reloadedStore = (await import('./telemetry')).useTelemetryStore
    expect(reloadedStore.getState().controlSafety.emergencyRequested).toBe(true)
    reloadedStore.getState().issueManualAxisMove('left', 'X', 1)
    expect(requests).toHaveLength(1)
  })

  it('安全确认响应成功但始终没有解除反馈时允许重新核验', async () => {
    store.getState().triggerEmergencyStop()
    await respond(0)
    store.getState().acknowledgeSafety()
    await respond(1)
    await vi.advanceTimersByTimeAsync(5_001)
    expect(store.getState().controlSafety).toMatchObject({ emergencyRequested: true, acknowledging: false, emergencyError: expect.stringContaining('未获得安全解除反馈') })
  })

  it('旧确认响应不能解除较新的急停', async () => {
    store.getState().triggerEmergencyStop()
    await respond(0)
    store.getState().acknowledgeSafety()
    expect(requests[1].path).toBe('/api/motion/safety/acknowledge')
    store.getState().triggerEmergencyStop()
    await respond(1)
    expect(store.getState().controlSafety).toMatchObject({ emergencyRequested: true, acknowledging: false, acknowledgeAfterFrame: null })
    await respond(2)
  })

  it('确认成功仍等待后续明确未锁存的遥测；保留未知轴且不自动使能', async () => {
    const socket = await connect()
    socket.emit(liveFrame({ forceStatus: { safety: { latched: true, canAcknowledge: true } } }))
    store.getState().acknowledgeSafety()
    await respond(0)
    expect(store.getState().controlSafety.emergencyRequested).toBe(true)
    expect(store.getState().frame.forceStatus?.safety?.latched).toBe(true)
    socket.emit(liveFrame({ motionAxisEnabled: { left: [false, false, false, false, false, false], right: [false, false, null, null, null, null] } }))
    expect(store.getState().controlSafety.emergencyRequested).toBe(false)
    expect(store.getState().frame.motionAxisEnabled.right[2]).toBeNull()
    expect(requests.map((request) => request.path)).toEqual(['/api/motion/safety/acknowledge'])
  })

  it('后台页面的力锁存不等待普通遥测节流', async () => {
    const socket = await connect()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    socket.emit(liveFrame({ dangerIndex: 0.1, forceStatus: { safety: { latched: true, canAcknowledge: false } } }))
    expect(store.getState().frame.forceStatus?.safety?.latched).toBe(true)
    store.getState().setMotionEnabled('left', true)
    expect(requests).toHaveLength(0)
    vi.restoreAllMocks()
  })

  it('双臂回原点在第一侧等待期间急停，即使已确认恢复也不续发第二侧', async () => {
    const socket = await connect()
    store.setState((state) => ({ recordSession: { ...state.recordSession, resetRequiredSides: ['left', 'right'] } }))
    store.getState().homeRecordArms()
    expect(requests[0].path).toBe('/api/motion/left/return_origin')
    store.getState().triggerEmergencyStop()
    await respond(1)
    store.getState().acknowledgeSafety()
    await respond(2)
    socket.emit(liveFrame())
    await vi.advanceTimersByTimeAsync(100)
    expect(store.getState().controlSafety.emergencyRequested).toBe(false)
    await respond(0)
    expect(requests.some((request) => request.path === '/api/motion/right/return_origin')).toBe(false)
    expect(store.getState().recordSession.resetReady).toBe(false)
  })

  it('录制启动准备期间急停，迟到的状态响应不能创建录制或恢复启动状态', async () => {
    store.getState().startRecordSession('safety-test', 'task')
    expect(requests[0].path).toBe('/api/record/status')
    store.getState().triggerEmergencyStop()
    await respond(1)
    await respond(0, { active: false })
    expect(requests.some((request) => request.path === '/api/record/session/create')).toBe(false)
    expect(store.getState().recordSession.phase).toBe('idle')
  })

  it('Tare 不改写危险值或力读数', async () => {
    const frame = liveFrame({ dangerIndex: 1.1, forceLeft: [3, 4, 5, 0, 0, 0] })
    store.setState({ frame })
    store.getState().tareRecordForceSensors()
    await respond(0)
    store.getState().setDangerOverride(0)
    expect(store.getState().frame).toBe(frame)
    expect(store.getState().dangerOverride).toBeNull()
  })

  it('锁存期间未确认卸载的 Tare 和单侧 Tare 仍被拒绝', async () => {
    const frame = liveFrame({ forceStatus: { safety: { latched: true } } })
    store.setState({ frame })
    store.getState().tareRecordForceSensors()
    await settle()
    await expect(api.tareForceSensor('left')).rejects.toThrow('锁存')
    expect(requests).toHaveLength(0)
    expect(store.getState().frame).toBe(frame)
  })

  it('锁存期间已确认卸载的双侧自检保留控制租约门闩，不伪造安全反馈', async () => {
    const frame = liveFrame({ forceStatus: { source: 'hkvl_serial', calibration: { state: 'waiting_sensors' }, safety: { latched: true } } })
    store.setState({ frame })
    const pending = api.runHkvlStartupSelfCheck()
    expect(requests[0].path).toBe('/api/sensors/tare')
    expect(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.body).toBe(JSON.stringify({ unloadedConfirmed: true }))
    await expect(api.runHkvlStartupSelfCheck()).rejects.toThrow('正在进行')
    await respond(0, { ok: true, data: { hal: { response: { calibration: { state: 'ready_for_ack' } } } } })
    await pending
    expect(store.getState().frame).toBe(frame)
    expect(requests.some((request) => request.path.includes('acknowledge'))).toBe(false)
    store.setState((state) => ({ controlLease: { ...state.controlLease, status: 'expired', expiresAt: 0, reason: '租约过期' } }))
    await expect(api.runHkvlStartupSelfCheck()).rejects.toThrow('租约')
    expect(requests).toHaveLength(1)
  })

  it('未知轴反馈拒绝点动，新鲜且明确使能的轴才发送', async () => {
    store.setState({ frame: liveFrame({ motionAxisEnabled: { left: [null, true, true, true, true, true], right: [] } }) })
    store.getState().issueManualAxisMove('left', 'X', 1)
    expect(requests).toHaveLength(0)
    store.getState().issueManualAxisMove('left', 'Y', 1)
    expect(requests[0].path).toBe('/api/motion/manual_axis_move')
    await respond(0)
  })

  it('自动运行的迟到启动响应不能覆盖停止', async () => {
    store.getState().setAutoRunning(true)
    store.getState().setAutoRunning(false)
    await respond(1)
    await respond(0)
    expect(store.getState().autoRunning).toBe(false)
  })

  it('急停后的旧夹爪使能响应不回写进度或发起配置回读', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    store.getState().triggerEmergencyStop()
    await respond(0)
    expect(store.getState().gripperCommand.left.phase).toBe('idle')
    expect(requests).toHaveLength(2)
    await respond(1)
  })

  it('直接调用 API 也不能绕过保护，但停止与断开仍能发送', async () => {
    store.getState().triggerEmergencyStop()
    await respond(0)
    await expect(api.homeMotionSide('left')).rejects.toThrow('急停')
    await expect(api.connectTeleopHand('left')).rejects.toThrow('急停')
    await expect(api.queueAutoAction({})).rejects.toThrow('急停')
    await expect(api.dispatchNextAutoAction()).rejects.toThrow('急停')
    const stopped = api.stopMotionSide('left')
    await respond(1)
    await stopped
    expect(requests).toHaveLength(2)
  })
})
