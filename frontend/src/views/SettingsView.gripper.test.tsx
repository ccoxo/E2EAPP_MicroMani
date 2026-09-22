import { act, cleanup, fireEvent, render, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { defaultConfig } from '../data'
import { initialGripperCommandProgress } from '../gripperDisplay'
import { useTelemetryStore } from '../stores/telemetry'
import type { TelemetryFrame } from '../types'
import { SettingsView } from './SettingsView'

const initialFrame = structuredClone(useTelemetryStore.getState().frame)
let timestamp = 0

function applyGripperStatus(partial: Partial<NonNullable<TelemetryFrame['gripperStatus']>>) {
  useTelemetryStore.setState((state) => ({
    frame: {
      ...state.frame,
      timestamp: ++timestamp,
      halOk: true,
      wsOk: true,
      gripperStatus: {
        nativeManaged: true,
        running: true,
        sides: {
          left: { ok: null, message: '' },
          right: { ok: null, message: '' },
        },
        ...partial,
      },
    },
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
  }))
}

async function renderGripperPage() {
  applyGripperStatus({})
  render(<MemoryRouter initialEntries={['/settings#gripper-left']}><SettingsView /></MemoryRouter>)
  await act(async () => { await Promise.resolve() })
  const card = document.getElementById('gripper-left')!
  return within(card)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(200_000)
  timestamp = Date.now()
  useTelemetryStore.setState({
    frame: { ...structuredClone(initialFrame), timestamp, gripperStatus: undefined, halOk: true, wsOk: true },
    config: structuredClone(defaultConfig),
    gripperCommand: { left: initialGripperCommandProgress(), right: initialGripperCommandProgress() },
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('夹爪三套展示语义', () => {
  it('请求启停、命令进度、反馈健康分开展示', async () => {
    const card = await renderGripperPage()

    expect(card.getAllByText('已请求断使能').length).toBeGreaterThan(0)
    expect(card.getAllByText('反馈待确认').length).toBeGreaterThan(0)

    fireEvent.click(card.getByRole('button', { name: '手动下发使能' }))
    expect(card.getByRole('status', { name: '左臂夹爪命令进度' })).toHaveTextContent('正在发送使能')
    expect(card.getAllByText('已请求使能').length).toBeGreaterThan(0)
    expect(card.queryByText('反馈正常')).not.toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(card.getByRole('status', { name: '左臂夹爪命令进度' })).toHaveTextContent('模拟命令已接受')

    await act(async () => {
      // gripper-left 为操作者侧，硬件侧是 right
      applyGripperStatus({ running: true, sides: { left: { ok: null }, right: { ok: true } } })
    })
    expect(card.getAllByText('反馈正常').length).toBeGreaterThan(0)
    expect(card.queryByText('已使能')).not.toBeInTheDocument()

    await act(async () => {
      applyGripperStatus({ running: false, sides: { left: { ok: null }, right: { ok: true } } })
    })
    expect(card.getAllByText('反馈待确认').length).toBeGreaterThan(0)
  })

  it('反馈失败显示反馈异常，且不回写请求使能', async () => {
    const card = await renderGripperPage()
    await act(async () => {
      applyGripperStatus({ running: true, sides: { left: { ok: null }, right: { ok: false, message: 'port busy' } } })
    })
    expect(card.getAllByText('反馈异常').length).toBeGreaterThan(0)
    expect(card.getAllByText('已请求断使能').length).toBeGreaterThan(0)
  })
})
