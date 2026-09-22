import { motionTelemetryIsLive } from '../stores/motionCommands'
import type { TelemetryFrame, TelemetryLinkStatus } from '../types'
import { controlLeaseBlockReason, type ControlLeaseState } from '../stores/controlLease'

export interface ControlSafetyState {
  generation: number
  emergencyRequested: boolean
  emergencyPending: boolean
  emergencyError: string | null
  acknowledging: boolean
  acknowledgeAfterFrame: number | null
}

export const initialControlSafety = (): ControlSafetyState => ({
  generation: 0, emergencyRequested: false, emergencyPending: false,
  emergencyError: null, acknowledging: false, acknowledgeAfterFrame: null,
})

interface SafetySnapshot {
  frame: TelemetryFrame
  telemetryLink: TelemetryLinkStatus
  controlSafety: ControlSafetyState
  controlLease: ControlLeaseState
}

/** 急停意图独立于显示数值；只能通过显式确认及后续安全反馈释放。 */
export function controlSafetyBlockReason(state: SafetySnapshot, requireLive = true): string | null {
  if (state.controlSafety.emergencyRequested || state.controlSafety.acknowledging) return '急停保护尚未确认解除'
  if (state.frame.forceStatus?.safety?.latched) return state.frame.forceStatus.safety.reason || '硬件安全联锁已锁存'
  const leaseReason = controlLeaseBlockReason(state.controlLease)
  if (leaseReason) return leaseReason
  if (requireLive && !motionTelemetryIsLive(state.frame, state.telemetryLink)) return '缺少新鲜的硬件遥测，禁止启动运动'
  return null
}

export function canAcknowledgeControlSafety(state: SafetySnapshot): boolean {
  if (state.controlSafety.emergencyPending || state.controlSafety.acknowledging) return false
  if (controlLeaseBlockReason(state.controlLease)) return false
  if (!motionTelemetryIsLive(state.frame, state.telemetryLink)) return false
  if (state.frame.forceStatus?.source === 'hkvl_serial'
    && state.frame.forceStatus.safety?.canAcknowledge !== true) return false
  // 无锁存时仍可请求 HAL 核验，恢复网络后才能解除一次发送失败的本地急停意图。
  return state.frame.forceStatus?.safety?.latched
    ? state.frame.forceStatus.safety.canAcknowledge === true
    : state.controlSafety.emergencyRequested
}

/** 无运动的 HKVL 自检允许已锁存，仍要求有效租约和新鲜反馈。 */
export function forceSelfCheckBlockReason(state: SafetySnapshot, requireLive = true): string | null {
  if (state.controlSafety.emergencyPending || state.controlSafety.acknowledging) return '等待当前安全操作完成'
  if (state.controlSafety.emergencyError) return '急停尚未确认，请先重新急停并检查反馈'
  const leaseReason = controlLeaseBlockReason(state.controlLease)
  if (leaseReason) return leaseReason
  if (requireLive && !motionTelemetryIsLive(state.frame, state.telemetryLink)) return '缺少新鲜的硬件遥测，无法执行自检'
  if (state.frame.recording) return '请先停止录制再执行力觉自检'
  return null
}
