import { telemetryStaleAfterMs } from '../hardwareStatus'
import type { ManualControlSide, TelemetryFrame, TelemetryLinkStatus } from '../types'

export type MotionCommandPhase = 'idle' | 'sending' | 'waitingConfirm' | 'confirmed' | 'failed' | 'timeout'
export interface MotionCommandState {
  phase: MotionCommandPhase
  requestId: number
  targetEnabled: boolean | null
  queuedEnabled: boolean | null
  // 展示超时或断连不能释放尚未返回的 HTTP 请求。
  transportPending: boolean
  message: string
}
export type MotionCommands = Record<ManualControlSide, MotionCommandState>
export const motionCommandTimeoutMs = 5_000

export function initialMotionCommand(): MotionCommandState {
  return { phase: 'idle', requestId: 0, targetEnabled: null, queuedEnabled: null, transportPending: false, message: '' }
}

export function motionTelemetryIsLive(
  frame: Pick<TelemetryFrame, 'wsOk' | 'halOk'>,
  link: TelemetryLinkStatus,
) {
  return link.state === 'live' && frame.wsOk && frame.halOk
    && link.lastFrameReceivedAt !== null && Date.now() - link.lastFrameReceivedAt <= telemetryStaleAfterMs
}

export function motionDeviceState(
  frame: Pick<TelemetryFrame, 'wsOk' | 'halOk' | 'motionAxisEnabled'>,
  link: TelemetryLinkStatus,
  side: ManualControlSide,
) {
  if (!motionTelemetryIsLive(frame, link)) return 'unknown'
  const axes = frame.motionAxisEnabled?.[side] ?? []
  // motionEnabled 仅聚合已知轴，不能用它把部分可读反馈提升为全部轴确认。
  if (axes.length === 6 && axes.every((value) => value === true)) return 'enabled'
  if (axes.length === 6 && axes.every((value) => value === false)) return 'disabled'
  return axes.some((value) => value === true) ? 'partial' : 'unknown'
}

export const motionDeviceLabels = {
  enabled: '已使能', disabled: '未使能', partial: '部分使能', unknown: '使能状态未知',
} as const

export function motionCommandLabel(command: MotionCommandState) {
  const queued = command.queuedEnabled === null ? '' : `；${command.queuedEnabled ? '使能' : '断使能'}请求排队中`
  const transport = command.phase === 'timeout' && command.transportPending ? '；原请求仍等待响应' : ''
  return command.message + transport + queued
}

interface Snapshot {
  frame: TelemetryFrame
  telemetryLink: TelemetryLinkStatus
  motionCommand: MotionCommands
}
interface Request {
  id: number
  enabled: boolean
  afterSequence: number
  afterTimestamp: number
  feedbackMatches: boolean
  feedbackReceivedAt: number | null
  accepted: boolean
  terminal: boolean
  timer: ReturnType<typeof setTimeout> | null
}
interface Lane {
  active: Request | null
  inFlight: Request | null
  queued: boolean | null
}

/** 每侧串行发送；请求进度独立于设备遥测，不由 API 成功制造设备反馈。 */
export function createMotionCommandController(
  read: () => Snapshot,
  write: (side: ManualControlSide, command: MotionCommandState) => void,
  send: (side: ManualControlSide, enabled: boolean) => Promise<unknown>,
  enableBlockedReason: () => string | null = () => null,
) {
  const lanes: Record<ManualControlSide, Lane> = {
    left: { active: null, inFlight: null, queued: null },
    right: { active: null, inFlight: null, queued: null },
  }
  let nextId = 0
  let receivedSequence = 0
  let lastReceivedTimestamp = 0

  function clearTimer(request: Request | null) {
    if (request?.timer !== null && request?.timer !== undefined) clearTimeout(request.timer)
    if (request) request.timer = null
  }

  function update(side: ManualControlSide, patch: Partial<MotionCommandState>) {
    write(side, { ...read().motionCommand[side], ...patch })
  }

  function safetyBlockReason(frame = read().frame) {
    return enableBlockedReason() ?? (frame.forceStatus?.safety?.latched ? '安全锁存，禁止使能' : null)
  }

  function enableBlockReason() {
    const state = read()
    return safetyBlockReason(state.frame)
      ?? (motionTelemetryIsLive(state.frame, state.telemetryLink) ? null : '遥测不可用')
  }

  /** 急停只撤销使能意图；仍保留操作员已请求的断使能及其 HTTP 顺序。 */
  function blockEnabling(reason: string) {
    for (const side of ['left', 'right'] as const) {
      const lane = lanes[side]
      const cancelledQueuedEnable = lane.queued === true
      if (cancelledQueuedEnable) lane.queued = null
      if (lane.active?.enabled) {
        clearTimer(lane.active)
        lane.active.terminal = true
        lane.active = null
        const state = read().motionCommand[side]
        update(side, {
          phase: state.phase === 'confirmed' ? 'idle' : state.phase === 'timeout' ? 'timeout' : 'failed',
          queuedEnabled: lane.queued,
          message: `${reason}，使能请求不再确认${lane.queued === false ? '；断使能请求仍待发送' : ''}`,
        })
      } else if (cancelledQueuedEnable) {
        update(side, {
          queuedEnabled: null,
          message: `${read().motionCommand[side].message}；${reason}，排队使能未发送`,
        })
      }
    }
  }

  function complete(side: ManualControlSide, request: Request) {
    if (lanes[side].active !== request || request.terminal || !request.accepted || !feedbackConfirms(request)) return
    request.terminal = true
    clearTimer(request)
    update(side, { phase: 'confirmed', message: `${request.enabled ? '使能' : '断使能'}请求已获反馈确认` })
  }

  function feedbackConfirms(request: Request) {
    return request.feedbackMatches && request.feedbackReceivedAt !== null
      && Date.now() - request.feedbackReceivedAt <= telemetryStaleAfterMs
  }

  function invalidate(reason: string) {
    for (const side of ['left', 'right'] as const) {
      const lane = lanes[side]
      clearTimer(lane.active)
      if (lane.active) lane.active.terminal = true
      lane.active = null
      const cancelledQueuedEnable = lane.queued === true
      if (cancelledQueuedEnable) lane.queued = null
      const state = read().motionCommand[side]
      if (state.phase === 'idle' && state.queuedEnabled === null) continue
      update(side, {
        phase: state.phase === 'confirmed' ? 'idle' : state.phase === 'timeout' ? 'timeout' : 'failed',
        queuedEnabled: lane.queued,
        message: `${reason}，状态未知${cancelledQueuedEnable ? '；排队使能未发送，需重新操作' : lane.queued === false ? '；断使能请求仍待发送' : ''}`,
      })
    }
  }

  function launch(side: ManualControlSide, enabled: boolean) {
    const lane = lanes[side]
    clearTimer(lane.active)
    if (lane.active) lane.active.terminal = true
    const request: Request = {
      id: ++nextId, enabled, afterSequence: receivedSequence,
      afterTimestamp: Math.max(lastReceivedTimestamp, read().frame.timestamp),
      feedbackMatches: false, feedbackReceivedAt: null, accepted: false, terminal: false, timer: null,
    }
    lane.active = request
    lane.inFlight = request
    lane.queued = null
    write(side, {
      phase: 'sending', requestId: request.id, targetEnabled: enabled, queuedEnabled: null,
      transportPending: true, message: `正在发送${enabled ? '使能' : '断使能'}请求`,
    })
    request.timer = setTimeout(() => {
      if (lane.active !== request || request.terminal) return
      request.terminal = true
      request.timer = null
      update(side, { phase: 'timeout', message: '未获得执行确认' })
    }, motionCommandTimeoutMs)

    void (async () => {
      try {
        const result = await send(side, enabled)
        if (result && typeof result === 'object' && 'ok' in result && result.ok === false) {
          const message = 'message' in result && typeof result.message === 'string' ? result.message : '后端拒绝请求'
          throw new Error(message)
        }
        if (lane.active !== request || request.terminal) return
        const blocker = request.enabled ? enableBlockReason() : null
        if (blocker) {
          blockEnabling(blocker)
          return
        }
        request.accepted = true
        if (feedbackConfirms(request)) complete(side, request)
        else update(side, { phase: 'waitingConfirm', message: `等待${enabled ? '使能' : '断使能'}反馈` })
      } catch (error) {
        // 传输失败不能证明服务端已取消；清除排队使能，保留已请求的断使能。
        const cancelledQueuedEnable = lane.queued === true
        if (cancelledQueuedEnable) lane.queued = null
        if (lane.active === request && !request.terminal) {
          request.terminal = true
          clearTimer(request)
          update(side, {
            phase: 'failed', queuedEnabled: lane.queued,
            message: `请求失败：${error instanceof Error ? error.message : String(error)}${cancelledQueuedEnable ? '；排队使能未发送，需重新操作' : ''}`,
          })
        } else if (cancelledQueuedEnable) {
          update(side, { queuedEnabled: null, message: `${read().motionCommand[side].message}；排队使能未发送，需重新操作` })
        }
      } finally {
        // 即使展示已经 timeout / 失效，也必须等旧 HTTP 真正结束才释放此侧通道。
        if (lane.inFlight === request) lane.inFlight = null
        if (read().motionCommand[side].requestId === request.id) update(side, { transportPending: false })
        const queued = lane.queued
        lane.queued = null
        if (queued !== null) {
          const blocker = queued ? enableBlockReason() : null
          if (!blocker) launch(side, queued)
          else {
            blockEnabling(blocker)
            update(side, { queuedEnabled: null, message: `${read().motionCommand[side].message}；${blocker}，排队使能未发送` })
          }
        }
      }
    })()
  }

  return {
    setEnabled(side: ManualControlSide, enabled: boolean) {
      const lane = lanes[side]
      const blocker = enabled ? enableBlockReason() : null
      if (blocker) {
        blockEnabling(blocker)
        update(side, {
          ...(!lane.active || lane.active.terminal ? { phase: 'failed' as const } : {}),
          message: `${blocker}，使能请求未发送`,
        })
        return
      }
      if (lane.inFlight) {
        // 仅合并仍有效的同向请求；已因急停/失联失效的旧 HTTP 不能吞掉重新操作。
        const sameActiveRequest = lane.active === lane.inFlight && !lane.inFlight.terminal
        lane.queued = enabled === lane.inFlight.enabled && sameActiveRequest ? null : enabled
        update(side, { queuedEnabled: lane.queued })
        return
      }
      if (lane.active && !lane.active.terminal && lane.active.enabled === enabled) return
      launch(side, enabled)
    },
    receiveFrame(frame: TelemetryFrame) {
      // 在接收时计数，包含尚未被 UI 节流提交的帧，避免旧缓存确认新请求。
      receivedSequence += 1
      lastReceivedTimestamp = frame.timestamp
      if (!frame.halOk || !frame.wsOk) {
        invalidate('HAL 或遥测不可用，未获得执行确认')
        return
      }
      const blocker = safetyBlockReason(frame)
      if (blocker) blockEnabling(blocker)
      for (const side of ['left', 'right'] as const) {
        const request = lanes[side].active
        if (!request || request.terminal) continue
        const axes = frame.motionAxisEnabled?.[side] ?? []
        request.feedbackMatches = receivedSequence > request.afterSequence && frame.timestamp > request.afterTimestamp
          && axes.length === 6 && axes.every((value) => value === request.enabled)
        request.feedbackReceivedAt = request.feedbackMatches ? Date.now() : null
        complete(side, request)
      }
    },
    invalidate,
    blockEnabling,
  }
}
