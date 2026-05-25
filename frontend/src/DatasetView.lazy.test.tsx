import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DatasetApi, DatasetEpisodeApi } from './types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

function ok(data: unknown): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify({ ok: true, data, ts: Date.now() }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

describe('DatasetView backend datasets', () => {
  it('loads episode samples from the detail endpoint after listing metadata', async () => {
    vi.resetModules()
    vi.stubEnv('MODE', 'development')

    const summaryEpisode: DatasetEpisodeApi = {
      id: 'episode_000001',
      name: 'Episode 1',
      task: 'assembly',
      status: 'review',
      quality: 92,
      frames: 12,
      fps: 30,
      durationS: 0.4,
      createdAt: 1500,
      warnings: [],
      samples: [],
    }
    const detailEpisode: DatasetEpisodeApi = {
      ...summaryEpisode,
      samples: [
        {
          frame: 0,
          leftJoints: [1, 2, 3, 4, 5, 6],
          rightJoints: [7, 8, 9, 10, 11, 12],
          forceLeft: [1, 0, 0, 0, 0, 0],
          forceRight: [2, 0, 0, 0, 0, 0],
          images: { global: '/frame.png' },
        },
      ],
    }
    const dataset: DatasetApi = {
      id: 'unit_dataset',
      name: 'Unit Dataset',
      status: 'local',
      root: 'E:/data group/unit_dataset',
      fps: 30,
      format: 'lerobot-v3-native',
      episodes: [summaryEpisode],
    }
    const fetchMock = vi.fn((input: unknown) => {
      const url = String(input)
      if (url.endsWith('/api/datasets')) return ok({ datasets: [dataset] })
      if (url.includes('/api/datasets/unit_dataset/episodes/episode_000001')) {
        return ok({ episode: detailEpisode })
      }
      if (url.includes('/api/cameras/') && url.includes('/snapshot')) {
        return Promise.resolve(new Response('jpeg', { status: 200, headers: { 'Content-Type': 'image/jpeg' } }))
      }
      return Promise.resolve(new Response('{}', { status: 404 }))
    })
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:live-frame'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })

    const { DatasetView } = await import('./views/DatasetView')
    render(<DatasetView />)

    await waitFor(() => expect(screen.getAllByText('Unit Dataset').length).toBeGreaterThan(0))
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/datasets/unit_dataset/episodes/episode_000001'),
      )
    })
    await waitFor(() => {
      expect(document.querySelector('img.dataset-video-image')?.getAttribute('src')).toBe('/frame.png')
    })
    await waitFor(() => expect(screen.getAllByTestId(/^camera-image-/).length).toBeGreaterThan(0))
  })
})
