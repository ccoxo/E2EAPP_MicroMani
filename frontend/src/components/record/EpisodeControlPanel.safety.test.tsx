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
