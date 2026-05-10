import { Tag, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import type { ConnectionState } from '../types'

const colorByState: Record<ConnectionState, string> = {
  ok: 'success',
  warn: 'warning',
  error: 'error',
  checking: 'processing',
  pending: 'default',
}

interface MetricPillProps {
  state: ConnectionState
  label: ReactNode
  tip?: string
}

export function MetricPill({ state, label, tip }: MetricPillProps) {
  const tag = (
    <Tag className="metric-pill" color={colorByState[state]}>
      <span className={`status-dot status-dot-${state}`} />
      {label}
    </Tag>
  )
  return tip ? <Tooltip title={tip}>{tag}</Tooltip> : tag
}
