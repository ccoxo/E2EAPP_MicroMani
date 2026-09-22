import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTelemetryStore } from '../stores/telemetry'
import type { LogEntry } from '../types'
import { LogPanel } from './LogPanel'

const initialState = useTelemetryStore.getState()
const entry = (id: number, level: LogEntry['level'], msg: string): LogEntry => ({ id, ts: 1000 + id, channel: '[HAL]', level, msg })

beforeEach(() => useTelemetryStore.setState({ logPanelOpen: true, logs: [] }))
afterEach(() => {
  cleanup()
  useTelemetryStore.setState(initialState, true)
  vi.restoreAllMocks()
})

describe('日志抽屉', () => {
  it('默认保留操作与异常，按需展开周期诊断并记住视图', () => {
    useTelemetryStore.setState({ logs: [
      entry(1, 'INFO', 'event=teleop_mode mode=position'),
      entry(2, 'INFO', 'event=teleop_axis_trace updateRet=[X:0]'),
      entry(3, 'WARNING', 'event=teleop_status lastError="设备断开"'),
      entry(4, 'ERROR', '急停未确认'),
    ] })
    render(<LogPanel />)
    expect(screen.getByText(/event=teleop_mode/)).toBeInTheDocument()
    expect(screen.getByText(/设备断开/)).toBeInTheDocument()
    expect(screen.getByText('急停未确认')).toBeInTheDocument()
    expect(screen.queryByText(/event=teleop_axis_trace/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '显示诊断日志' }))
    expect(screen.getByText(/event=teleop_axis_trace/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Log Panel' }))
    expect(screen.queryByRole('separator')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Log Panel' }))
    expect(screen.getByRole('button', { name: '显示诊断日志' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('重复事件合并显示，导出仍保留每次事件且不重复导出重放', () => {
    const first = entry(1, 'WARNING', '连接中断')
    const second = entry(2, 'WARNING', '连接中断')
    useTelemetryStore.setState({ logs: [first, { ...first }, second] })
    const createUrl = vi.fn(() => 'blob:log-export')
    vi.stubGlobal('URL', { createObjectURL: createUrl, revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    try {
      render(<LogPanel />)
      expect(screen.getAllByText('连接中断')).toHaveLength(1)
      expect(screen.getByText('×2')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '导出' }))
      expect(createUrl).toHaveBeenCalledTimes(1)
      const expected = [first, second].map((log) => `${new Date(log.ts).toISOString()} ${log.channel} ${log.level} ${log.msg}`).join('\n')
      expect((createUrl.mock.calls[0] as unknown[])[0]).toMatchObject({ size: new Blob([expected]).size })
      expect(useTelemetryStore.getState().logs).toHaveLength(3)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('键盘向上扩展，最大化可还原，收起再开保留自选高度', () => {
    render(<LogPanel />)
    const handle = screen.getByRole('separator', { name: '调整日志高度' })
    const original = Number(handle.getAttribute('aria-valuenow'))
    fireEvent.keyDown(handle, { key: 'ArrowUp' })
    expect(Number(handle.getAttribute('aria-valuenow'))).toBe(original + 32)
    fireEvent.click(screen.getByRole('button', { name: '最大化日志' }))
    expect(handle.getAttribute('aria-valuenow')).toBe(handle.getAttribute('aria-valuemax'))
    fireEvent.click(screen.getByRole('button', { name: '还原日志高度' }))
    expect(Number(handle.getAttribute('aria-valuenow'))).toBe(original + 32)
    fireEvent.click(screen.getByRole('button', { name: 'Log Panel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Log Panel' }))
    expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', String(original + 32))
  })

  it('通道和诊断预设按需展开，预设自动包含诊断', () => {
    useTelemetryStore.setState({ logs: [
      entry(1, 'INFO', 'event=teleop_axis_trace axis=Roll rawPose=[Roll:12.5]'),
      entry(2, 'INFO', 'event=teleop_axis_trace axis=X rawPose=[X:0.001]'),
    ] })
    render(<LogPanel />)
    expect(screen.queryByRole('button', { name: 'Roll' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '日志筛选' }))
    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    expect(screen.getByText(/axis=Roll/)).toBeInTheDocument()
    expect(screen.queryByText(/axis=X/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '显示诊断日志' })).toHaveAttribute('aria-pressed', 'true')
  })
})
