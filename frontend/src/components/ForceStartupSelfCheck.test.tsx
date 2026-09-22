import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as api from '../api'
import { useTelemetryStore } from '../stores/telemetry'
import { initialControlSafety } from '../utils/controlSafety'
import { ForceStartupSelfCheck } from './ForceStartupSelfCheck'
import { GlobalEmergencyStopButton } from './GlobalEmergencyStopButton'

const initialState = useTelemetryStore.getState()
beforeEach(() => {
  useTelemetryStore.setState({
    controlSafety: { ...initialControlSafety(), emergencyRequested: true },
    controlLease: { required: false, status: 'active', sessionId: null, expiresAt: 0, reason: '' },
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    frame: { ...initialState.frame, timestamp: Date.now(), recording: false, halOk: true, wsOk: true,
      forceStatus: { source: 'hkvl_serial', calibration: { state: 'waiting_sensors', progress: 0 }, safety: { latched: true, canAcknowledge: false, acknowledgeBlocker: '请先自检' } },
    },
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); useTelemetryStore.setState(initialState) })

it('锁存下先明确确认双侧卸载，再执行自检；请求成功不会自动 ACK', async () => {
  const run = vi.spyOn(api, 'runHkvlStartupSelfCheck').mockResolvedValue({ ok: true })
  const ack = vi.spyOn(api, 'acknowledgeSafety')
  render(<><ForceStartupSelfCheck /><GlobalEmergencyStopButton /></>)
  expect(screen.getByRole('button', { name: '确认安全态' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '双侧去皮自检' }))
  expect(run).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '开始双侧自检' })).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: '我已确认双侧传感器卸载并保持静止' }))
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '开始双侧自检' })))
  expect(run).toHaveBeenCalledOnce()
  expect(ack).not.toHaveBeenCalled()
  expect(useTelemetryStore.getState().frame.forceStatus?.safety?.latched).toBe(true)
  expect(screen.getByRole('button', { name: '确认安全态' })).toBeDisabled()
  act(() => useTelemetryStore.setState((state) => ({ frame: { ...state.frame, forceStatus: {
    source: 'hkvl_serial', calibration: { state: 'ready_for_ack', progress: 100 }, safety: { latched: true, canAcknowledge: true },
  } } })))
  expect(screen.getByText('自检通过，待人工确认 · 100%')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认安全态' })).toBeEnabled()
})

it('取消卸载确认不发送命令，失败原因由 HAL 遥测显示', () => {
  const run = vi.spyOn(api, 'runHkvlStartupSelfCheck')
  useTelemetryStore.setState((state) => ({ frame: { ...state.frame, forceStatus: {
    ...state.frame.forceStatus, calibration: { state: 'failed', progress: 30, reason: 'left sensor is unstable' },
  } } }))
  render(<ForceStartupSelfCheck />)
  expect(screen.getByText('left sensor is unstable')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '双侧去皮自检' }))
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(run).not.toHaveBeenCalled()
})

it('控制租约过期仍阻止锁存期间自检', () => {
  useTelemetryStore.setState({ controlLease: { required: true, status: 'expired', sessionId: 'old', expiresAt: 0, reason: '租约过期' } })
  render(<ForceStartupSelfCheck />)
  expect(screen.getByRole('button', { name: '双侧去皮自检' })).toBeDisabled()
})
