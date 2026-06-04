import { Alert, Button, Modal, Space, Typography } from 'antd'
import { AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'

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
      <Typography.Text strong>{title}</Typography.Text>
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
  return (
    <Modal
      title={
        <Space size={8}>
          {toneIcon(tone)}
          <span>{title}</span>
        </Space>
      }
      open={open}
      onCancel={onCancel}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          取消
        </Button>,
        <Button key="confirm" danger={tone === 'danger'} loading={confirmLoading} type="primary" onClick={onConfirm}>
          {confirmText}
        </Button>,
      ]}
      width={620}
    >
      <Alert className="action-compare-impact" type={toneType(tone)} message={impact} showIcon={false} />
      <div className="action-compare-grid">
        <CompareColumn title="当前" items={current} />
        <CompareColumn title="将应用" items={proposed} />
      </div>
      {expected && (
        <Typography.Paragraph className="action-compare-expected" type="secondary">
          {expected}
        </Typography.Paragraph>
      )}
    </Modal>
  )
}
