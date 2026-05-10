import { useVirtualizer } from '@tanstack/react-virtual'
import { Button, Checkbox, Input, Select, Space, Typography } from 'antd'
import { ChevronDown, ChevronUp, Download, Search, TriangleAlert } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { channelColor, logChannels } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import type { LogLevel } from '../types'

const levelOptions: Array<LogLevel | 'ALL'> = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR']

function matchesSearch(text: string, search: string) {
  if (!search.trim()) return true
  try {
    return new RegExp(search, 'i').test(text)
  } catch {
    return text.toLowerCase().includes(search.toLowerCase())
  }
}

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

  // TanStack Virtual is intentionally used here; the returned instance is not passed to memoized children.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 24,
    overscan: 20,
  })

  function exportLogs() {
    const body = filtered.map((entry) => `${new Date(entry.ts).toISOString()} ${entry.channel} ${entry.level} ${entry.msg}`).join('\n')
    const url = URL.createObjectURL(new Blob([body], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `appstation-m0-${Date.now()}.log`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  function jumpNextError() {
    const index = filtered.findIndex((entry) => entry.level === 'ERROR')
    if (index >= 0) virtualizer.scrollToIndex(index, { align: 'center' })
  }

  return (
    <section className={`log-panel ${open ? 'log-panel-open' : 'log-panel-closed'}`}>
      <header className="log-toolbar">
        <Space size={8} wrap>
          <Button size="small" icon={open ? <ChevronDown size={14} /> : <ChevronUp size={14} />} onClick={() => setOpen(!open)}>
            Log Panel
          </Button>
          {open && (
            <>
              <Checkbox.Group options={logChannels} value={selectedChannels} onChange={(value) => setSelectedChannels(value.map(String))} />
              <Select size="small" value={level} options={levelOptions.map((item) => ({ value: item, label: item }))} onChange={setLevel} />
              <Input size="small" prefix={<Search size={14} />} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="regex / keyword" />
            </>
          )}
        </Space>
        {open && (
          <Space>
            <Button size="small" icon={<TriangleAlert size={14} />} onClick={jumpNextError}>
              下一个错误
            </Button>
            <Button size="small" icon={<Download size={14} />} onClick={exportLogs}>
              导出
            </Button>
            <Typography.Text type="secondary">{filtered.length} / {logs.length}</Typography.Text>
          </Space>
        )}
      </header>
      {open && (
        <div ref={parentRef} className="log-viewport">
          <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
            {virtualizer.getVirtualItems().map((virtualItem) => {
              const entry = filtered[virtualItem.index]
              return (
                <div
                  className={`log-line log-line-${entry.level.toLowerCase()}`}
                  key={entry.id}
                  style={{ transform: `translateY(${virtualItem.start}px)` }}
                >
                  <span>{new Date(entry.ts).toLocaleTimeString()}</span>
                  <b style={{ color: channelColor[entry.channel] }}>{entry.channel}</b>
                  <em>{entry.level}</em>
                  <span>{entry.msg}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
