import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useTelemetryStore as store } from '../../stores/telemetry'
import EpisodeControlPanel from './EpisodeControlPanel'

const initial = store.getState()
afterEach(() => { cleanup(); store.setState(initial, true) })

it('区分编号、本次和累计，按累计保留数显示目标进度', () => {
  store.setState({ recordSession: { ...initial.recordSession, phase: 'resetting',
    currentEpisode: 31, savedEpisodes: 29, sessionSavedEpisodes: 9, targetEpisodes: 50 } })
  const { container } = render(<EpisodeControlPanel onStartSession={vi.fn()} />)
  expect(screen.getByText('片段 #031')).toBeInTheDocument()
  expect(screen.getByText('目标 50 条 / 累计保留 29 条 / 本次保留 9 条')).toBeInTheDocument()
  expect(container.querySelector('.ui-progress-fill')).toHaveStyle({ width: '58%' })
})
