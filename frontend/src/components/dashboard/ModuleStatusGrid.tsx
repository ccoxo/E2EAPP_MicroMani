/*
 * 阅读导航 01｜入口与界面
 * 职责：将各模块运行状态组织为概览网格。
 * 先看：ModuleStatus → ModuleStatusGrid。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { UiButton, UiSpace, UiTag, UiText } from '../ui'
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
/** 渲染当前界面单元，并连接所需数据。 */
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
              <UiTag>{stateText[item.state]}</UiTag>
            </div>
            <UiText strong>{item.primary}</UiText>
            {item.secondary && <UiText secondary>{item.secondary}</UiText>}
            <UiSpace className="module-status-footer" size={6} wrap>
              {item.group && <UiTag tone="default">{item.group}</UiTag>}
              {item.metric && <UiTag tone={item.state === 'warn' || item.state === 'error' ? 'warning' : 'processing'}>{item.metric}</UiTag>}
              {item.actionLabel && item.onAction && (
                <UiButton variant="text" icon={<HelpCircle size={13} />} onClick={item.onAction}>
                  {item.actionLabel}
                </UiButton>
              )}
            </UiSpace>
          </article>
        )
      })}
    </div>
  )
}
