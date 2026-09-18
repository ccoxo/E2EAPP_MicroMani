/*
 * 阅读导航 01｜入口与界面
 * 职责：按通道、级别和搜索词筛选日志，并使用虚拟列表控制渲染开销。
 * 先看：formatLogTime → matchesSearch → LogPanel。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { useVirtualizer } from '@tanstack/react-virtual'
import { Activity, ArrowDownToLine, ChevronDown, ChevronUp, Download, Maximize2, Minimize2, RotateCw, Search, SlidersHorizontal, TriangleAlert } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { channelColor, logChannels } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import type { LogLevel } from '../types'
import { buildLogRows, deduplicateLogEntries, isDiagnosticLog } from '../utils/logPresentation'
import { UiButton, UiText } from './ui'

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
  const [includeDiagnostics, setIncludeDiagnostics] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [followLatest, setFollowLatest] = useState(true)
  const [height, setHeight] = useState(260)
  const [maxHeight, setMaxHeight] = useState(520)
  const [maximized, setMaximized] = useState(false)
  const [resizing, setResizing] = useState(false)
  const panelRef = useRef<HTMLElement>(null)
  const parentRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ y: number; height: number } | null>(null)
  const errorCursor = useRef<string | null>(null)
  const minHeight = Math.min(180, maxHeight)
  const panelHeight = maximized ? maxHeight : Math.min(maxHeight, Math.max(minHeight, height))

  // 抽屉上限为悬浮急停和主导航留出空间；不依赖固定的状态栏高度。
  useLayoutEffect(() => {
    const shell = panelRef.current?.closest('.app-shell')
    const top = shell?.querySelector('.top-bar')
    const status = shell?.querySelector('.status-bar')
    const dock = shell?.querySelector('.safety-dock')
    const nav = shell?.querySelector('.left-nav')
    const measure = () => {
      const viewportHeight = shell?.getBoundingClientRect().height || window.innerHeight
      const navigationHeight = nav && getComputedStyle(nav).flexDirection === 'row' ? nav.getBoundingClientRect().height : 0
      const reserved = (top?.getBoundingClientRect().height ?? 0) + (status?.getBoundingClientRect().height ?? 0)
        + navigationHeight + Math.max(160, (dock?.getBoundingClientRect().height ?? 0) + 24)
      setMaxHeight(Math.max(120, Math.floor(Math.min(viewportHeight * 0.68, viewportHeight - reserved))))
    }
    measure()
    const observer = new ResizeObserver(measure)
    for (const element of [shell, top, status, dock, nav]) if (element) observer.observe(element)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])

  const uniqueLogs = useMemo(() => deduplicateLogEntries(logs), [logs])
  const diagnosticCount = useMemo(() => uniqueLogs.filter(isDiagnosticLog).length, [uniqueLogs])

  const filtered = useMemo(
    () =>
      uniqueLogs.filter((entry) => {
        const text = `${entry.channel} ${entry.level} ${entry.msg}`
        return (includeDiagnostics || level === 'DEBUG' || !isDiagnosticLog(entry))
          && selectedChannels.includes(entry.channel) && (level === 'ALL' || entry.level === level) && matchesSearch(text, search)
      }),
    [includeDiagnostics, level, uniqueLogs, search, selectedChannels],
  )
  const rows = useMemo(() => buildLogRows(filtered), [filtered])

  // Virtualization keeps long diagnostic sessions usable in the fixed-height log panel.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 24,
    overscan: 20,
  })
  const virtualItems = virtualizer.getVirtualItems()
  const visibleItems = virtualItems.length > 0
    ? virtualItems
    : rows.slice(0, Math.ceil(panelHeight / 24)).map((row, index) => ({ index, start: index * 24, key: row.key }))

  useEffect(() => {
    if (!open || !followLatest) return
    const id = requestAnimationFrame(() => {
      const viewport = parentRef.current
      if (viewport) viewport.scrollTop = viewport.scrollHeight
    })
    return () => cancelAnimationFrame(id)
  }, [open, followLatest, rows, panelHeight])

  function startResize(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { y: event.clientY, height: panelHeight }
    setMaximized(false)
    setResizing(true)
  }

  function resize(event: PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return
    setHeight(Math.min(maxHeight, Math.max(minHeight, dragRef.current.height + dragRef.current.y - event.clientY)))
  }

  function stopResize() {
    dragRef.current = null
    setResizing(false)
  }

  function resizeWithKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    const next = event.key === 'ArrowUp' ? panelHeight + 32 : event.key === 'ArrowDown' ? panelHeight - 32
      : event.key === 'Home' ? minHeight : event.key === 'End' ? maxHeight : null
    if (next === null) return
    event.preventDefault()
    setMaximized(false)
    setHeight(Math.min(maxHeight, Math.max(minHeight, next)))
  }

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
    const previous = rows.findIndex((row) => row.key === errorCursor.current)
    let index = rows.findIndex((row, rowIndex) => rowIndex > previous && row.entry.level === 'ERROR')
    if (index < 0) index = rows.findIndex((row) => row.entry.level === 'ERROR')
    if (index >= 0) {
      errorCursor.current = rows[index].key
      setFollowLatest(false)
      virtualizer.scrollToIndex(index, { align: 'center' })
    }
  }

  return (
    <section ref={panelRef} aria-label="日志面板" className={`log-panel ${open ? 'log-panel-open' : 'log-panel-closed'} ${resizing ? 'log-panel-resizing' : ''}`}
      style={open ? { height: panelHeight } : undefined}>
      {open && <div role="separator" aria-label="调整日志高度" aria-orientation="horizontal" tabIndex={0}
        aria-valuemin={minHeight} aria-valuemax={maxHeight} aria-valuenow={panelHeight}
        title="向上拖动扩展日志；方向键调整高度，Home / End 切换最小 / 最大高度"
        className="log-resize-handle" onPointerDown={startResize} onPointerMove={resize}
        onPointerUp={stopResize} onPointerCancel={stopResize} onLostPointerCapture={stopResize}
        onKeyDown={resizeWithKeyboard} />}
      <header className="log-toolbar">
        <UiButton aria-expanded={open} aria-controls={open ? 'log-panel-content' : undefined} icon={open ? <ChevronDown size={14} /> : <ChevronUp size={14} />} onClick={() => setOpen(!open)}>
          Log Panel
        </UiButton>
        <div className="log-actions">
          <UiText secondary>{open ? `${rows.length} / ${uniqueLogs.length}` : `${uniqueLogs.length} 条`}</UiText>
          {open && (
            <>
              <UiButton aria-label="下一个错误" title="下一个错误" disabled={!rows.some((row) => row.entry.level === 'ERROR')} icon={<TriangleAlert size={14} />} onClick={jumpNextError}>
                <span className="log-action-label">下一个错误</span>
              </UiButton>
              <UiButton aria-label="导出" title="导出当前筛选的原始日志，包含合并显示的重复项" icon={<Download size={14} />} onClick={exportLogs}>
                <span className="log-action-label">导出</span>
              </UiButton>
              <UiButton aria-label={maximized ? '还原日志高度' : '最大化日志'} title={maximized ? '还原日志高度' : '向上扩展至最大高度'}
                icon={maximized ? <Minimize2 size={14} /> : <Maximize2 size={14} />} onClick={() => setMaximized(!maximized)} />
            </>
          )}
        </div>
      </header>
      {open && (
        <div id="log-panel-content" className="log-panel-content">
          <div className="log-filters">
            <div className="log-primary-filters">
              <select
                aria-label="日志级别"
                className="ui-select log-level-select"
                value={level}
                onChange={(event) => setLevel(event.target.value as LogLevel | 'ALL')}
              >
                {levelOptions.map((item) => (
                  <option key={item} value={item}>{item === 'ALL' ? '全部级别' : item}</option>
                ))}
              </select>
              <span className="log-search">
                <Search size={14} />
                <input aria-label="搜索日志" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索日志 / 正则" />
              </span>
              <UiButton aria-label="显示诊断日志" aria-pressed={includeDiagnostics} variant={includeDiagnostics ? 'primary' : 'default'}
                title={`常规视图保留操作与异常；${diagnosticCount} 条详细诊断可展开查看`}
                onClick={() => setIncludeDiagnostics(!includeDiagnostics)}>诊断 {diagnosticCount}</UiButton>
              <UiButton aria-label="日志筛选" aria-expanded={filtersOpen} title="通道与诊断快捷筛选" icon={<SlidersHorizontal size={14} />}
                onClick={() => setFiltersOpen(!filtersOpen)} />
              <UiButton aria-label="跟随最新日志" aria-pressed={followLatest} title={followLatest ? '暂停自动滚动' : '跟随最新日志'}
                variant={followLatest ? 'primary' : 'default'} icon={<ArrowDownToLine size={14} />} onClick={() => setFollowLatest(!followLatest)} />
            </div>
            {filtersOpen && <div className="log-advanced-filters">
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
              <div className="log-quick-filters">
                {quickFilters.map((item) => (
                  <UiButton
                    key={item.label}
                    variant={search === item.query ? 'primary' : 'default'}
                    aria-pressed={search === item.query}
                    icon={item.icon}
                    onClick={() => { setSearch(item.query); setIncludeDiagnostics(true) }}
                  >
                    {item.label}
                  </UiButton>
                ))}
              </div>
            </div>}
          </div>
          <div ref={parentRef} className="log-viewport" onWheel={() => setFollowLatest(false)} onTouchMove={() => setFollowLatest(false)}>
            {rows.length === 0 && <div className="log-empty">{search ? '没有匹配的日志' : '暂无运行事件'}{!includeDiagnostics && diagnosticCount > 0 ? ` · ${diagnosticCount} 条诊断日志已收起` : ''}</div>}
            <div className="log-lines" style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
              {visibleItems.map((virtualItem) => {
                const row = rows[virtualItem.index]
                const entry = row.entry
                return (
                  <div
                    className={`log-line log-line-${entry.level.toLowerCase()}`}
                    key={row.key}
                    style={{ transform: `translateY(${virtualItem.start}px)` }}
                  >
                    <span>{formatLogTime(entry.ts)}</span>
                    <b style={{ color: channelColor[entry.channel] }}>{entry.channel}</b>
                    <em>{entry.level}</em>
                    <span className="log-message" title={entry.msg}>{entry.msg}</span>
                    <span className="log-repeat" title={`首次 ${formatLogTime(row.firstTs)}；末次 ${formatLogTime(entry.ts)}；导出保留每条记录`}>{row.count > 1 ? `×${row.count}` : ''}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
