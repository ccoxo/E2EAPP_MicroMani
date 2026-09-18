/*
 * 阅读导航 01｜入口与界面
 * 职责：在确认操作前对比参数或状态的前后变化，显示各项差异与风险提示。
 * 先看：ActionCompareItem → ActionCompareModalProps → toneIcon → toneType。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { UiButton, UiText } from './ui'

export interface ActionCompareItem {
  label: string
  value: ReactNode
  hint?: ReactNode
}

interface ActionCompareModalProps {
  open: boolean
  title: string
  tone?: 'default' | 'warning' | 'danger'
  impact: ReactNode
  expected?: ReactNode
  current: ActionCompareItem[]
  proposed: ActionCompareItem[]
  confirmText: string
  confirmLoading?: boolean
  onConfirm: () => void
  onCancel: () => void
}
/** 描述当前方法的功能边界。 */
function toneIcon(tone: ActionCompareModalProps['tone']) {
  if (tone === 'danger') return <ShieldAlert size={18} />
  if (tone === 'warning') return <AlertTriangle size={18} />
  return <CheckCircle2 size={18} />
}
/** 描述当前方法的功能边界。 */
function toneType(tone: ActionCompareModalProps['tone']) {
  if (tone === 'danger') return 'error'
  if (tone === 'warning') return 'warning'
  return 'info'
}
/** 渲染当前界面单元，并连接所需数据。 */
function CompareColumn({ title, items }: { title: string; items: ActionCompareItem[] }) {
  return (
    <div className="action-compare-column">
      <UiText strong>{title}</UiText>
      <div className="action-compare-list">
        {items.map((item) => (
          <span className="action-compare-row" key={item.label}>
            <small>{item.label}</small>
            <b>{item.value}</b>
            {item.hint && <em>{item.hint}</em>}
          </span>
        ))}
      </div>
    </div>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export function ActionCompareModal({
  open,
  title,
  tone = 'default',
  impact,
  expected,
  current,
  proposed,
  confirmText,
  confirmLoading,
  onConfirm,
  onCancel,
}: ActionCompareModalProps) {
  if (!open) return null
  return (
    <div className="ui-modal-mask" role="presentation" onClick={onCancel}>
      <div
        className="ui-modal"
        style={{ width: 'min(620px, calc(100vw - 32px))' }}
        role="dialog"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="ui-modal-head">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            {toneIcon(tone)}
            <strong>{title}</strong>
          </span>
        </header>
        <div className="ui-modal-body">
          <div className={`ui-alert ui-alert-${toneType(tone)}`}>{impact}</div>
          <div className="action-compare-grid">
            <CompareColumn title="当前" items={current} />
            <CompareColumn title="将应用" items={proposed} />
          </div>
          {expected && (
            <UiText secondary className="action-compare-expected">
              {expected}
            </UiText>
          )}
        </div>
        <div className="ui-modal-actions">
          <UiButton onClick={onCancel}>取消</UiButton>
          <UiButton
            danger={tone === 'danger'}
            loading={confirmLoading}
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={onConfirm}
          >
            {confirmText}
          </UiButton>
        </div>
      </div>
    </div>
  )
}
