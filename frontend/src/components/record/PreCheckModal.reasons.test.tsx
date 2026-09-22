import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { defaultDiagnostics } from '../../data'
import { useTelemetryStore } from '../../stores/telemetry'
import type { TelemetryFrame } from '../../types'
import PreCheckModal from './PreCheckModal'

const baseFrame = structuredClone(useTelemetryStore.getState().frame)

function healthyFrame(): TelemetryFrame {
  return {
    ...baseFrame,
    timestamp: Date.now(),
    halOk: true,
    wsOk: true,
    cameras: [
      { key: 'global', label: '全局相机', fps: 30, timestampSkewMs: 0, frameAgeMs: 20, health: 'ok' },
    ],
    teleopHands: [
      { side: 'left', connected: true, calibrated: true, openId: 0, deviceId: 0, serial: 's', systemName: 'n', leftHanded: true, pose: [0,0,0,0,0,0], clutchPressed: false, gripperPressed: false, gripperGapMm: null, lastReadOk: true, message: '' },
      { side: 'right', connected: true, calibrated: true, openId: 1, deviceId: 1, serial: 's', systemName: 'n', leftHanded: false, pose: [0,0,0,0,0,0], clutchPressed: false, gripperPressed: false, gripperGapMm: null, lastReadOk: true, message: '' },
    ],
    motionEnabled: { left: true, right: true },
    motionAxisEnabled: {
      left: Array(6).fill(true),
      right: Array(6).fill(null),
    },
  }
}

function readyDiagnostics() {
  return structuredClone(defaultDiagnostics).map((item) =>
    item.key === 'omega7' || item.key === 'gripper' ? { ...item, status: 'ok' as const } : item,
  )
}

beforeEach(() => {
  useTelemetryStore.setState({
    frame: healthyFrame(),
    diagnostics: readyDiagnostics(),
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    recordSession: {
      ...useTelemetryStore.getState().recordSession,
      resetRequiredSides: ['left'],
      returnOriginInFlight: false,
    },
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('PreCheck 失败原因与使能入口', () => {
  it('硬件连接未通过时列出具体原因', () => {
    useTelemetryStore.setState((s) => ({
      frame: {
        ...s.frame,
        halOk: false,
        cameras: s.frame.cameras.map((c) => ({ ...c, health: 'error' as const })),
      },
    }))
    render(
      <MemoryRouter>
        <PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />
      </MemoryRouter>,
    )
    expect(screen.getByText('HAL 不可用')).toBeInTheDocument()
    expect(screen.getByText(/相机异常：全局相机/)).toBeInTheDocument()
  })

  it('回工作原点未就绪时提示原因并提供去设置使能；仍可手动勾选', () => {
    useTelemetryStore.setState((s) => ({
      frame: {
        ...s.frame,
        motionEnabled: { left: false, right: true },
        motionAxisEnabled: {
          left: Array(6).fill(false),
          right: Array(6).fill(null),
        },
      },
    }))
    const onCancel = vi.fn()
    render(
      <MemoryRouter initialEntries={['/record']}>
        <PreCheckModal open onConfirm={vi.fn()} onCancel={onCancel} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/左臂未使能/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '去设置页使能' })).toBeEnabled()
    // 回原点按钮仍禁用，但手动勾选可绕过（不把使能并入 allDone）
    expect(screen.getByRole('button', { name: '自动回工作原点' })).toBeDisabled()
    fireEvent.click(screen.getAllByRole('checkbox')[0])
    fireEvent.click(screen.getByRole('button', { name: '确认开始' }))
    // 确认可用：硬件连接与回原点勾选均已满足
  })

  it('使能就绪后不显示受阻原因', () => {
    useTelemetryStore.setState((s) => ({
      frame: {
        ...s.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: Array(6).fill(true),
          right: Array(6).fill(true),
        },
      },
    }))
    render(
      <MemoryRouter>
        <PreCheckModal open onConfirm={vi.fn()} onCancel={vi.fn()} />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('button', { name: '去设置页使能' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '自动回工作原点' })).toBeEnabled()
  })
})
