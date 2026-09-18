import { RefreshCw, Trash2 } from 'lucide-react'
import { UiButton } from '../../components/ui'

export interface SnapshotMenuData {
  items: { key: string; label: string; disabled?: boolean }[]
  onClick: (info: { key: string }) => void
  onDelete: (key: string) => void
}

/** 恢复与删除使用独立按钮，删除不会同时触发应用快照。 */
export function ParameterSnapshotMenu({ title, menu }: { title: string; menu: SnapshotMenuData }) {
  return (
    <details className="ui-dropdown">
      <summary className="ui-btn"><RefreshCw size={15} />{title}</summary>
      <div className="ui-dropdown-panel">
        {menu.items.length === 0 ? <UiButton disabled>暂无快照</UiButton> : menu.items.map((item) => (
          <div className="snapshot-menu-label" key={item.key}>
            <button type="button" disabled={item.disabled} style={{ flex: 1 }} onClick={() => menu.onClick({ key: item.key })}>
              {item.label}
            </button>
            <UiButton
              aria-label={`删除 ${item.label}`}
              danger
              style={{ width: 'auto', flexShrink: 0 }}
              icon={<Trash2 size={13} />}
              onClick={() => menu.onDelete(item.key)}
            />
          </div>
        ))}
      </div>
    </details>
  )
}
