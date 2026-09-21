import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { DatasetApi, DatasetEpisodeApi } from '../types'
import { DatasetView } from './DatasetView'

vi.mock('../api', async (importOriginal) => ({ ...await importOriginal<typeof import('../api')>(), mockMode: false }))

const episode: DatasetEpisodeApi = { id: 'episode_000001', name: 'Episode 1', task: 'test', status: 'review', quality: 90, frames: 12, fps: 30, durationS: 0.4, createdAt: 0, warnings: [], samples: [
  { frame: 0, leftJoints: [1, 2, 3, 4, 5, 6], rightJoints: [1, 2, 3, 4, 5, 6], forceLeft: [0, 0, 0, 0, 0, 0], forceRight: [0, 0, 0, 0, 0, 0] },
] }
const datasets: DatasetApi[] = ['A', 'B'].map((id) => ({ id, name: `Dataset ${id}`, status: 'local', root: 'fixture', fps: 30, format: 'lerobot-v3-native', episodes: [{ ...episode }] }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.spyOn(api, 'fetchDatasets').mockResolvedValue(structuredClone(datasets))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) =>
    url.endsWith('/api/replay/status')
      ? new Response(JSON.stringify({ data: { active: false, phase: 'idle', frame: 0, totalFrames: 0 } }))
      : new Response('{}', { status: 503 })))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function mount() {
  render(<DatasetView />)
  await waitFor(() => expect(document.querySelectorAll('.dataset-row-button')).toHaveLength(2))
  return within(document.querySelector<HTMLElement>('.dataset-browser')!)
}

describe('真实数据集页面的请求结果归属', () => {
  it.each(['dataset', 'episode'] as const)('%s 删除失败保留原数据', async (kind) => {
    if (kind === 'dataset') vi.spyOn(api, 'deleteDatasetApi').mockRejectedValue(new Error('删除拒绝'))
    else vi.spyOn(api, 'deleteDatasetEpisodeApi').mockRejectedValue(new Error('删除拒绝'))
    const browser = await mount()
    fireEvent.click(screen.getByRole('button', { name: kind === 'dataset' ? '删除' : '删除本条' }))
    await screen.findByText('后端数据异常')
    expect(browser.getByRole('button', { name: /Dataset A/ })).toBeInTheDocument()
    expect(browser.getByRole('button', { name: /Episode 1/ })).toBeInTheDocument()
  })

  it('删除 A 的同名 episode 不隐藏 B 的数据', async () => {
    vi.spyOn(api, 'deleteDatasetEpisodeApi').mockResolvedValue({ ok: true })
    const browser = await mount()
    fireEvent.click(screen.getByRole('button', { name: '删除本条' }))
    await waitFor(() => expect(api.deleteDatasetEpisodeApi).toHaveBeenCalledWith('A', 'episode_000001'))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(browser.getByRole('button', { name: /Dataset B/ }))
    expect(browser.getByRole('button', { name: /Episode 1/ })).toBeInTheDocument()
  })

  it('A 的复核状态不污染 B 的同名 episode', async () => {
    vi.spyOn(api, 'updateDatasetEpisodeApi').mockResolvedValue({ ok: true })
    const browser = await mount()
    fireEvent.click(screen.getByRole('button', { name: '标记有效' }))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(browser.getByRole('button', { name: /Dataset B/ }))
    expect(browser.getByRole('button', { name: /Episode 1/ })).toHaveTextContent('待复核')
  })

  it('复核请求失败不显示已生效的状态', async () => {
    vi.spyOn(api, 'updateDatasetEpisodeApi').mockRejectedValue(new Error('复核拒绝'))
    const browser = await mount()
    fireEvent.click(screen.getByRole('button', { name: '标记有效' }))
    await screen.findByRole('alert')
    expect(browser.getByRole('button', { name: /Episode 1/ })).toHaveTextContent('待复核')
  })

  it('A 的重命名不污染 B 的同名 episode', async () => {
    vi.spyOn(api, 'updateDatasetEpisodeApi').mockResolvedValue({ ok: true })
    const browser = await mount()
    fireEvent.click(screen.getByRole('button', { name: '重命名本条' }))
    const dialog = within(screen.getByRole('dialog', { name: '重命名' }))
    fireEvent.change(dialog.getByRole('textbox'), { target: { value: 'A renamed' } })
    fireEvent.click(dialog.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(browser.getByRole('button', { name: /A renamed/ })).toBeInTheDocument())
    fireEvent.click(browser.getByRole('button', { name: /Dataset B/ }))
    expect(browser.getByRole('button', { name: /Episode 1/ })).toBeInTheDocument()
  })

  it('删除完成前保留条目并阻止重复提交', async () => {
    const command = deferred<{ ok: boolean }>()
    const remove = vi.spyOn(api, 'deleteDatasetEpisodeApi').mockReturnValue(command.promise)
    const browser = await mount()
    const button = screen.getByRole('button', { name: '删除本条' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(button).toBeDisabled()
    expect(remove).toHaveBeenCalledTimes(1)
    expect(browser.getByRole('button', { name: /Episode 1/ })).toBeInTheDocument()
    await act(async () => command.resolve({ ok: true }))
    fireEvent.click(browser.getByRole('button', { name: /Dataset A/ }))
    expect(browser.queryByRole('button', { name: /Episode 1/ })).not.toBeInTheDocument()
  })

  it('切页卸载后完成的修改不再续发列表请求', async () => {
    const command = deferred<{ ok: boolean }>()
    vi.spyOn(api, 'deleteDatasetEpisodeApi').mockReturnValue(command.promise)
    await mount()
    fireEvent.click(screen.getByRole('button', { name: '删除本条' }))
    cleanup()
    await act(async () => command.resolve({ ok: true }))
    expect(api.fetchDatasets).toHaveBeenCalledTimes(1)
  })

  it('详情读取失败后重选同一条可再次读取', async () => {
    vi.mocked(api.fetchDatasets).mockResolvedValue(datasets.map((dataset) => ({ ...dataset, episodes: [{ ...episode, samples: [] }] })))
    const detailSpy = vi.spyOn(api, 'fetchDatasetEpisodeApi')
      .mockRejectedValueOnce(new Error('详情暂时断连'))
      .mockResolvedValue(episode)
    const browser = await mount()
    await screen.findByText('后端数据异常')
    fireEvent.click(browser.getByRole('button', { name: /Dataset B/ }))
    await waitFor(() => expect(detailSpy).toHaveBeenCalledWith('B', 'episode_000001'))
    await act(async () => { await Promise.resolve() })
    fireEvent.click(browser.getByRole('button', { name: /Dataset A/ }))
    await waitFor(() => expect(detailSpy.mock.calls.filter(([id]) => id === 'A')).toHaveLength(2))
  })

  it('迟到的 A 详情错误不覆盖已经读取成功的 B', async () => {
    vi.mocked(api.fetchDatasets).mockResolvedValue(datasets.map((dataset) => ({ ...dataset, episodes: [{ ...episode, samples: [] }] })))
    const previous = deferred<DatasetEpisodeApi | null>()
    const detail = vi.spyOn(api, 'fetchDatasetEpisodeApi').mockReturnValueOnce(previous.promise).mockResolvedValue(episode)
    const browser = await mount()
    fireEvent.click(browser.getByRole('button', { name: /Dataset B/ }))
    await waitFor(() => expect(detail).toHaveBeenCalledWith('B', 'episode_000001'))
    await act(async () => { await Promise.resolve() })
    await act(async () => previous.reject(new Error('A 迟到失败')))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(browser.getByRole('button', { name: /Episode 1/ })).toBeInTheDocument()
  })
})
