import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { controlLeaseBlockReason, createControlLeaseSession, initialControlLease } from './controlLease'

let time: number
let current: boolean
const challenge = (challengeId = 'nonce-1', sessionId = 'session-1') => ({ type: 'safety_challenge', data: { sessionId, challengeId, ttlMs: 2000 } })
const confirmation = (challengeId = 'nonce-1', status = 'active') => ({ type: 'control_lease', data: { sessionId: 'session-1', challengeId, status, ttlMs: 2500 } })
const send = vi.fn()
const publish = vi.fn()
const close = vi.fn()
const create = () => createControlLeaseSession({ isCurrent: () => current, now: () => time, send, publish, close })

beforeEach(() => { vi.useFakeTimers(); time = 0; current = true; vi.clearAllMocks() })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })

describe('主线程安全挑战与执行侧租约', () => {
  it('未确认的真实连接拒绝控制，仅显式 mock 不要求租约', () => {
    expect(controlLeaseBlockReason(undefined, 0)).toContain('缺少')
    expect(controlLeaseBlockReason(initialControlLease(), 0)).toContain('等待')
    expect(controlLeaseBlockReason(initialControlLease(false), 0)).toBeNull()
  })

  it('收到当前挑战才应答，配对执行侧确认才允许控制', () => {
    const session = create()
    expect(send).not.toHaveBeenCalled()
    session.receive(challenge())
    expect(JSON.parse(send.mock.calls[0][0])).toEqual({ type: 'safety_heartbeat', data: { sessionId: 'session-1', challengeId: 'nonce-1' } })
    expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0], time)).not.toBeNull()
    session.receive(confirmation('wrong'))
    expect(publish.mock.calls.at(-1)![0].status).toBe('pending')
    session.receive(confirmation())
    expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0], time)).toBeNull()
  })

  it('首次握手期间禁止控制，不按运行中心跳期限反复断连', () => {
    create()
    time = 2000
    vi.advanceTimersByTime(2000)
    expect(send).not.toHaveBeenCalled()
    expect(close).not.toHaveBeenCalled()
    time = 10_000
    vi.advanceTimersByTime(8000)
    expect(close).toHaveBeenCalledTimes(1)
    expect(publish.mock.calls.at(-1)![0].status).toBe('expired')
  })

  it.each(['challenge', 'confirmation'] as const)('主线程暂停后先处理积压 %s 也不能续旧会话', (kind) => {
    const session = create()
    session.receive(challenge())
    session.receive(confirmation())
    time = 2100 // 不执行计时器，模拟积压 message 先于超时 timer 被调度。
    session.receive(kind === 'challenge' ? challenge('nonce-2') : confirmation())
    expect(close).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledTimes(1)
    expect(publish.mock.calls.at(-1)![0].status).toBe('expired')
  })

  it('断开/卸载后到达的挑战与确认不再应答或改变状态', () => {
    const session = create()
    session.receive(challenge())
    session.dispose()
    session.receive(challenge('nonce-2'))
    session.receive(confirmation())
    expect(send).toHaveBeenCalledTimes(1)
    expect(publish.mock.calls.at(-1)![0].status).toBe('pending')
  })

  it('被新 WS 替换的旧连接不能答复', () => {
    const session = create()
    current = false
    session.receive(challenge())
    expect(send).not.toHaveBeenCalled()
  })

  it('跨会话挑战撤销当前租约', () => {
    const session = create()
    session.receive(challenge())
    session.receive(challenge('nonce-2', 'other-session'))
    expect(close).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('重复挑战不重新应答也不延长本地截止时间', () => {
    const session = create()
    session.receive(challenge())
    time = 1500
    session.receive(challenge())
    time = 2000
    vi.advanceTimersByTime(2000)
    expect(send).toHaveBeenCalledTimes(1)
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('迟到确认的截止时间仍从答复开始算，不能从收到确认时重新获得完整租约', () => {
    const session = create()
    session.receive(challenge())
    time = 1000
    session.receive(confirmation())
    expect(publish.mock.calls.at(-1)![0].expiresAt).toBe(2500)
    expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0], 2500)).toContain('过期')
  })

  it('新挑战到达后仍接受未过期的上一条确认，但不延长其原始期限', () => {
    const session = create()
    session.receive(challenge())
    time = 500
    session.receive(challenge('nonce-2'))
    session.receive(confirmation())
    expect(publish.mock.calls.at(-1)![0]).toMatchObject({ status: 'active', expiresAt: 2500 })
    time = 600
    session.receive(confirmation('nonce-2'))
    expect(publish.mock.calls.at(-1)![0].expiresAt).toBe(3000)
    session.receive(confirmation())
    expect(publish.mock.calls.at(-1)![0].expiresAt).toBe(3000)
    expect(close).not.toHaveBeenCalled()
  })

  it('已过期的旧确认不影响仍新鲜的新确认', () => {
    const session = create()
    session.receive(challenge()); session.receive(confirmation())
    time = 1500
    session.receive(challenge('nonce-2')); session.receive(confirmation('nonce-2'))
    time = 2100
    session.receive(confirmation())
    expect(close).not.toHaveBeenCalled()
    expect(publish.mock.calls.at(-1)![0].expiresAt).toBe(4000)
  })

  it('连续一分钟确认延迟跨过下一次挑战时，健康会话不误过期', () => {
    const session = create()
    session.receive(challenge()); session.receive(confirmation())
    for (let i = 1; i <= 120; i++) {
      time = i * 500
      session.receive(challenge(`nonce-${i}`))
      if (i > 1) {
        time += 100
        session.receive(confirmation(`nonce-${i - 1}`))
      }
      expect(close).not.toHaveBeenCalled()
      expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0], time)).toBeNull()
    }
  })

  it('首个挑战延迟到达时可完成握手，未确认前始终拒绝控制', () => {
    const session = create()
    expect(controlLeaseBlockReason(undefined, 0)).not.toBeNull()
    time = 3000
    session.receive(challenge()); session.receive(confirmation())
    expect(close).not.toHaveBeenCalled()
    expect(publish.mock.calls.at(-1)![0]).toMatchObject({ status: 'active', expiresAt: 5500 })
  })

  it('即使挑战仍持续，执行侧确认已过期也不能继续回答旧会话', () => {
    const session = create()
    session.receive(challenge()); session.receive(confirmation())
    time = 1500
    session.receive(challenge('nonce-2'))
    time = 2600
    session.receive(challenge('nonce-3'))
    expect(send).toHaveBeenCalledTimes(2)
    expect(close).toHaveBeenCalledTimes(1)
    expect(publish.mock.calls.at(-1)![0].status).toBe('expired')
  })

  it.each([0, -1, 2001, '2000'])('非法挑战期限 %j 不应答', (ttlMs) => {
    const session = create()
    session.receive({ ...challenge(), data: { ...challenge().data, ttlMs } })
    expect(send).not.toHaveBeenCalled()
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('发送失败先关闭会话，此后正常挑战也不能复活旧会话', () => {
    send.mockImplementationOnce(() => { throw new Error('socket closed') })
    const session = create()
    session.receive(challenge())
    session.receive(challenge('nonce-2'))
    expect(send).toHaveBeenCalledTimes(1)
    expect(close).toHaveBeenCalledTimes(1)
    expect(publish.mock.calls.at(-1)![0].status).toBe('expired')
  })
})
