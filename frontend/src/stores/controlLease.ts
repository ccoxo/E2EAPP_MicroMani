/** 浏览器主线程必须逐次回答后端挑战；定时器只能撤销，不能续租。 */
export interface ControlLeaseState {
  required: boolean
  status: 'pending' | 'active' | 'expired'
  sessionId: string | null
  expiresAt: number | null
  reason: string
}

export const challengeTimeoutMs = 2_000
export const executionLeaseTimeoutMs = 2_500

export function initialControlLease(required = true): ControlLeaseState {
  return { required, status: 'pending', sessionId: null, expiresAt: null, reason: '等待执行侧安全租约确认' }
}

export function controlLeaseBlockReason(lease: ControlLeaseState | undefined, now = performance.now()): string | null {
  if (lease?.required === false) return null
  if (lease?.status === 'active' && lease.expiresAt !== null && lease.expiresAt > now) return null
  return lease?.status === 'active' ? '执行侧安全租约已过期，禁止启动运动' : lease?.reason || '缺少执行侧安全租约确认，禁止启动运动'
}

interface LeaseSessionOptions {
  isCurrent: () => boolean
  send: (message: string) => void
  publish: (state: ControlLeaseState) => void
  close: (reason: string) => void
  now?: () => number
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}
function token(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 128
}
function validTtl(value: unknown, maximum: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= maximum
}

export function createControlLeaseSession(options: LeaseSessionOptions) {
  const now = options.now ?? (() => performance.now())
  let stopped = false
  let sessionId: string | null = null
  let lastChallengeAt = now()
  let currentChallengeTtl = challengeTimeoutMs
  let response: { challengeId: string; respondedAt: number; deadline: number } | null = null
  let state = initialControlLease()
  let timer: ReturnType<typeof setTimeout> | undefined
  const answered = new Set<string>()

  const dispose = () => {
    stopped = true
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }
  const revoke = (reason: string) => {
    if (stopped) return
    dispose()
    state = { ...state, status: 'expired', expiresAt: null, reason }
    // 先关闭传输；即使显示订阅抛错，也不能再为执行侧续租。
    try { options.close(reason) } finally { options.publish(state) }
  }
  const watchDeadline = () => {
    if (timer !== undefined) clearTimeout(timer)
    const deadline = Math.min(lastChallengeAt + currentChallengeTtl, state.expiresAt ?? Infinity)
    timer = setTimeout(() => {
      if (stopped || !options.isCurrent()) { dispose(); return }
      revoke('页面主线程响应或执行侧租约超时，控制已暂停')
    }, Math.max(0, deadline - now()))
  }
  const publish = (next: ControlLeaseState) => {
    state = next
    options.publish(next)
  }

  const receive = (message: unknown): boolean => {
    if (!record(message) || !['safety_challenge', 'control_lease'].includes(String(message.type))) return false
    if (stopped || !options.isCurrent()) return true
    try {
      const time = now()
      // 不使用计时器刷新此时间：主线程恢复后的积压包必须先通过这个检查。
      if (time - lastChallengeAt >= currentChallengeTtl || (state.expiresAt !== null && time >= state.expiresAt)) {
        revoke('页面主线程响应超时，旧控制会话已失效')
        return true
      }
      const data = message.data
      if (!record(data) || !token(data.sessionId)) throw new Error('安全租约消息格式无效')
      if (sessionId !== null && sessionId !== data.sessionId) throw new Error('安全租约会话不匹配')
      if (message.type === 'safety_challenge') {
        if (!token(data.challengeId) || !validTtl(data.ttlMs, challengeTimeoutMs)) throw new Error('安全挑战无效')
        if (answered.has(data.challengeId)) return true
        sessionId = data.sessionId
        lastChallengeAt = time
        currentChallengeTtl = data.ttlMs
        response = { challengeId: data.challengeId, respondedAt: time, deadline: time + data.ttlMs }
        answered.add(data.challengeId)
        if (answered.size > 128) answered.delete(answered.values().next().value!)
        options.send(JSON.stringify({ type: 'safety_heartbeat', data: { sessionId, challengeId: data.challengeId } }))
        if (state.sessionId === null) publish({ ...state, sessionId })
        watchDeadline()
        return true
      }
      if (data.status === 'expired') { revoke('执行侧安全租约已失效，控制已暂停'); return true }
      if (data.status === 'pending') {
        publish({ ...state, status: 'pending', expiresAt: null, reason: '等待执行侧安全租约确认' })
        watchDeadline()
        return true
      }
      if (data.status !== 'active' || !validTtl(data.ttlMs, executionLeaseTimeoutMs)) throw new Error('执行侧安全租约确认无效')
      if (!response || data.challengeId !== response.challengeId) return true
      if (time >= response.deadline) { revoke('执行侧租约确认迟到，旧控制会话已失效'); return true }
      // 截止时间从本主线程回答挑战时算起，迟到确认不能延长授权。
      const expiresAt = response.respondedAt + data.ttlMs
      if (expiresAt <= time) { revoke('执行侧安全租约已过期'); return true }
      publish({ required: true, status: 'active', sessionId, expiresAt, reason: '' })
      watchDeadline()
    } catch (error) {
      revoke(`安全租约处理失败：${String(error)}`)
    }
    return true
  }

  watchDeadline()
  return { receive, revoke, dispose }
}
