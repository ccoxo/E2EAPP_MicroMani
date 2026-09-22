/*
 * 阅读导航 02｜前端契约与状态
 * 职责：拆分夹爪「请求启停 / 命令进度 / 反馈健康」三套展示语义，禁止把反馈正常写成已使能。
 * 先看：gripperRequestedEnabled → gripperFeedbackHealth → gripperCommandProgressLabel。
 */
import type {
  AppConfig,
  ManualControlSide,
  ManualGripperCommand,
  TelemetryFrame,
  TelemetryLinkStatus,
} from './types'
import { gripperFeedbackIsQueued, telemetryLinkIsLive } from './hardwareStatus'

/** 操作者请求的启停标志；来自配置，后端在非真实硬件分支用作命令门闩并持久化。 */
export type GripperRequestState = 'enabled' | 'disabled'

/** 命令进度：与设备反馈、请求标志都分开。 */
export type GripperCommandPhase = 'idle' | 'sending' | 'accepted' | 'failed'

/** 反馈健康：由控制器运行与逐侧 ok 结合 live 遥测解释，不能等同使能。 */
export type GripperFeedbackHealth = 'ok' | 'error' | 'pending' | 'unknown'

export interface GripperCommandProgress {
  phase: GripperCommandPhase
  command: ManualGripperCommand | null
  requestId: number
  message: string
}

export function initialGripperCommandProgress(): GripperCommandProgress {
  return { phase: 'idle', command: null, requestId: 0, message: '' }
}

export function gripperRequestedEnabled(config: AppConfig, side: ManualControlSide) {
  return Boolean(side === 'left' ? config.gripper.leftEnabled : config.gripper.rightEnabled)
}

export function gripperRequestLabel(requested: GripperRequestState) {
  return requested === 'enabled' ? '已请求使能' : '已请求断使能'
}

export function gripperRequestTone(requested: GripperRequestState) {
  return requested === 'enabled' ? ('success' as const) : ('warning' as const)
}

/**
 * 逐侧反馈健康。
 * ok===true 仅表示最近命令/反馈详情正常；必须结合控制器 running 与 live 遥测，
 * 历史成功不能持续展示为当前健康，更不能写成「已使能」。
 */
export function gripperFeedbackHealth(
  frame: TelemetryFrame,
  link: TelemetryLinkStatus,
  side: ManualControlSide,
): GripperFeedbackHealth {
  if (!telemetryLinkIsLive(link) || !frame.wsOk || !frame.halOk) return 'unknown'
  const status = frame.gripperStatus
  if (!status) return 'unknown'
  const sideStatus = status.sides?.[side]
  if (!sideStatus) return 'unknown'
  if (sideStatus.ok === false) return 'error'
  if (sideStatus.ok === true) {
    if (gripperFeedbackIsQueued(sideStatus)) return 'pending'
    // running 表示原生控制器在跑；未 running 时的 ok 只能算历史/待确认。
    return status.running === true ? 'ok' : 'pending'
  }
  return 'pending'
}

export function gripperFeedbackLabel(health: GripperFeedbackHealth) {
  if (health === 'ok') return '反馈正常'
  if (health === 'error') return '反馈异常'
  if (health === 'pending') return '反馈待确认'
  return '反馈未知'
}

export function gripperFeedbackTone(health: GripperFeedbackHealth) {
  if (health === 'ok') return 'success' as const
  if (health === 'error') return 'error' as const
  if (health === 'pending') return 'warning' as const
  return 'muted' as const
}

export function gripperCommandProgressLabel(progress: GripperCommandProgress) {
  if (progress.phase === 'sending') {
    const cmd = progress.command === 'enable' ? '使能' : progress.command === 'disable' ? '断使能' : progress.command === 'open' ? '打开' : progress.command === 'close' ? '闭合' : progress.command === 'home' ? '回零' : progress.command === 'target' ? '目标' : progress.command === 'stop' ? '停止' : '命令'
    return `正在发送${cmd}`
  }
  if (progress.phase === 'accepted') return progress.message || '命令已接受'
  if (progress.phase === 'failed') return progress.message || '命令失败'
  return ''
}

export function gripperCommandTone(phase: GripperCommandPhase) {
  if (phase === 'sending') return 'processing' as const
  if (phase === 'accepted') return 'success' as const
  if (phase === 'failed') return 'error' as const
  return 'muted' as const
}
