import type { LogEntry } from '../types'

export interface LogRow {
  entry: LogEntry
  count: number
  firstTs: number
  key: string
}

/** 同时区分前端与后端的独立编号，并识别重连时重放的同一条日志。 */
function logIdentity(entry: LogEntry): string {
  return JSON.stringify([entry.id, entry.ts, entry.channel, entry.level, entry.msg])
}

/** 读取现有诊断日志的 key=value 字段，保留引号内的空格和转义。 */
function diagnosticFields(message: string): Map<string, string[]> {
  const fields = new Map<string, string[]>()
  const pattern = /(?:^|\s)([A-Za-z]\w*)=("(?:\\.|[^"\\])*"|\[[^\]]*\]|[^\s]*)/g
  for (const match of message.matchAll(pattern)) {
    let value = match[2]
    if (value.startsWith('"')) {
      try { value = JSON.parse(value) as string } catch { /* 未知格式按原文保留。 */ }
    }
    fields.set(match[1], [...(fields.get(match[1]) ?? []), value])
  }
  return fields
}

function hasDetail(value: string | undefined): boolean {
  return value !== undefined && !['', '-', 'null'].includes(value.trim())
}

/** 诊断数组只在明确全零时视为正常，未知值仍留在运行事件中。 */
function hasNonzeroAxisValue(value: string | undefined): boolean {
  if (!hasDetail(value)) return false
  const text = value!.trim()
  const values = text.startsWith('[') && text.endsWith(']') ? text.slice(1, -1).split(',') : [text]
  return values.some((item) => {
    const number = Number(item.slice(item.lastIndexOf(':') + 1).trim())
    return !Number.isFinite(number) || number !== 0
  })
}

/** 仅收起周期诊断；异常诊断、操作转换及所有告警/错误始终保留。 */
export function isDiagnosticLog(entry: LogEntry): boolean {
  if (entry.level === 'WARNING' || entry.level === 'ERROR') return false
  if (entry.level === 'DEBUG') return true
  if (entry.channel === '[BACKEND]') return /^Frame \d+ OK, ws=\d+(?:\.\d+)?Hz$/.test(entry.msg)
  if (entry.channel !== '[HAL]') return false

  const fields = diagnosticFields(entry.msg)
  const event = fields.get('event')?.[0]
  const periodic = event === 'teleop_status' || event === 'teleop_axis_trace'
    || /^(?:teleop diag|native status) /.test(entry.msg)
  if (!periodic) return false

  return !['lastError', 'blockReason', 'block'].some(key => fields.get(key)?.some(hasDetail))
    && !fields.get('clip')?.some(value => value.startsWith('[') ? hasNonzeroAxisValue(value) : hasDetail(value))
    && !['clipped', 'updateRet', 'stopReason'].some(key => fields.get(key)?.some(hasNonzeroAxisValue))
    && !/\b(?:left|right):ERR(?:\s|;|$)/.test(entry.msg)
}

/** 只删除同一条日志的重放，时间或内容不同的重复事件仍完整保留。 */
export function deduplicateLogEntries(entries: readonly LogEntry[]): LogEntry[] {
  const seen = new Set<string>()
  return entries.filter((entry) => {
    const key = logIdentity(entry)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/** 折叠相邻的同内容事件用于展示，原始日志与导出内容不受影响。 */
export function buildLogRows(entries: readonly LogEntry[]): LogRow[] {
  const rows: LogRow[] = []
  for (const entry of deduplicateLogEntries(entries)) {
    const previous = rows.at(-1)
    const elapsed = previous ? entry.ts - previous.entry.ts : -1
    if (previous && elapsed >= 0 && elapsed <= 30_000
      && previous.entry.channel === entry.channel && previous.entry.level === entry.level
      && previous.entry.msg === entry.msg) {
      previous.entry = entry
      previous.count += 1
    } else {
      rows.push({ entry, count: 1, firstTs: entry.ts, key: logIdentity(entry) })
    }
  }
  return rows
}
