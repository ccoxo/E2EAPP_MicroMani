import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { defaultConfig } from '../data'
import { telemetryStaleAfterMs } from '../hardwareStatus'
import { initialMotionCommand } from '../stores/motionCommands'
import { initialControlLease } from '../stores/controlLease'
import { useTelemetryStore } from '../stores/telemetry'
import type { TelemetryFrame } from '../types'
import { initialControlSafety } from '../utils/controlSafety'
import { SettingsView } from './SettingsView'

class MockWebSocket {
  static OPEN = 1
  static current: MockWebSocket
  static confirmLeases = true
  static sessionCount = 0
  readyState = 1
  private sessionId = `motion-test-${++MockWebSocket.sessionCount}`
  private openTimer: ReturnType<typeof setTimeout>
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  constructor() {
    MockWebSocket.current = this
    this.openTimer = setTimeout(() => { this.onopen?.(); this.confirmSession() }, 0)
  }
  private confirmSession() {
    if (MockWebSocket.confirmLeases) this.onmessage?.({ data: JSON.stringify({ type: 'control_lease', data: {
      sessionId: this.sessionId, status: 'active', renewalOwner: 'backend',
    } }) } as MessageEvent)
  }
  send() {}
  close() {
    this.readyState = 3
    clearTimeout(this.openTimer)
    this.onclose?.({ code: 1000 } as CloseEvent)
  }
  emit(frame: TelemetryFrame) {
    this.onmessage?.({ data: JSON.stringify({ type: 'telemetry', data: frame }) } as MessageEvent)
  }
}

const initialFrame = structuredClone(useTelemetryStore.getState().frame)
let timestamp = 0
function emit(axes: Array<boolean | null>, extra: Partial<TelemetryFrame> = {}) {
  const frame = useTelemetryStore.getState().frame
  MockWebSocket.current.emit({
    ...frame, timestamp: ++timestamp, halOk: true, wsOk: true,
    motionEnabled: { left: axes.every((value) => value === true), right: null },
    motionAxisEnabled: { left: axes, right: Array(6).fill(null) },
    ...extra,
  })
}

async function renderMotionPage() {
  useTelemetryStore.getState().startBackend()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
    emit(Array(6).fill(false))
    await Promise.resolve()
  })
  render(<MemoryRouter initialEntries={['/settings#manual']}><SettingsView /></MemoryRouter>)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  const card = document.getElementById('motion-right')!
  const manual = screen.getByRole('heading', { name: '右臂手动控制' }).closest('article')!
  return { card: within(card), manual: within(manual) }
}

function expectSharedProgress(text: string) {
  expect(screen.getByRole('status', { name: '右臂运动控制卡使能进度' })).toHaveTextContent(text)
  expect(screen.getByRole('status', { name: '右臂手动控制使能进度' })).toHaveTextContent(text)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(100_000)
  timestamp = Date.now()
  MockWebSocket.confirmLeases = true
  MockWebSocket.sessionCount = 0
  vi.stubGlobal('WebSocket', MockWebSocket)
  useTelemetryStore.setState({
    frame: { ...structuredClone(initialFrame), timestamp }, config: structuredClone(defaultConfig),
    motionCommand: { left: initialMotionCommand(), right: initialMotionCommand() },
    controlSafety: initialControlSafety(),
    controlLease: initialControlLease(false),
    telemetryLink: { state: 'connecting', lastFrameReceivedAt: null },
  })
})

afterEach(() => {
  cleanup()
  useTelemetryStore.getState().stopBackend()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('运动使能共享入口', () => {
  it('WS 挑战已经应答但执行侧未确认租约时两处仍拒绝使能', async () => {
    MockWebSocket.confirmLeases = false
    const enable = vi.spyOn(api, 'enableMotionSide').mockResolvedValue({ ok: true })
    const disable = vi.spyOn(api, 'disableMotionSide').mockResolvedValue({ ok: true })
    const { card, manual } = await renderMotionPage()
    expect(useTelemetryStore.getState().controlLease).toMatchObject({ required: true, status: 'pending' })
    expect(card.getByRole('button', { name: '使能全部' })).toBeDisabled()
    expect(manual.queryByRole('button', { name: '使能' })).not.toBeInTheDocument()
    await act(async () => {
      fireEvent.click(card.getByRole('button', { name: '使能全部' }))
      fireEvent.click(manual.getByRole('button', { name: '断使能' }))
    })
    expect(enable).not.toHaveBeenCalled()
    expect(disable).toHaveBeenCalledExactlyOnceWith('left')
  })

  it.each(['offline', 'stale', 'halUnavailable', 'emergency'] as const)('%s 阻止两处使能并保留断使能入口', async (reason) => {
    const enable = vi.spyOn(api, 'enableMotionSide').mockResolvedValue({ ok: true })
    const disable = vi.spyOn(api, 'disableMotionSide').mockResolvedValue({ ok: true })
    const { card, manual } = await renderMotionPage()
    await act(async () => {
      useTelemetryStore.setState((state) => ({
        ...(reason === 'emergency' ? { controlSafety: { ...state.controlSafety, emergencyRequested: true } }
          : reason === 'halUnavailable' ? { frame: { ...state.frame, halOk: false } }
            : { telemetryLink: { ...state.telemetryLink, state: reason } }),
      }))
    })

    expect(card.getByRole('button', { name: '使能全部' })).toBeDisabled()
    expect(card.getByRole('button', { name: '断使能' })).toBeEnabled()
    expect(manual.getByRole('button', { name: '断使能' })).toBeEnabled()
    await act(async () => {
      fireEvent.click(card.getByRole('button', { name: '使能全部' }))
      fireEvent.click(manual.getByRole('button', { name: '断使能' }))
    })
    expect(enable).not.toHaveBeenCalled()
    expect(disable).toHaveBeenCalledExactlyOnceWith('left')
  })

  it('两处 UI 共享进度、转换硬件侧，并在旧 HTTP 完成后发送相反请求', async () => {
    let finishEnable!: (value: unknown) => void
    const enable = vi.spyOn(api, 'enableMotionSide').mockImplementation(() => new Promise((resolve) => { finishEnable = resolve }))
    const disable = vi.spyOn(api, 'disableMotionSide').mockResolvedValue({ ok: true })
    const { card, manual } = await renderMotionPage()
    fireEvent.click(card.getByRole('button', { name: '使能全部' }))
    expect(enable).toHaveBeenCalledExactlyOnceWith('left')
    expectSharedProgress('正在发送使能请求')
    expect(card.queryByText('已使能')).not.toBeInTheDocument()
    expect(manual.queryByText('已使能')).not.toBeInTheDocument()

    fireEvent.click(manual.getByRole('button', { name: '断使能' }))
    expectSharedProgress('断使能请求排队中')
    expect(disable).not.toHaveBeenCalled()
    await act(async () => { emit(Array(6).fill(true)) })
    expect(card.getByText('已使能')).toBeInTheDocument()
    expect(manual.getByText('已使能')).toBeInTheDocument()
    expect(disable).not.toHaveBeenCalled()

    await act(async () => { finishEnable({ ok: true }); await Promise.resolve() })
    expect(disable).toHaveBeenCalledExactlyOnceWith('left')
    expectSharedProgress('等待断使能反馈')
    await act(async () => { emit(Array(6).fill(false)) })
    expectSharedProgress('断使能请求已获反馈确认')
    expect(card.getAllByText('未使能').length).toBeGreaterThan(0)
    expect(manual.getByText('未使能')).toBeInTheDocument()
  })

  it('HTTP 成功仍等待遥测，部分反馈不误报全部使能，停滞撤销两处可信状态', async () => {
    vi.spyOn(api, 'enableMotionSide').mockResolvedValue({ ok: true })
    const { card, manual } = await renderMotionPage()
    await act(async () => { fireEvent.click(manual.getByRole('button', { name: '使能' })) })
    expectSharedProgress('等待使能反馈')
    await act(async () => {
      emit([true, null, null, null, null, null], { motionEnabled: { left: true, right: null } })
    })
    expect(card.getAllByText('部分使能').length).toBeGreaterThan(0)
    expect(manual.getByText('部分使能')).toBeInTheDocument()
    expect(useTelemetryStore.getState().motionCommand.left.phase).toBe('waitingConfirm')
    await act(async () => { emit(Array(6).fill(true)) })
    expectSharedProgress('使能请求已获反馈确认')
    await act(async () => { await vi.advanceTimersByTimeAsync(telemetryStaleAfterMs + 300) })
    expect(useTelemetryStore.getState().telemetryLink.state).toBe('stale')
    expect(useTelemetryStore.getState().motionCommand.left.phase).toBe('idle')
    expect(card.queryByText('已使能')).not.toBeInTheDocument()
    expect(manual.queryByText('已使能')).not.toBeInTheDocument()
    expectSharedProgress('状态未知')
  })

  it('HAL 不可用时中止确认，迟到响应不能恢复成功状态', async () => {
    let finishEnable!: (value: unknown) => void
    vi.spyOn(api, 'enableMotionSide').mockImplementation(() => new Promise((resolve) => { finishEnable = resolve }))
    const { card, manual } = await renderMotionPage()
    fireEvent.click(card.getByRole('button', { name: '使能全部' }))
    await act(async () => { emit(Array(6).fill(true), { halOk: false }) })
    expectSharedProgress('状态未知')
    expect(card.queryByText('已使能')).not.toBeInTheDocument()
    expect(manual.queryByText('已使能')).not.toBeInTheDocument()
    await act(async () => { finishEnable({ ok: true }) })
    expect(useTelemetryStore.getState().motionCommand.left.phase).toBe('failed')
  })

  it.each(['error', 'close'] as const)('断连 %s 后不提交积压遥测来恢复已使能展示', async (event) => {
    const { card, manual } = await renderMotionPage()
    await act(async () => { emit(Array(6).fill(true)) })
    await act(async () => {
      emit(Array(6).fill(true), { frameCount: 123 })
      if (event === 'error') MockWebSocket.current.onerror?.()
      else MockWebSocket.current.close()
      await vi.advanceTimersByTimeAsync(450)
    })
    expect(useTelemetryStore.getState().telemetryLink.state).toBe('offline')
    expect(card.queryByText('已使能')).not.toBeInTheDocument()
    expect(manual.queryByText('已使能')).not.toBeInTheDocument()
  })

  it('重连后忽略旧 socket 的消息、错误与 open 回调', async () => {
    vi.spyOn(api, 'enableMotionSide').mockResolvedValue({ ok: true })
    await renderMotionPage()
    const oldSocket = MockWebSocket.current
    await act(async () => {
      useTelemetryStore.getState().stopBackend()
      useTelemetryStore.getState().startBackend()
      emit(Array(6).fill(false))
      await vi.advanceTimersByTimeAsync(70)
    })
    expect(useTelemetryStore.getState().telemetryLink.state).toBe('live')
    await act(async () => { useTelemetryStore.getState().setMotionEnabled('left', true) })
    await act(async () => {
      oldSocket.emit({
        ...useTelemetryStore.getState().frame, timestamp: ++timestamp,
        motionAxisEnabled: { left: Array(6).fill(true), right: Array(6).fill(null) },
      })
    })
    expect(useTelemetryStore.getState().motionCommand.left.phase).toBe('waitingConfirm')
    await act(async () => { oldSocket.onerror?.(); oldSocket.onopen?.() })
    expect(useTelemetryStore.getState().telemetryLink.state).toBe('live')
    expect(useTelemetryStore.getState().motionCommand.left.phase).toBe('waitingConfirm')
    await act(async () => { emit(Array(6).fill(true)) })
    expect(useTelemetryStore.getState().motionCommand.left.phase).toBe('confirmed')
  })

  it('停止 mock 遥测立即撤销连接可信状态', async () => {
    useTelemetryStore.getState().startMock()
    await vi.advanceTimersByTimeAsync(34)
    expect(useTelemetryStore.getState().telemetryLink.state).toBe('live')
    useTelemetryStore.getState().stopMock()
    expect(useTelemetryStore.getState().telemetryLink.state).toBe('offline')
    expect(useTelemetryStore.getState().frame.wsOk).toBe(false)
  })
})
