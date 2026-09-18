import { cleanup, render, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { useTelemetryStore } from '../stores/telemetry'
import { initialControlSafety } from '../utils/controlSafety'
import { AppLayout } from './AppLayout'

const initialState = useTelemetryStore.getState()

beforeEach(() => {
  useTelemetryStore.setState({
    ...initialState,
    controlSafety: initialControlSafety(),
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    frame: { ...initialState.frame, halOk: true, wsOk: true, dangerIndex: 0, forceStatus: { safety: { latched: false } } },
    logs: [],
  })
})

afterEach(() => {
  cleanup()
  useTelemetryStore.setState(initialState, true)
})

function statusRegions() {
  render(<MemoryRouter><AppLayout /></MemoryRouter>)
  return ['.top-status', '.status-bar'].map((selector) => within(document.querySelector<HTMLElement>(selector)!))
}

describe('顶部与底部全局安全状态', () => {
  it.each(['pending', 'failed'] as const)('本地急停 %s 时不显示绿色零危险值', (phase) => {
    useTelemetryStore.setState({
      controlSafety: { ...initialControlSafety(), emergencyRequested: true, emergencyPending: phase === 'pending', emergencyError: phase === 'failed' ? '网络失败' : null },
    })
    for (const region of statusRegions()) {
      expect(region.getByText('Safety LOCK')).toHaveClass('ui-tag-error')
      expect(region.queryByText('Safety 0.00')).not.toBeInTheDocument()
    }
  })

  it.each(['offline', 'stale', 'connecting', 'expired'] as const)('遥测 %s 时 HAL 与 Safety 不冒充正常', (mode) => {
    useTelemetryStore.setState({ telemetryLink: { state: mode === 'expired' ? 'live' : mode, lastFrameReceivedAt: mode === 'expired' ? Date.now() - 3000 : Date.now() } })
    for (const region of statusRegions()) {
      expect(region.getByText('HAL 未知')).toHaveClass('ui-tag-muted')
      expect(region.getByText('Safety 不可用')).toHaveClass('ui-tag-muted')
      expect(region.queryByText('Safety 0.00')).not.toBeInTheDocument()
    }
  })

  it('HAL 失联时安全值不可用', () => {
    useTelemetryStore.setState((state) => ({ frame: { ...state.frame, halOk: false } }))
    for (const region of statusRegions()) {
      expect(region.getByText('HAL')).toHaveClass('ui-tag-error')
      expect(region.getByText('Safety 不可用')).toHaveClass('ui-tag-muted')
    }
  })

  it('离线不会隐藏已经知道的硬件锁存', () => {
    useTelemetryStore.setState((state) => ({
      telemetryLink: { state: 'offline', lastFrameReceivedAt: null },
      frame: { ...state.frame, forceStatus: { safety: { latched: true } } },
    }))
    for (const region of statusRegions()) {
      expect(region.getByText('HAL 未知')).toHaveClass('ui-tag-muted')
      expect(region.getByText('Safety LOCK')).toHaveClass('ui-tag-error')
    }
  })

  it('新鲜遥测且无保护时保留正常数值', () => {
    for (const region of statusRegions()) {
      expect(region.getByText('HAL')).toHaveClass('ui-tag-success')
      expect(region.getByText('Safety 0.00')).toHaveClass('ui-tag-success')
    }
  })
})
