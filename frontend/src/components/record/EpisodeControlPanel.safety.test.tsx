import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useTelemetryStore } from '../../stores/telemetry'
import EpisodeControlPanel from './EpisodeControlPanel'

const initial = useTelemetryStore.getState()
afterEach(() => { cleanup(); useTelemetryStore.setState(initial, true); window.sessionStorage.clear() })

it('急停后保留中断片段的保存入口，不投影成空闲会话', async () => {
  useTelemetryStore.setState({ recordSession: { ...initial.recordSession, phase: 'recording', recorderElapsedS: 3 } })
  await act(async () => { useTelemetryStore.getState().triggerEmergencyStop() })
  expect(useTelemetryStore.getState().recordSession.phase).toBe('interrupted')
  expect(useTelemetryStore.getState().recordSession.recorderElapsedS).toBe(3)
  const save = vi.fn()
  useTelemetryStore.setState({ saveRecordEpisode: save })
  render(<EpisodeControlPanel onStartSession={vi.fn()} />)
  expect(screen.getAllByText(/采集中断/).length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: /保存/ }))
  expect(save).toHaveBeenCalledOnce()
})

it('跳过复位被后端拒绝时在复位面板显示原因和张开夹爪入口', () => {
  const open = vi.fn()
  useTelemetryStore.setState({
    issueManualGripperMove: open,
    recordSession: {
      ...initial.recordSession,
      phase: 'resetting',
      resetReady: true,
      participation: { version: 'appstation.participation.v1', arms: ['right'], grippers: ['right'] },
      startError: '录制起点未连续稳定 300 ms：操作者右夹爪目标与实际开口需接近 26 mm',
    },
  })
  render(<EpisodeControlPanel onStartSession={vi.fn()} />)
  expect(screen.getByRole('alert').textContent).toContain('未能开始下一条')
  expect(screen.getByRole('alert').textContent).toContain('需接近 26 mm')
  fireEvent.click(screen.getByRole('button', { name: '张开夹爪' }))
  expect(open).toHaveBeenCalledOnce()
})

it('开始采集会话被起点检查拒绝时同样提供张开夹爪入口', () => {
  const open = vi.fn()
  useTelemetryStore.setState({
    issueManualGripperMove: open,
    recordSession: {
      ...initial.recordSession,
      phase: 'idle',
      participation: { version: 'appstation.participation.v1', arms: ['right'], grippers: ['right'] },
      startError: '录制起点未连续稳定 300 ms：操作者右夹爪目标与实际开口需接近 26 mm（主手目标 26.0 mm，实际 1.7 mm）',
    },
  })
  render(<EpisodeControlPanel onStartSession={vi.fn()} />)
  expect(screen.getByRole('alert').textContent).toContain('录制启动失败')
  fireEvent.click(screen.getByRole('button', { name: '张开夹爪' }))
  expect(open).toHaveBeenCalledOnce()
})
