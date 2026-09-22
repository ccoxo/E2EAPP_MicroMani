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


async function mountPreview() {
  const items = datasets.map((dataset) => ({ ...dataset, episodes: [{ ...episode, frames: 4,
    samples: [0, 1, 2, 3].map((frame) => ({ ...episode.samples![0], frame, images: {
      global: `/api/${dataset.id}/global/${frame}`, wrist_left: `/api/${dataset.id}/left/${frame}`,
      wrist_right: `/api/${dataset.id}/right/${frame}`,
    } })),
  }] }))
  vi.mocked(api.fetchDatasets).mockResolvedValue(items)
  const browser = await mount()
  await waitFor(() => expect(document.querySelectorAll('.dataset-video-image').length).toBeGreaterThanOrEqual(3))
  return browser
}
const previewImages = (frame: number, dataset = 'A') => Array.from(document.querySelectorAll<HTMLImageElement>('.dataset-video-image'))
  .filter((image) => image.src.includes(`/api/${dataset}/`) && image.src.endsWith(`/${frame}`))
const visibleImages = () => Array.from(document.querySelectorAll<HTMLImageElement>('.dataset-video-image'))
  .filter((image) => image.style.visibility === 'visible')
const slider = () => document.querySelector<HTMLInputElement>('.dataset-frame-slider')!
async function loadGroup(frame: number, dataset = 'A') {
  await act(async () => { for (const image of previewImages(frame, dataset)) fireEvent.load(image) })
}
async function tick() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) }) }

it('预加载下一组三路，慢加载保持旧画面，解码完成后原节点一起显示', async () => {
  await mountPreview()
  expect(previewImages(1)).toHaveLength(3)
  await loadGroup(0)
  expect(visibleImages()).toHaveLength(3)
  const oldImages = visibleImages()
  const incoming = previewImages(1)
  let finishDecode!: () => void
  incoming[2].decode = vi.fn(() => new Promise<void>((resolve) => { finishDecode = resolve }))
  fireEvent.click(screen.getByRole('button', { name: /^播放$/ }))
  await tick()
  expect(slider().value).toBe('0')
  expect(visibleImages()).toEqual(oldImages)
  expect(screen.queryByText('正在加载画面…')).not.toBeInTheDocument()
  await loadGroup(1)
  await tick()
  expect(slider().value).toBe('0')
  expect(visibleImages()).toEqual(oldImages)
  await act(async () => finishDecode())
  await waitFor(() => expect(slider().value).toBe('1'))
  expect(visibleImages()).toEqual(incoming)
  expect(document.querySelectorAll('.dataset-video-image').length).toBeLessThanOrEqual(9)
})

it('预加载失败保留当前画面和进度，跳转新位置可继续', async () => {
  await mountPreview()
  await loadGroup(0)
  const previous = visibleImages()
  fireEvent.error(previewImages(1)[0])
  fireEvent.click(screen.getByRole('button', { name: /^播放$/ }))
  await tick()
  expect(slider().value).toBe('0')
  expect(visibleImages()).toEqual(previous)
  expect(screen.getByText(/图片加载失败/)).toBeInTheDocument()
  fireEvent.change(slider(), { target: { value: '3' } })
  await loadGroup(3)
  await waitFor(() => expect(slider().value).toBe('3'))
  expect(visibleImages()).toHaveLength(3)
  expect(visibleImages().every((image) => image.src.endsWith('/3'))).toBe(true)
})

it('切换数据集后迟到解码不恢复旧画面，卸载后不更新', async () => {
  const browser = await mountPreview()
  await loadGroup(0)
  const old = previewImages(1)[0]
  let finish!: () => void
  old.decode = vi.fn(() => new Promise<void>((resolve) => { finish = resolve }))
  fireEvent.load(old)
  fireEvent.click(browser.getByRole('button', { name: /Dataset B/ }))
  expect(visibleImages()).toHaveLength(0)
  await act(async () => finish())
  await loadGroup(0, 'B')
  expect(visibleImages()).toHaveLength(3)
  expect(visibleImages().every((image) => image.src.includes('/api/B/'))).toBe(true)
  const pending = previewImages(1, 'B')[0]
  let finishUnmounted!: () => void
  pending.decode = vi.fn(() => new Promise<void>((resolve) => { finishUnmounted = resolve }))
  fireEvent.load(pending)
  cleanup()
  await act(async () => finishUnmounted())
  expect(document.querySelectorAll('.dataset-video-image')).toHaveLength(0)
})


it('等待下一组时暂停，不被迟到加载推进；再次播放使用已预加载图片', async () => {
  await mountPreview()
  await loadGroup(0)
  fireEvent.click(screen.getByRole('button', { name: /^播放$/ }))
  await tick()
  fireEvent.click(screen.getByRole('button', { name: /^暂停$/ }))
  await loadGroup(1)
  await tick()
  expect(slider().value).toBe('0')
  fireEvent.click(screen.getByRole('button', { name: /^播放$/ }))
  await waitFor(() => expect(slider().value).toBe('1'))
  expect(visibleImages()).toHaveLength(3)
})
