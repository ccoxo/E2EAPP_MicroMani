/** 页面控制会话状态；执行侧租约由后端独立维护。 */
export interface ControlLeaseState {
  required: boolean
  status: 'pending' | 'active' | 'expired'
  sessionId: string | null
  expiresAt: number | null
  reason: string
}

export function initialControlLease(required = true): ControlLeaseState {
  return { required, status: 'pending', sessionId: null, expiresAt: null, reason: '等待执行侧安全租约确认' }
}

export function controlLeaseBlockReason(lease: ControlLeaseState | undefined, now = performance.now()): string | null {
  if (lease?.required === false) return null
  if (lease?.status === 'active' && (lease.expiresAt === null || lease.expiresAt > now)) return null
  return lease?.status === 'active' ? '执行侧安全租约已过期，禁止启动运动' : lease?.reason || '缺少执行侧安全租约确认，禁止启动运动'
}

interface LeaseSessionOptions {
  isCurrent: () => boolean
  publish: (state: ControlLeaseState) => void
  close: (reason: string, restartRequired?: boolean) => void
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** HAL 续租由后端执行；页面不发送心跳，也不因主线程暂停关闭连接。 */
export function createControlLeaseSession(options: LeaseSessionOptions) {
  let stopped = false
  let state = initialControlLease()
  const dispose = () => { stopped = true }
  const revoke = (reason: string, restartRequired = false) => {
    if (stopped) return
    dispose()
    state = { ...state, status: 'expired', expiresAt: null, reason }
    try { options.close(reason, restartRequired) } finally { options.publish(state) }
  }
  const receive = (message: unknown): boolean => {
    if (!record(message) || !['safety_challenge', 'control_lease'].includes(String(message.type))) return false
    if (stopped || !options.isCurrent()) return true
    if (message.type === 'safety_challenge') {
      revoke('后端仍要求页面心跳，请同步更新并重启后端')
      return true
    }
    const data = message.data
    if (!record(data) || typeof data.sessionId !== 'string' || !data.sessionId || data.sessionId.length > 128
      || (state.sessionId !== null && state.sessionId !== data.sessionId)) {
      revoke('控制会话消息无效或会话不匹配')
      return true
    }
    if (data.status === 'expired') {
      revoke(data.restartRequired === true
        ? 'DDS 控制通道已隔离，请停止设备并重启后端，再显式重连和确认安全状态'
        : '执行侧连接已失效，控制已暂停', data.restartRequired === true)
      return true
    }
    if (data.renewalOwner !== 'backend' || !['pending', 'active'].includes(String(data.status))) {
      revoke('后端控制会话协议不匹配，请同步更新后端')
      return true
    }
    state = { required: true, status: data.status as 'pending' | 'active', sessionId: data.sessionId,
      expiresAt: null, reason: data.status === 'active' ? '' : '等待执行侧安全租约确认' }
    options.publish(state)
    return true
  }
  return { receive, revoke, dispose }
}
