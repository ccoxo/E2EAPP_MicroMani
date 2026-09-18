/*
 * 阅读导航 02｜前端契约与状态
 * 职责：综合诊断与逻辑连接状态，生成单手或双手遥操作的显示状态。
 * 先看：omegaDiagnosticState → handForSide → logicalDisconnected → teleopHandState。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import type { ConnectionState, DiagnosticItem, Omega7Telemetry, TelemetryFrame } from './types'

const sides = ['left', 'right'] as const

function omegaDiagnosticState(diagnostics: DiagnosticItem[]): ConnectionState {
  return diagnostics.find((item) => item.key === 'omega7')?.status ?? 'pending'
}

function handForSide(frame: TelemetryFrame, side: (typeof sides)[number]) {
  return frame.teleopHands.find((hand) => hand.side === side)
}

function logicalDisconnected(hand: Omega7Telemetry) {
  return hand.message.toLowerCase().includes('logical')
}

export function teleopHandState(
  frame: TelemetryFrame,
  diagnostics: DiagnosticItem[],
  side: (typeof sides)[number],
): ConnectionState {
  const diagnostic = omegaDiagnosticState(diagnostics)
  if (diagnostic !== 'ok') return diagnostic

  const hand = handForSide(frame, side)
  if (!hand) return 'pending'
  if (!hand.connected) return logicalDisconnected(hand) ? 'pending' : 'error'
  if (!hand.lastReadOk) return 'warn'
  return 'ok'
}

export function teleopHandValue(frame: TelemetryFrame, side: (typeof sides)[number]) {
  const hand = handForSide(frame, side)
  if (!hand) return '无遥测'
  if (!hand.connected) return logicalDisconnected(hand) ? '逻辑断开' : '物理离线'
  if (!hand.lastReadOk) return '读数待恢复'
  return side === 'left' ? '左 Omega.7' : '右 Omega.7'
}

export function teleopPairState(frame: TelemetryFrame, diagnostics: DiagnosticItem[]): ConnectionState {
  const handStates = sides.map((side) => teleopHandState(frame, diagnostics, side))
  if (handStates.includes('error')) return 'error'
  if (handStates.includes('warn')) return 'warn'
  if (handStates.includes('checking')) return 'checking'
  if (handStates.includes('pending')) return 'pending'
  return 'ok'
}

export function teleopPairValue(frame: TelemetryFrame) {
  const ready = frame.teleopHands.filter((hand) => hand.connected && hand.lastReadOk).length
  return ready === 2 ? '读数 2/2' : `读数 ${ready}/2`
}
