import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { useTelemetryStore } from '../../stores/telemetry'
import type { DatasetApi, DatasetEpisodeApi } from '../../types'
import EpisodeHistoryCard from './EpisodeHistoryCard'

const initialRecordSession = structuredClone(useTelemetryStore.getState().recordSession)

function episode(
  id: string,
  createdAt: number,
  status: DatasetEpisodeApi['status'] = 'valid',
): DatasetEpisodeApi {
  return {
    id,
    name: id,
    task: 'assembly',
    status,
    quality: 90,
    frames: 300,
    fps: 30,
    durationS: 10,
    createdAt,
    warnings: [],
    samples: [],
  }
}

function dataset(id: string, episodes: DatasetEpisodeApi[]): DatasetApi {
  return {
    id,
    name: id,
    status: 'local',
    fps: 30,
    format: 'lerobot-v3-native',
    episodes,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useTelemetryStore.setState({ recordSession: structuredClone(initialRecordSession) })
})

describe('EpisodeHistoryCard', () => {
  it('loads the current dataset history from the backend in newest-first order', async () => {
    useTelemetryStore.setState((state) => ({
      recordSession: { ...state.recordSession, datasetName: 'micro_assembly_v1', episodeHistory: [] },
    }))
    const fetchSpy = vi.spyOn(api, 'fetchDatasets').mockResolvedValue([
      dataset('other_dataset', [episode('episode_000009', 400)]),
      dataset('micro_assembly_v1', [
        episode('episode_000000', 100),
        episode('episode_000002', 300, 'review'),
        episode('episode_000001', 200),
      ]),
    ])

    render(
      <MemoryRouter>
        <EpisodeHistoryCard />
      </MemoryRouter>,
    )

    expect(screen.getByText('正在读取录制历史…')).toBeInTheDocument()
    expect(await screen.findByText('#002')).toBeInTheDocument()
    const labels = screen.getAllByText(/^#\d{3}$/).map((node) => node.textContent)
    expect(labels).toEqual(['#002', '#001', '#000'])
    expect(screen.getByText('待复核')).toBeInTheDocument()
    expect(screen.queryByText('#009')).not.toBeInTheDocument()
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('prefers live records over backend duplicates', async () => {
    useTelemetryStore.setState((state) => ({
      recordSession: {
        ...state.recordSession,
        datasetName: 'micro_assembly_v1',
        episodeHistory: [{
          index: 7,
          frameCount: 321,
          durationS: 10.7,
          status: 'ok',
          maxForceLeft: 2.1,
          maxForceRight: 2.3,
          lateFrames: 0,
          cameraDrops: { global: 0, wristLeft: 0, wristRight: 0 },
        }],
      },
    }))
    vi.spyOn(api, 'fetchDatasets').mockResolvedValue([
      dataset('micro_assembly_v1', [episode('episode_000007', 300), episode('episode_000006', 200)]),
    ])

    render(
      <MemoryRouter>
        <EpisodeHistoryCard />
      </MemoryRouter>,
    )

    expect(await screen.findByText('#006')).toBeInTheDocument()
    expect(screen.getAllByText('#007')).toHaveLength(1)
    expect(screen.getByText('321 帧 / 10.7s')).toBeInTheDocument()
  })

  it('keeps live records when loading the backend history fails', async () => {
    useTelemetryStore.setState((state) => ({
      recordSession: {
        ...state.recordSession,
        datasetName: 'micro_assembly_v1',
        episodeHistory: [{
          index: 7,
          frameCount: 321,
          durationS: 10.7,
          status: 'ok',
          maxForceLeft: 2.1,
          maxForceRight: 2.3,
          lateFrames: 0,
          cameraDrops: { global: 0, wristLeft: 0, wristRight: 0 },
        }],
      },
    }))

    vi.spyOn(api, 'fetchDatasets').mockRejectedValueOnce(new Error('offline'))
    render(
      <MemoryRouter>
        <EpisodeHistoryCard />
      </MemoryRouter>,
    )

    expect(await screen.findByText('后端历史读取失败，仅显示本次会话记录')).toBeInTheDocument()
    expect(screen.getByText('#007')).toBeInTheDocument()
  })
})
