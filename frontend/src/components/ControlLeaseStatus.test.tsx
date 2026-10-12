import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useTelemetryStore } from '../stores/telemetry'
import { initialControlLease } from '../stores/controlLease'
import { ControlLeaseStatus } from './ControlLeaseStatus'
import { StatusBar } from './StatusBar'

const initial = useTelemetryStore.getState()
afterEach(() => { cleanup(); useTelemetryStore.setState(initial, true) })

it('真实模式租约未确认时明确提示，Safety 不显示绿色正常数值', () => {
  useTelemetryStore.setState({ controlLease: initialControlLease(),
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    frame: { ...initial.frame, halOk: true, wsOk: true, dangerIndex: 0, forceStatus: { safety: { latched: false } } },
  })
  render(<><ControlLeaseStatus /><StatusBar /></>)
  expect(screen.getByRole('status', { name: '控制安全租约' })).toHaveTextContent('等待执行侧安全租约确认')
  expect(screen.getByText('Safety 不可用')).toBeInTheDocument()
  expect(screen.queryByText('Safety 0.00')).not.toBeInTheDocument()
})

it('租约确认后仍要求显式解除硬件锁定，不自动 ACK', () => {
  const acknowledge = vi.fn()
  useTelemetryStore.setState({ controlLease: { ...initialControlLease(), status: 'active', sessionId: 'ui-fixture', expiresAt: performance.now() + 2500 },
    frame: { ...initial.frame, forceStatus: { safety: { latched: true } } }, acknowledgeSafety: acknowledge,
  })
  render(<ControlLeaseStatus />)
  expect(screen.getByRole('status')).toHaveTextContent('请核对设备后显式确认解除')
  expect(acknowledge).not.toHaveBeenCalled()
})

it('失效租约保留可见恢复入口', () => {
  useTelemetryStore.setState({ controlLease: { ...initialControlLease(), status: 'expired', reason: '页面主线程响应超时' } })
  const startBackend = vi.fn(), stopBackend = vi.fn(), acknowledgeSafety = vi.fn()
  useTelemetryStore.setState({ startBackend, stopBackend, acknowledgeSafety })
  render(<ControlLeaseStatus />)
  expect(screen.getByRole('status')).toHaveTextContent('页面主线程响应超时')
  fireEvent.click(screen.getByRole('button', { name: '重新连接并核验' }))
  expect(stopBackend).toHaveBeenCalledOnce()
  expect(startBackend).toHaveBeenCalledOnce()
  expect(acknowledgeSafety).not.toHaveBeenCalled()
})

it('控制租约正常时仍显示观察入口，观察模式提供申请控制', () => {
  const setObserverMode = vi.fn()
  useTelemetryStore.setState({ observerMode: false, setObserverMode,
    controlLease: { ...initialControlLease(), status: 'active', sessionId: 'owner' },
    frame: { ...initial.frame, forceStatus: { safety: { latched: false } } },
  })
  const view = render(<ControlLeaseStatus />)
  expect(screen.getByRole('status')).toHaveTextContent('控制模式')
  fireEvent.click(screen.getByRole('button', { name: '切换到观察模式' }))
  expect(setObserverMode).toHaveBeenLastCalledWith(true)
  useTelemetryStore.setState({ observerMode: true, controlLease: initialControlLease() })
  view.rerender(<ControlLeaseStatus />)
  fireEvent.click(screen.getByRole('button', { name: '申请控制' }))
  expect(setObserverMode).toHaveBeenLastCalledWith(false)
})
