import { Button, Space, Tag, Typography } from 'antd'
import { AlertTriangle, CheckCircle2, CircleDashed, HelpCircle, Loader2, XCircle } from 'lucide-react'
import type { ConnectionState } from '../../types'

export interface ModuleStatus {
  key: string
  label: string
  state: ConnectionState
  primary: string
  secondary?: string
  metric?: string
  group?: string
  actionLabel?: string
  onAction?: () => void
}

const iconByState = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
  checking: Loader2,
  pending: CircleDashed,
} satisfies Record<ConnectionState, typeof CheckCircle2>

const stateText = {
  ok: '正常',
  warn: '注意',
  error: '故障',
  checking: '检查中',
  pending: '待确认',
} satisfies Record<ConnectionState, string>

export function ModuleStatusGrid({ modules, compact = false }: { modules: ModuleStatus[]; compact?: boolean }) {
  return (
    <div className={`module-status-grid ${compact ? 'module-status-grid-compact' : ''}`}>
      {modules.map((item) => {
        const Icon = iconByState[item.state]
        return (
          <article className={`module-status-card module-status-${item.state}`} key={item.key}>
            <div className="module-status-card-head">
              <span className="module-status-title">
                <Icon size={16} />
                {item.label}
              </span>
              <Tag>{stateText[item.state]}</Tag>
            </div>
            <Typography.Text strong>{item.primary}</Typography.Text>
            {item.secondary && <Typography.Text type="secondary">{item.secondary}</Typography.Text>}
            <Space className="module-status-footer" size={6}>
              {item.group && <Tag color="default">{item.group}</Tag>}
              {item.metric && <Tag color={item.state === 'warn' || item.state === 'error' ? 'warning' : 'processing'}>{item.metric}</Tag>}
              {item.actionLabel && item.onAction && (
                <Button size="small" type="text" icon={<HelpCircle size={13} />} onClick={item.onAction}>
                  {item.actionLabel}
                </Button>
              )}
            </Space>
          </article>
        )
      })}
    </div>
  )
}
