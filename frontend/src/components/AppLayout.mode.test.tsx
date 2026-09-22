import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { AppLayout } from './AppLayout'
import { useTelemetryStore } from '../stores/telemetry'

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="loc">{location.pathname}{location.hash}</div>
}

function renderLayout(initial = '/') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<div>home</div>} />
          <Route path="record" element={<div>record</div>} />
          <Route path="auto" element={<div>auto</div>} />
          <Route path="dataset" element={<div>dataset</div>} />
          <Route path="settings" element={<div>settings</div>} />
          <Route path="*" element={<div>other</div>} />
        </Route>
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useTelemetryStore.setState({
    frame: {
      ...useTelemetryStore.getState().frame,
      halOk: true,
      wsOk: true,
      resource: { ...useTelemetryStore.getState().frame.resource, wsHz: 30 },
      dangerIndex: 0,
    },
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('工作模式分段与路由', () => {
  it('按 URL 推导选中项：record / auto / settings#manual', () => {
    renderLayout('/record')
    expect(screen.getByRole('radio', { name: 'Record' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Auto' })).toHaveAttribute('aria-checked', 'false')
    cleanup()

    renderLayout('/auto')
    expect(screen.getByRole('radio', { name: 'Auto' })).toHaveAttribute('aria-checked', 'true')
    cleanup()

    renderLayout('/settings#manual')
    expect(screen.getByRole('radio', { name: 'Manual' })).toHaveAttribute('aria-checked', 'true')
  })

  it('主页与数据集等页面不高亮任何工作模式', () => {
    renderLayout('/')
    expect(screen.getByRole('radio', { name: 'Record' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('radio', { name: 'Auto' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('radio', { name: 'Manual' })).toHaveAttribute('aria-checked', 'false')
  })

  it('点击分段会导航到对应路由，而非只改本地状态', () => {
    renderLayout('/')
    fireEvent.click(screen.getByRole('radio', { name: 'Auto' }))
    expect(screen.getByTestId('loc')).toHaveTextContent('/auto')
    expect(screen.getByRole('radio', { name: 'Auto' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByRole('radio', { name: 'Manual' }))
    expect(screen.getByTestId('loc')).toHaveTextContent('/settings#manual')
    fireEvent.click(screen.getByRole('radio', { name: 'Record' }))
    expect(screen.getByTestId('loc')).toHaveTextContent('/record')
  })

  it('左侧导航切页会同步更新工作模式高亮', () => {
    renderLayout('/record')
    fireEvent.click(screen.getByRole('link', { name: /自动/ }))
    expect(screen.getByTestId('loc')).toHaveTextContent('/auto')
    expect(screen.getByRole('radio', { name: 'Auto' })).toHaveAttribute('aria-checked', 'true')
  })
})
