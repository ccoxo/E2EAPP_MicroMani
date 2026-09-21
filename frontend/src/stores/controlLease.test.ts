import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { controlLeaseBlockReason, createControlLeaseSession, initialControlLease } from './controlLease'

const publish = vi.fn(), close = vi.fn()
let current = true
const create = () => createControlLeaseSession({ isCurrent: () => current, publish, close })
const confirmation = (status = 'active', sessionId = 's1') => ({ type: 'control_lease', data: { sessionId, status, renewalOwner: 'backend' } })
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); current = true })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })

it('首次连接仍需后端执行侧确认', () => {
  expect(controlLeaseBlockReason(initialControlLease())).toContain('等待')
  expect(controlLeaseBlockReason(undefined)).toContain('缺少')
  expect(controlLeaseBlockReason(initialControlLease(false))).toBeNull()
  create().receive(confirmation())
  expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0])).toBeNull()
})
it('页面暂停或未收到周期消息不再主动关闭连接', () => {
  const session = create()
  vi.advanceTimersByTime(60_000)
  expect(close).not.toHaveBeenCalled()
  session.receive(confirmation())
  vi.advanceTimersByTime(60_000)
  expect(close).not.toHaveBeenCalled()
  expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0], 120_000)).toBeNull()
  expect(vi.getTimerCount()).toBe(0)
})
it('后端明确报告失联仍撤销会话', () => {
  const session = create()
  session.receive(confirmation())
  session.receive(confirmation('expired'))
  expect(close).toHaveBeenCalledOnce()
  expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0])).toContain('失效')
})
it('DDS 隔离仍提示重启', () => {
  create().receive({ type: 'control_lease', data: { sessionId: 's1', status: 'expired', restartRequired: true } })
  expect(close).toHaveBeenCalledWith(expect.stringContaining('重启后端'), true)
})
it('旧后端不得绕过更新检查', () => {
  create().receive({ type: 'safety_challenge', data: { sessionId: 's1' } })
  expect(close).toHaveBeenCalledWith(expect.stringContaining('更新'), false)
})
it.each([{}, { sessionId: 's1', status: 'active' }, { sessionId: '', status: 'active' }])('无效确认保持拒绝控制', (data) => {
  create().receive({ type: 'control_lease', data })
  expect(close).toHaveBeenCalledOnce()
})
it('连接更换或 dispose 后旧消息不能恢复会话', () => {
  const session = create()
  current = false
  session.receive(confirmation())
  current = true
  session.dispose()
  session.receive(confirmation())
  expect(publish).not.toHaveBeenCalled()
})
it('跨会话确认拒绝，pending 撤销启动权', () => {
  const session = create()
  session.receive(confirmation())
  session.receive(confirmation('pending'))
  expect(controlLeaseBlockReason(publish.mock.calls.at(-1)![0])).toContain('等待')
  session.receive(confirmation('active', 'other'))
  expect(close).toHaveBeenCalledOnce()
})
it('撤销时先关闭传输，即使显示订阅抛错', () => {
  const session = create()
  publish.mockImplementationOnce(() => { throw new Error('display') })
  expect(() => session.revoke('stop')).toThrow('display')
  expect(close).toHaveBeenCalledOnce()
})
