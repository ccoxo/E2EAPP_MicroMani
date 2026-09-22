import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTelemetryStore } from '../../stores/telemetry'
import { defaultConfig } from '../../data'
import { initialControlSafety } from '../../utils/controlSafety'
import { initialControlLease } from '../../stores/controlLease'
import { GripperCard, ManualGripperControl } from './GripperCards'
import type { PendingComparison } from './shared'

const apiMode = vi.hoisted(() => ({ mock: false }))
vi.mock('../../api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../api')>(),
  get mockMode() { return apiMode.mock },
}))

const initialState = useTelemetryStore.getState()

beforeEach(() => {
  apiMode.mock = false
  useTelemetryStore.setState({
    ...initialState,
    config: structuredClone(defaultConfig),
    controlSafety: initialControlSafety(),
    // 这些原有用例分别验证遥测/急停；执行侧租约单独提供已确认前提。
    controlLease: { ...initialControlLease(), status: 'active', sessionId: 'gripper-ui-test', expiresAt: performance.now() + 2500 },
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    frame: { ...initialState.frame, halOk: true, wsOk: true, forceStatus: { safety: { latched: false } } },
    logs: [],
  })
})

afterEach(() => {
  cleanup()
  useTelemetryStore.setState(initialState, true)
})

function renderCard(kind: 'settings' | 'manual', enabled = false) {
  const config = structuredClone(defaultConfig)
  config.gripper.rightEnabled = enabled
  const issue = vi.fn()
  const comparison = vi.fn<(value: PendingComparison) => void>()
  const common = { side: 'left' as const, config, updateConfig: vi.fn(), currentMm: 0, issueManualGripperMove: issue, requestComparison: comparison }
  if (kind === 'settings') render(<GripperCard {...common} focusHash="" injectLog={vi.fn()} />)
  else render(<ManualGripperControl {...common} />)
  return { issue, comparison }
}

describe.each(['settings', 'manual'] as const)('%s 夹爪卡的安全操作边界', (kind) => {
  it.each(['pending', 'expired'] as const)('真实模式租约 %s 时拒绝启动动作并保留停止', (status) => {
    useTelemetryStore.setState({ controlLease: { ...initialControlLease(), status } })
    const { issue } = renderCard(kind)
    for (const name of [kind === 'settings' ? '手动下发使能' : '使能', '执行目标', '打开', '闭合', '回零']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
      fireEvent.click(screen.getByRole('button', { name }))
    }
    expect(issue).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('租约')
    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(issue).toHaveBeenCalledWith('right', 'stop', undefined)
  })

  it.each(['emergency', 'force', 'offline'] as const)('%s 时禁用启动动作并显示原因，保留停止', (mode) => {
    useTelemetryStore.setState((state) => ({
      controlSafety: { ...state.controlSafety, emergencyRequested: mode === 'emergency' },
      telemetryLink: { state: mode === 'offline' ? 'offline' : 'live', lastFrameReceivedAt: Date.now() },
      frame: { ...state.frame, forceStatus: { safety: { latched: mode === 'force', reason: '力安全锁存' } } },
    }))
    const { issue } = renderCard(kind)
    for (const name of [kind === 'settings' ? '手动下发使能' : '使能', '执行目标', '打开', '闭合', '回零']) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(issue).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent(mode === 'emergency' ? '急停保护尚未确认解除' : mode === 'force' ? '力安全锁存' : '缺少新鲜的硬件遥测')
    const stop = screen.getByRole('button', { name: '停止' })
    expect(stop).toBeEnabled()
    fireEvent.click(stop)
    expect(issue).toHaveBeenCalledWith('right', 'stop', undefined)
  })

  it('保护状态仍允许断使能', () => {
    useTelemetryStore.setState({ controlSafety: { ...initialControlSafety(), emergencyRequested: true } })
    const { issue } = renderCard(kind, true)
    const disable = screen.getByRole('button', { name: kind === 'settings' ? '手动断使能' : '断使能' })
    expect(disable).toBeEnabled()
    fireEvent.click(disable)
    expect(issue).toHaveBeenCalledWith('right', 'disable', undefined)
  })

  it.each(['emergency', 'lease'] as const)('目标确认弹窗打开后 %s，不再下发旧目标', (cause) => {
    const { issue, comparison } = renderCard(kind)
    fireEvent.click(screen.getByRole('button', { name: '执行目标' }))
    act(() => useTelemetryStore.setState(cause === 'emergency'
      ? { controlSafety: { ...initialControlSafety(), emergencyRequested: true } }
      : { controlLease: initialControlLease() }))
    comparison.mock.calls[0][0].onConfirm()
    expect(issue).not.toHaveBeenCalled()
    expect(useTelemetryStore.getState().logs.at(-1)?.msg).toContain('夹爪操作受阻')
  })

  it.each(['emergency', 'force'] as const)('mock 可跳过实时连接要求，但不能跳过 %s 锁存', (mode) => {
    apiMode.mock = true
    useTelemetryStore.setState({
      controlLease: initialControlLease(false),
      telemetryLink: { state: 'offline', lastFrameReceivedAt: null },
    })
    const { issue } = renderCard(kind)
    const open = screen.getByRole('button', { name: '打开' })
    expect(open).toBeEnabled()
    fireEvent.click(open)
    expect(issue).toHaveBeenCalledWith('right', 'open', undefined)
    act(() => useTelemetryStore.setState((state) => ({
      controlSafety: { ...initialControlSafety(), emergencyRequested: mode === 'emergency' },
      frame: { ...state.frame, forceStatus: { safety: { latched: mode === 'force' } } },
    })))
    expect(open).toBeDisabled()
  })
})
