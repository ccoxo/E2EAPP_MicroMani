import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
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
    telemetryLink: {
      state: 'live',
      lastFrameReceivedAt: Date.now(),
    },
    frame: {
      ...state.frame,
      motionEnabled: { left: true, right: true },
      motionAxisEnabled: {
        left: [true, true, true, true, true, true],
        right: [true, true, true, true, true, false],
      },
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

  it('uses required work-origin side returns for the precheck auto-return action', async () => {
    makeReadyForRecordPrecheck()
    const returnOriginSpy = vi.spyOn(api, 'returnMotionOriginSide').mockResolvedValue({ ok: true })
    const homeAllSpy = vi.spyOn(api, 'homeAll').mockResolvedValue({ ok: true })
    const captureOriginSpy = vi.spyOn(api, 'captureMotionOrigin').mockResolvedValue({ ok: true })
    const homeMotionSideSpy = vi.spyOn(api, 'homeMotionSide').mockResolvedValue({ ok: true })

    render(<PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: '自动回工作原点' }))

    await vi.waitFor(() => expect(returnOriginSpy).toHaveBeenCalledTimes(1))
    expect(returnOriginSpy.mock.calls).toEqual([['left']])
    expect(homeAllSpy).not.toHaveBeenCalled()
    expect(captureOriginSpy).not.toHaveBeenCalled()
    expect(homeMotionSideSpy).not.toHaveBeenCalled()
  })

  it('allows low camera fps as a warning-only precheck condition', () => {
    const onConfirm = vi.fn()
    makeReadyForRecordPrecheck()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        cameras: state.frame.cameras.map((camera) => ({ ...camera, fps: 14, health: 'ok' })),
      },
    }))

    render(<PreCheckModal open onConfirm={onConfirm} onCancel={vi.fn()} />)

    fireEvent.click(screen.getByRole('checkbox'))

    const confirmButton = screen.getAllByRole('button').find((button) => button.className.includes('ant-btn-primary'))
    if (!confirmButton) throw new Error('confirm button not found')
    expect(confirmButton).toBeEnabled()
    fireEvent.click(confirmButton)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('does not label direct camera capture as worker fallback', () => {
    makeReadyForRecordPrecheck()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        cameras: state.frame.cameras.map((camera) => ({
          ...camera,
          fps: 30,
          health: 'ok',
          backend: 'CAP_DSHOW',
          workerActive: false,
        })),
      },
    }))

    render(<PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />)

    expect(screen.queryByText(/fallback/)).not.toBeInTheDocument()
  })

  it('disables precheck auto-return while a return-to-origin request is pending', () => {
    makeReadyForRecordPrecheck()
    useTelemetryStore.setState((state) => ({
      recordSession: {
        ...state.recordSession,
        returnOriginInFlight: true,
      },
    }))

    render(<PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />)

    const returnButton = screen.getAllByRole('button').find((button) => button.style.marginTop === '6px')
    if (!returnButton) throw new Error('return-origin button not found')
    expect(returnButton).toBeDisabled()
  })

  it('disables precheck auto-return while a motion side is not enabled', () => {
    makeReadyForRecordPrecheck()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: false, right: true },
        motionAxisEnabled: {
          left: [false, false, false, false, false, false],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    render(<PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />)

    expect(screen.getByRole('button', { name: '自动回工作原点' })).toBeDisabled()
  })

  it('keeps precheck auto-return enabled when only the non-required right side is disabled', () => {
    makeReadyForRecordPrecheck()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: false },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [false, false, false, false, false, false],
        },
      },
    }))

    render(<PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />)

    const returnButton = screen.getAllByRole('button').find((button) => button.style.marginTop === '6px')
    if (!returnButton) throw new Error('return-origin button not found')
    expect(returnButton).toBeEnabled()
  })
})
