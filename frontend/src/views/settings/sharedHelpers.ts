import type { ConnectionState, LogEntry } from '../../types'

export function stateTone(state: ConnectionState) {
  if (state === 'ok') return 'success'
  if (state === 'warn') return 'warning'
  if (state === 'error') return 'error'
  if (state === 'checking') return 'processing'
  return 'default'
}

export function stateText(state: ConnectionState) {
  if (state === 'ok') return '正常'
  if (state === 'warn') return '注意'
  if (state === 'error') return '错误'
  if (state === 'checking') return '检查中'
  return '待确认'
}

export function formatGripperPosition(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} mm` : '不可用'
}

export function safeGripperPosition(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
}

export function commandLog(
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void,
  channel: LogEntry['channel'],
  msg: string,
) {
  injectLog('INFO', msg, channel)
}
