import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defaultConfig, defaultDiagnostics } from '../../data'
import { useTelemetryStore } from '../../stores/telemetry'
import PreCheckModal from './PreCheckModal'

function makeReadyForRecordPrecheck() {
  useTelemetryStore.setState((state) => ({
    config: {
      ...structuredClone(defaultConfig),
      teleop: {
        ...structuredClone(defaultConfig.teleop),
        leftConnected: false,
        rightConnected: true,
      },
    },
    diagnostics: structuredClone(defaultDiagnostics).map((item) =>
      item.key === 'omega7' || item.key === 'gripper' ? { ...item, status: 'ok' } : item,
    ),
    frame: {
      ...state.frame,
      halOk: true,
      wsOk: true,
      cameras: state.frame.cameras.map((camera) => ({ ...camera, fps: 30, health: 'ok' })),
      teleopHands: state.frame.teleopHands.map((hand) =>
        hand.side === 'left'
          ? {
              ...hand,
              connected: false,
              lastReadOk: false,
              message: 'logical teleop hand disconnected',
            }
          : {
              ...hand,
              connected: true,
              lastReadOk: true,
              message: '',
            },
      ),
    },
    recordSession: {
      ...state.recordSession,
      forceTareActive: false,
    },
  }))
}

afterEach(() => {
  cleanup()
  useTelemetryStore.getState().clearRecordSession()
  useTelemetryStore.setState((state) => ({
    config: structuredClone(defaultConfig),
    diagnostics: structuredClone(defaultDiagnostics),
    frame: {
      ...state.frame,
      cameras: state.frame.cameras.map((camera) => ({ ...camera, fps: 0, health: 'pending' })),
      teleopHands: state.frame.teleopHands.map((hand) => ({
        ...hand,
        connected: false,
        lastReadOk: false,
        message: '',
      })),
    },
  }))
  vi.restoreAllMocks()
})

describe('PreCheckModal', () => {
  it('allows a single logically connected Omega hand to satisfy the record hardware check', () => {
    const onConfirm = vi.fn()
    makeReadyForRecordPrecheck()

    render(<PreCheckModal open onConfirm={onConfirm} onCancel={vi.fn()} />)

    const confirmButton = screen.getByRole('button', { name: '确认开始' })
    expect(confirmButton).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: '已完成' }))

    expect(confirmButton).toBeEnabled()
    fireEvent.click(confirmButton)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})
