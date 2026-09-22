import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defaultConfig } from '../data'
import type { AppConfig } from '../types'

let store: typeof import('./telemetry').useTelemetryStore
let requests: Array<{
  url: string
  resolve: (response: Response) => void
  reject: (error: Error) => void
}>

async function settleRequests() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

async function respond(index: number, body: unknown, status = 200) {
  requests[index].resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }))
  await settleRequests()
}

function configWithGripper(patch: Partial<AppConfig['gripper']>) {
  const config = structuredClone(defaultConfig)
  config.gripper = { ...config.gripper, leftEnabled: false, rightEnabled: false, ...patch }
  return config
}

beforeEach(async () => {
  vi.resetModules()
  // 使用真实 HTTP 封装和 store 的非 mock 分支；网络完全由可控 Promise 替代。
  vi.stubEnv('MODE', 'development')
  requests = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => new Promise<Response>((resolve, reject) => {
    requests.push({ url: String(input), resolve, reject })
  })))
  store = (await import('./telemetry')).useTelemetryStore
  store.setState((state) => ({
    // 本组只验证夹爪请求归属；协议续租和过期由 telemetry.controlLease.test 覆盖。
    controlLease: { required: true, status: 'active', sessionId: 'gripper-test', expiresAt: performance.now() + 2500, reason: '' },
    config: configWithGripper({}),
    frame: {
      ...state.frame,
      timestamp: Date.now(),
      wsOk: true,
      halOk: true,
      forceStatus: { safety: { latched: false } },
    },
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
  }))
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('真实夹爪请求的异步归属', () => {
  it.each(['before', 'after'] as const)('旧命令在新命令成功 %s 失败，不覆盖最新进度', async (order) => {
    store.getState().issueManualGripperMove('left', 'open')
    store.getState().issueManualGripperMove('left', 'stop')
    const latestId = store.getState().gripperCommand.left.requestId
    expect(requests.map((request) => request.url)).toEqual([
      expect.stringContaining('/api/gripper/left/command'),
      expect.stringContaining('/api/gripper/left/command'),
    ])

    if (order === 'after') await respond(1, { ok: true })
    requests[0].reject(new Error('旧打开请求超时'))
    await settleRequests()
    expect(store.getState().gripperCommand.left).toMatchObject({
      requestId: latestId,
      command: 'stop',
      phase: order === 'after' ? 'accepted' : 'sending',
    })
    if (order === 'before') await respond(1, { ok: true })
    expect(store.getState().gripperCommand.left).toMatchObject({ phase: 'accepted', command: 'stop' })
  })

  it('旧启停命令失败不能回滚后续命令保留的请求使能标志', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    store.getState().issueManualGripperMove('left', 'open')
    await respond(1, { ok: true })
    requests[0].reject(new Error('旧使能请求超时'))
    await settleRequests()

    expect(store.getState().config.gripper.leftEnabled).toBe(true)
    expect(store.getState().gripperCommand.left).toMatchObject({ phase: 'accepted', command: 'open' })
  })

  it('旧启停成功不能覆盖新命令或发起无归属的配置回读', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    store.getState().issueManualGripperMove('left', 'stop')
    await respond(0, { ok: true })

    expect(requests).toHaveLength(2)
    expect(store.getState().gripperCommand.left).toMatchObject({ phase: 'sending', command: 'stop' })
    await respond(1, { ok: true })
    expect(store.getState().gripperCommand.left.phase).toBe('accepted')
  })

  it('迟到的配置回读不能撤销后续断使能及其权威回读', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    await respond(0, { ok: true })
    expect(requests[1].url).toContain('/api/settings')
    store.getState().issueManualGripperMove('left', 'disable')
    await respond(2, { ok: true })
    await respond(3, configWithGripper({ leftEnabled: false }))
    await respond(1, configWithGripper({ leftEnabled: true }))

    expect(store.getState().config.gripper.leftEnabled).toBe(false)
    expect(store.getState().gripperCommand.left).toMatchObject({ phase: 'accepted', command: 'disable' })
  })

  it('全量配置回读不能覆盖另一侧刚提交的请求', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    await respond(0, { ok: true })
    store.getState().issueManualGripperMove('right', 'enable')
    await respond(1, configWithGripper({ leftEnabled: true, rightEnabled: false }))

    expect(store.getState().config.gripper.rightEnabled).toBe(true)
    await respond(2, { ok: true })
    await respond(3, configWithGripper({ leftEnabled: true, rightEnabled: true }))
  })

  it('全量配置回读不能覆盖期间收到的新配置', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    await respond(0, { ok: true })
    const latestConfig = configWithGripper({ leftEnabled: true, commandForceLimitN: 3 })
    store.setState({ config: latestConfig })
    await respond(1, configWithGripper({ leftEnabled: true, commandForceLimitN: 8 }))

    expect(store.getState().config.gripper.commandForceLimitN).toBe(3)
  })

  it('当前启停请求成功仍应用权威回读', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    await respond(0, { ok: true })
    await respond(1, configWithGripper({ leftEnabled: false }))

    expect(store.getState().config.gripper.leftEnabled).toBe(false)
    expect(store.getState().gripperCommand.left).toMatchObject({ phase: 'accepted', command: 'enable' })
  })

  it('当前启停请求 HTTP 失败仍报告失败并回滚其请求标志', async () => {
    store.getState().issueManualGripperMove('left', 'enable')
    await respond(0, { detail: { message: '端口不可用' } }, 503)

    await vi.waitFor(() => expect(store.getState().gripperCommand.left.phase).toBe('failed'))
    expect(store.getState().config.gripper.leftEnabled).toBe(false)
    expect(store.getState().gripperCommand.left.message).toContain('端口不可用')
  })
})
