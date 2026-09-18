/*
 * 阅读导航 02｜前端契约与状态
 * 职责：录制业务态展示派生。UI 以 phase 为业务真相；frame.recording 仅为后端事实同步信号。
 * 先看：recordPhaseLabel → recordSessionBusy → recordUiIsRecording。
 */
import type { RecordSessionState } from './types'

export type RecordPhase = RecordSessionState['phase']

/** 业务上是否处于「录制中」；UI 状态条、主页指标统一用此派生，不直接读 frame.recording。 */
export function recordUiIsRecording(phase: RecordPhase) {
  return phase === 'recording'
}

/** 是否处于忙态（不可开始新会话等）。 */
export function recordSessionBusy(phase: RecordPhase) {
  return phase === 'starting' || phase === 'saving' || phase === 'finishing'
}

export function recordPhaseLabel(phase: RecordPhase) {
  switch (phase) {
    case 'idle': return '就绪'
    case 'starting': return '启动中'
    case 'recording': return '录制中'
    case 'reviewing': return '质检中'
    case 'resetting': return '复位中'
    case 'saving': return '保存中'
    case 'finishing': return '结束中'
    default: return phase
  }
}

/** 后端 frame.recording 与 UI phase 的一致性说明：
 * commitBackendFrame 会在同一次 set 中同步顶层 recording；
 * phase 由业务流程推进。重连后以 fetchRecordStatus + phase 为准，
 * 不因 frame.recording 短暂 false 就把 UI 从 recording 拉回 idle。
 */
export function recordDisplayConsistent(phase: RecordPhase, frameRecording: boolean) {
  if (recordUiIsRecording(phase)) return true
  return !frameRecording
}
