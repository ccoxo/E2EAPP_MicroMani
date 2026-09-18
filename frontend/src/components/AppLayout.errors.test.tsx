import { Suspense, lazy, type ReactNode } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useTelemetryStore } from '../stores/telemetry'
import type { LogEntry, TelemetryFrame } from '../types'
import { AppLayout } from './AppLayout'

const fault = vi.hoisted(() => ({ emergencyDisplay: false }))
vi.mock('./GlobalEmergencyStopButton', async (importOriginal) => {
  const original = await importOriginal<typeof import('./GlobalEmergencyStopButton')>()
  return { GlobalEmergencyStopButton: () => {
    if (fault.emergencyDisplay) throw new Error('隔离测试：急停显示失败')
    return <original.GlobalEmergencyStopButton />
  } }
})

const initialState = useTelemetryStore.getState()
const silenceExpectedError = (event: ErrorEvent) => {
  if (event.message.startsWith('隔离测试：')) event.preventDefault()
}

afterEach(() => {
  cleanup()
  fault.emergencyDisplay = false
  useTelemetryStore.setState(initialState, true)
  window.removeEventListener('error', silenceExpectedError)
  vi.restoreAllMocks()
})

function BrokenPage(): ReactNode {
  throw new Error('隔离测试：页面渲染失败')
}

describe('业务页错误隔离', () => {
  it('急停状态显示自身失败时仍保留直接急停按钮', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    window.addEventListener('error', silenceExpectedError)
    fault.emergencyDisplay = true
    const emergencyStop = vi.fn()
    useTelemetryStore.setState({ triggerEmergencyStop: emergencyStop })
    render(<MemoryRouter><Routes><Route element={<AppLayout />}><Route index element={<div>可用页面</div>} /></Route></Routes></MemoryRouter>)
    expect(screen.getByText('急停状态显示异常')).toBeInTheDocument()
    expect(useTelemetryStore.getState().controlLease.status).toBe('expired')
    expect(screen.getByText('可用页面')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全局急停' }))
    expect(emergencyStop).toHaveBeenCalledTimes(1)
  })

  it.each(['日志面板', '顶部状态'] as const)('%s 渲染异常不卸载页面和急停入口', async (region) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const onError = (event: ErrorEvent) => event.preventDefault()
    window.addEventListener('error', onError)
    const emergencyStop = vi.fn()
    useTelemetryStore.setState({
      triggerEmergencyStop: emergencyStop,
      ...(region === '日志面板'
        ? { logs: [null] as unknown as LogEntry[] }
        : { frame: { ...initialState.frame, resource: null } as unknown as TelemetryFrame }),
    })
    try {
      render(
        <MemoryRouter initialEntries={['/settings']}>
          <Routes><Route element={<AppLayout />}><Route path="settings" element={<div>其余模块继续显示</div>} /></Route></Routes>
        </MemoryRouter>,
      )
      expect(screen.getByText('其余模块继续显示')).toBeInTheDocument()
      expect(screen.getByText(`${region}暂时不可用`)).toBeInTheDocument()
      expect(useTelemetryStore.getState().controlLease.status).toBe('expired')
      fireEvent.click(screen.getByRole('button', { name: '全局急停' }))
      expect(emergencyStop).toHaveBeenCalledTimes(1)
    } finally {
      window.removeEventListener('error', onError)
    }
  })

  it.each(['render', 'lazy'] as const)('%s 失败保留急停和导航，切换页面后可恢复', async (failure) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    window.addEventListener('error', silenceExpectedError)
    const emergencyStop = vi.fn()
    useTelemetryStore.setState({ triggerEmergencyStop: emergencyStop })
    const LazyPage = lazy(() => Promise.reject(new Error('隔离测试：分块加载失败')))
    const content = failure === 'render' ? <BrokenPage /> : <LazyPage />

    render(
      <MemoryRouter initialEntries={['/model']}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="model" element={<Suspense fallback={<div>加载中</div>}>{content}</Suspense>} />
            <Route path="settings" element={<div>可用的设置页</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('页面暂时无法显示')
    expect(useTelemetryStore.getState().controlLease.status).toBe('expired')
    expect(screen.getByRole('button', { name: '重试页面并重新核验' })).toBeEnabled()
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全局急停' }))
    expect(emergencyStop).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('link', { name: '设置' }))
    expect(screen.getByText('可用的设置页')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '全局急停' })).toBeEnabled()
  })
})
