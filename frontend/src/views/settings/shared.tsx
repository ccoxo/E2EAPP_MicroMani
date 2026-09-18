import type { ReactNode } from 'react'
import type { ConnectionState, LogEntry } from '../../types'
import { UiSpace, UiTag, UiText, UiTitle } from '../../components/ui'

export interface GripperPortHint {
  side?: string
  port?: string
  slaveId?: number
  baudrate?: number
  ok?: boolean | null
  message?: string
}

export type InlineStatusTone = 'ok' | 'warn' | 'error' | 'pending'

export interface ActionCompareItem {
  label: string
  value: ReactNode
}

export interface PendingComparison {
  title: string
  tone?: 'default' | 'warning' | 'danger'
  impact: ReactNode
  expected?: ReactNode
  current: ActionCompareItem[]
  proposed: ActionCompareItem[]
  confirmText: string
  onConfirm: () => boolean | void | Promise<boolean | void>
}

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

export function HardwareConfigCard({
  id,
  focusHash,
  icon,
  title,
  subtitle,
  state,
  badges,
  actions,
  children,
  wide,
}: {
  id: string
  focusHash: string
  icon: ReactNode
  title: string
  subtitle: string
  state: ConnectionState
  badges?: ReactNode
  actions?: ReactNode
  children: ReactNode
  wide?: boolean
}) {
  const focused = focusHash === id
  return (
    <article id={id} className={`hardware-config-card hardware-config-card-state-${state} ${wide ? 'hardware-config-card-wide' : ''} ${focused ? 'hardware-config-card-focused' : ''}`}>
      <div className="hardware-config-card-head">
        <div className="hardware-config-title">
          <span className="hardware-config-icon">{icon}</span>
          <div>
            <UiTitle level={3}>{title}</UiTitle>
            <UiText secondary>{subtitle}</UiText>
          </div>
        </div>
        <UiSpace wrap>
          {badges}
          <UiTag tone={stateTone(state)}>{stateText(state)}</UiTag>
        </UiSpace>
      </div>
      {actions && <div className="hardware-config-actions">{actions}</div>}
      {children}
    </article>
  )
}

export function MetricBox({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'warn' | 'ok' | 'neutral'
}) {
  return (
    <span className={`hardware-metric-box hardware-metric-${tone ?? 'neutral'}`}>
      <small>{label}</small>
      <b>{value}</b>
      {hint ? <em>{hint}</em> : null}
    </span>
  )
}
