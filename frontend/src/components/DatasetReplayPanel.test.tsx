import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DatasetReplayPanel } from './DatasetReplayPanel'
import { postCommand } from '../api'

vi.mock('../api', () => ({ apiBase: 'http://localhost', mockMode: false, postCommand: vi.fn() }))
beforeEach(() => {
  vi.mocked(postCommand).mockReset()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: { phase: 'idle', active: false, frame: 0, totalFrames: 0 } }) }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('DatasetReplayPanel', () => {
  it('requires validation and explicit motion confirmation before start', async () => {
    vi.mocked(postCommand).mockResolvedValueOnce({ data: { frames: 30, fps: 30, durationS: 1 } })
      .mockResolvedValueOnce({ data: { phase: 'loading', active: true, frame: 0, totalFrames: 30 } })
    render(<DatasetReplayPanel datasetId="local" episodeId="episode_000001" />)
    const start = screen.getByRole('button', { name: '对齐起点并真机回放' })
    expect(start).toBeDisabled()
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    await screen.findByText('30 帧 · 30 Hz · 1.0 秒')
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(start)
    await waitFor(() => expect(postCommand).toHaveBeenLastCalledWith(
      '/api/datasets/local/episodes/episode_000001/replay/start', { speed: .25, confirmMotion: true }))
    expect(screen.getByRole('button', { name: '停止真机回放' })).toBeEnabled()
  })

  it('shows validation rejection without allowing motion', async () => {
    vi.mocked(postCommand).mockRejectedValueOnce(new Error('原点不一致'))
    render(<DatasetReplayPanel datasetId="local" episodeId="episode" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('原点不一致')
    expect(screen.getByRole('button', { name: '对齐起点并真机回放' })).toBeDisabled()
  })

  it('collapses technical errors behind a readable message', async () => {
    const detail = `REPLAY_REJECTED: ${'dataset path /'.repeat(30)} Parquet magic bytes not found in footer`
    vi.mocked(postCommand).mockRejectedValueOnce(new Error(detail))
    render(<DatasetReplayPanel datasetId="local" episodeId="episode" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('数据文件无法读取，请检查 Parquet 文件是否完整。')
    const details = screen.getByText('查看错误详情').closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(details).toHaveTextContent(detail)
    expect(screen.getByRole('button', { name: '对齐起点并真机回放' })).toBeDisabled()
  })

  it('sends the selected speed and locks speed controls during replay', async () => {
    vi.mocked(postCommand).mockResolvedValueOnce({ data: { frames: 30, fps: 30, durationS: 1 } })
      .mockResolvedValueOnce({ data: { phase: 'running', active: true, frame: 1, totalFrames: 30 } })
    render(<DatasetReplayPanel datasetId="local" episodeId="episode" />)
    fireEvent.click(screen.getByRole('radio', { name: '0.5 倍速' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    await screen.findByText('30 帧 · 30 Hz · 1.0 秒')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: '对齐起点并真机回放' }))
    await waitFor(() => expect(postCommand).toHaveBeenLastCalledWith(
      '/api/datasets/local/episodes/episode/replay/start', { speed: .5, confirmMotion: true }))
    expect(screen.getByRole('radio', { name: '0.5 倍速' })).toBeDisabled()
  })

  it('can stop an active replay belonging to another selected episode', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ data: {
      phase: 'running', active: true, frame: 2, totalFrames: 30, datasetId: 'other', episodeId: 'old',
    } }) } as Response)
    vi.mocked(postCommand).mockResolvedValue({ data: { phase: 'stopped', active: false, frame: 2, totalFrames: 30 } })
    render(<DatasetReplayPanel datasetId="local" episodeId="new" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '停止真机回放' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '停止真机回放' }))
    await waitFor(() => expect(postCommand).toHaveBeenCalledWith('/api/replay/stop', {}))
  })
})
