/*
 * 阅读导航 01｜入口与界面
 * 职责：按通道、级别和搜索词筛选日志，并使用虚拟列表控制渲染开销。
 * 先看：formatLogTime → matchesSearch → LogPanel。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { useVirtualizer } from '@tanstack/react-virtual'
import { Activity, ChevronDown, ChevronUp, Download, RotateCw, Search, TriangleAlert } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { channelColor, logChannels } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import type { LogLevel } from '../types'
import { UiButton, UiSpace, UiText } from './ui'

const levelOptions: Array<LogLevel | 'ALL'> = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR']
const quickFilters = [
  {
    label: 'Teleop',
    query: 'event=teleop_(axis_trace|status|origin_transition|mode|profile)',
    icon: <Activity size={13} />,
  },
  {
    label: 'Axis',
    query: 'event=teleop_axis_trace',
    icon: <Activity size={13} />,
  },
  {
    label: 'Roll',
    query: 'event=teleop_(axis_trace|status).*axis=Roll|axis=Roll.*event=teleop_(axis_trace|status)',
    icon: <RotateCw size={13} />,
  },
  {
    label: 'Motion err',
    query: 'updateRet=\\[[^\\]]*[1-9][^\\]]*\\]|clipped=\\[[^\\]]+:1\\]|clip=(?!-|""|\\s)',
    icon: <TriangleAlert size={13} />,
  },
]
/** 格式化对应数值用于界面展示。 */
function formatLogTime(ts: number) {
  const date = new Date(ts)
  return `${date.toLocaleTimeString()}.${String(date.getMilliseconds()).padStart(3, '0')}`
}
/** Match either a regex query or a plain keyword without breaking the panel. */
function matchesSearch(text: string, search: string) {
  if (!search.trim()) return true
  try {
    return new RegExp(search, 'i').test(text)
  } catch {
    return text.toLowerCase().includes(search.toLowerCase())
  }
}
/** 渲染当前界面单元，并连接所需数据。 */
export function LogPanel() {
  const logs = useTelemetryStore((state) => state.logs)
  const open = useTelemetryStore((state) => state.logPanelOpen)
  const setOpen = useTelemetryStore((state) => state.setLogPanelOpen)
  const [selectedChannels, setSelectedChannels] = useState<string[]>(logChannels)
  const [level, setLevel] = useState<LogLevel | 'ALL'>('ALL')
  const [search, setSearch] = useState('')
  const parentRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(
    () =>
      logs.filter((entry) => {
        const text = `${entry.channel} ${entry.level} ${entry.msg}`
        return selectedChannels.includes(entry.channel) && (level === 'ALL' || entry.level === level) && matchesSearch(text, search)
      }),
    [level, logs, search, selectedChannels],
  )

  // Virtualization keeps long diagnostic sessions usable in the fixed-height log panel.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 24,
    overscan: 20,
  })
  const virtualItems = virtualizer.getVirtualItems()
  const visibleItems = virtualItems.length > 0
    ? virtualItems
    : filtered.map((_, index) => ({ index, start: index * 24, key: index }))

 /** Export the currently filtered log view, not the full backing store. */
 function exportLogs() {
    const body = filtered.map((entry) => `${new Date(entry.ts).toISOString()} ${entry.channel} ${entry.level} ${entry.msg}`).join('\n')
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `appstation-m0-${Date.now()}.log`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  /** 处理对应的用户交互。 */
  function jumpNextError() {
    const index = filtered.findIndex((entry) => entry.level === 'ERROR')
    if (index >= 0) virtualizer.scrollToIndex(index, { align: 'center' })
  }

  return (
    <section className={`log-panel ${open ? 'log-panel-open' : 'log-panel-closed'}`}>
      <header className="log-toolbar">
        <UiSpace size={8} wrap>
          <UiButton icon={open ? <ChevronDown size={14} /> : <ChevronUp size={14} />} onClick={() => setOpen(!open)}>
            Log Panel
          </UiButton>
          {open && (
            <>
              <span className="log-channel-filters">
                {logChannels.map((channel) => (
                  <label key={channel}>
                    <input
                      type="checkbox"
                      checked={selectedChannels.includes(channel)}
                      onChange={(event) => {
                        setSelectedChannels((current) =>
                          event.target.checked ? [...current, channel] : current.filter((item) => item !== channel),
                        )
                      }}
                    />
                    {channel}
                  </label>
                ))}
              </span>
              <select
                className="ui-select"
                value={level}
                onChange={(event) => setLevel(event.target.value as LogLevel | 'ALL')}
              >
                {levelOptions.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
              <UiSpace size={4} className="log-quick-filters">
                {quickFilters.map((item) => (
                  <UiButton
                    key={item.label}
                    variant={search === item.query ? 'primary' : 'default'}
                    icon={item.icon}
                    onClick={() => setSearch(item.query)}
                  >
                    {item.label}
                  </UiButton>
                ))}
              </UiSpace>
              <span className="log-search">
                <Search size={14} />
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="regex / keyword" />
              </span>
            </>
          )}
        </UiSpace>
        {open && (
          <UiSpace>
            <UiButton icon={<TriangleAlert size={14} />} onClick={jumpNextError}>
              下一个错误
            </UiButton>
            <UiButton icon={<Download size={14} />} onClick={exportLogs}>
              导出
            </UiButton>
            <UiText secondary>{filtered.length} / {logs.length}</UiText>
          </UiSpace>
        )}
      </header>
      {open && (
        <div ref={parentRef} className="log-viewport">
          <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
            {visibleItems.map((virtualItem) => {
              const entry = filtered[virtualItem.index]
              return (
                <div
                  className={`log-line log-line-${entry.level.toLowerCase()}`}
                  key={entry.id}
                  style={{ transform: `translateY(${virtualItem.start}px)` }}
                >
                  <span>{formatLogTime(entry.ts)}</span>
                  <b style={{ color: channelColor[entry.channel] }}>{entry.channel}</b>
                  <em>{entry.level}</em>
                  <span className="log-message" title={entry.msg}>{entry.msg}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
