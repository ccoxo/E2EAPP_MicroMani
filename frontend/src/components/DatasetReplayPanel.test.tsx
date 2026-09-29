import type { Participation } from '../types'
const dualParticipation: Participation = { version: 'appstation.participation.v1', arms: ['left', 'right'], grippers: ['left', 'right'] }
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

const timing = {
  requestedSpeed: .25, plannedDurationS: 12.4, plannedAverageSpeed: .201, minimumSpeed: .08,
  limitedFrames: 2, limitingChannels: ['右臂 Y'], gripperFeedbackLimited: true,
  profileSource: 'teleop', hardwareValidated: false,
  profile: { translationVelocityUiPerSec: 8000, rotationVelocityUiPerSec: 12, accTimeSec: .05, decTimeSec: .05 },
}

it('按选择的倍率规划并显示未验证的示教参数，改倍率需重新校验', async () => {
  vi.mocked(postCommand).mockResolvedValue({ data: { frames: 60, fps: 30, durationS: 2, timing } })
  render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="e" />)
  const inspect = screen.getByRole('button', { name: '校验回放数据' })
  await waitFor(() => expect(inspect).toBeEnabled())
  fireEvent.click(inspect)
  const plan = await screen.findByLabelText('回放时间规划')
  expect(postCommand).toHaveBeenCalledWith('/api/datasets/local/episodes/e/replay/inspect', { speed: .25, participation: dualParticipation })
  expect(plan).toHaveTextContent('预计 12.4 秒')
  expect(plan).toHaveTextContent('2 段延时，受限通道：右臂 Y')
  expect(plan).toHaveTextContent('当前示教配置 · 平移 8000 μm/s · 旋转 12°/s')
  expect(plan).toHaveTextContent('尚无逐轴及负载实机验收记录')
  fireEvent.click(screen.getByRole('checkbox', { name: /确认工作区/ }))
  fireEvent.click(screen.getByRole('radio', { name: '0.5 倍速' }))
  expect(screen.getByRole('button', { name: '对齐起点并真机回放' })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: /确认工作区/ })).not.toBeChecked()
  expect(screen.queryByLabelText('回放时间规划')).not.toBeInTheDocument()
})

it('跟随等待显示实际任务倍率、耗时及等待通道', async () => {
  vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ data: {
    phase: 'following', active: true, datasetId: 'local', episodeId: 'e', frame: 2, totalFrames: 60,
    elapsedS: 1.2, effectiveSpeed: .12, feedbackWaitS: .5, waitingChannels: ['右臂 Y'], timing,
  } }) } as Response)
  render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="e" />)
  await screen.findByText('等待设备跟随')
  expect(screen.getByText(/任务已运行/)).toHaveTextContent('实际平均 0.120× · 跟随等待 0.5 秒 · 等待：右臂 Y')
  expect(screen.getByLabelText('回放时间规划')).toHaveTextContent('请求 0.25×')
  expect(screen.getByRole('button', { name: '停止真机回放' })).toBeEnabled()
})

it('不把其他片段的运行计划当成所选片段的规划', async () => {
  vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ data: {
    phase: 'running', active: true, datasetId: 'other', episodeId: 'old', frame: 2, totalFrames: 60, timing,
  } }) } as Response)
  render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="new" />)
  await screen.findByText('执行片段：other / old')
  expect(screen.queryByLabelText('回放时间规划')).not.toBeInTheDocument()
})

it('切换片段后迟到的校验响应不能解除新片段的门控', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(postCommand).mockReturnValue(new Promise((done) => { resolve = done }) as ReturnType<typeof postCommand>)
  const { rerender } = render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="old" />)
  const inspect = screen.getByRole('button', { name: '校验回放数据' })
  await waitFor(() => expect(inspect).toBeEnabled())
  fireEvent.click(inspect)
  rerender(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="new" />)
  resolve({ data: { frames: 60, fps: 30, durationS: 2, timing } })
  await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
  expect(screen.queryByLabelText('回放时间规划')).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: /确认工作区/ })).toBeDisabled()
})

describe('DatasetReplayPanel', () => {
  it('requires validation and explicit motion confirmation before start', async () => {
    vi.mocked(postCommand).mockResolvedValueOnce({ data: { frames: 30, fps: 30, durationS: 1 } })
      .mockResolvedValueOnce({ data: { phase: 'loading', active: true, frame: 0, totalFrames: 30 } })
    render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="episode_000001" />)
    const start = screen.getByRole('button', { name: '对齐起点并真机回放' })
    expect(start).toBeDisabled()
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    await screen.findByText('30 帧 · 30 Hz · 1.0 秒')
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /确认工作区/ }))
    fireEvent.click(start)
    await waitFor(() => expect(postCommand).toHaveBeenLastCalledWith(
      '/api/datasets/local/episodes/episode_000001/replay/start', { speed: .25, confirmMotion: true, participation: dualParticipation }))
    expect(screen.getByRole('button', { name: '停止真机回放' })).toBeEnabled()
  })

  it('shows validation rejection without allowing motion', async () => {
    vi.mocked(postCommand).mockRejectedValueOnce(new Error('原点不一致'))
    render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="episode" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('原点不一致')
    expect(screen.getByRole('button', { name: '对齐起点并真机回放' })).toBeDisabled()
  })

  it('collapses technical errors behind a readable message', async () => {
    const detail = `REPLAY_REJECTED: ${'dataset path /'.repeat(30)} Parquet magic bytes not found in footer`
    vi.mocked(postCommand).mockRejectedValueOnce(new Error(detail))
    render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="episode" />)
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
    render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="episode" />)
    fireEvent.click(screen.getByRole('radio', { name: '0.5 倍速' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '校验回放数据' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '校验回放数据' }))
    await screen.findByText('30 帧 · 30 Hz · 1.0 秒')
    fireEvent.click(screen.getByRole('checkbox', { name: /确认工作区/ }))
    fireEvent.click(screen.getByRole('button', { name: '对齐起点并真机回放' }))
    await waitFor(() => expect(postCommand).toHaveBeenLastCalledWith(
      '/api/datasets/local/episodes/episode/replay/start', { speed: .5, confirmMotion: true, participation: dualParticipation }))
    expect(screen.getByRole('radio', { name: '0.5 倍速' })).toBeDisabled()
  })

  it('can stop an active replay belonging to another selected episode', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ data: {
      phase: 'running', active: true, frame: 2, totalFrames: 30, datasetId: 'other', episodeId: 'old',
    } }) } as Response)
    vi.mocked(postCommand).mockResolvedValue({ data: { phase: 'stopped', active: false, frame: 2, totalFrames: 30 } })
    render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="new" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '停止真机回放' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '停止真机回放' }))
    await waitFor(() => expect(postCommand).toHaveBeenCalledWith('/api/replay/stop', {}))
  })
})

it('requires explicit legacy selection and resets validation after changing devices', async () => {
  vi.mocked(postCommand).mockResolvedValue({ data: { frames: 30, fps: 30, durationS: 1 } })
  render(<DatasetReplayPanel datasetId="legacy" episodeId="episode" />)
  const inspect = screen.getByRole('button', { name: '校验回放数据' })
  expect(inspect).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: '左臂' }))
  await waitFor(() => expect(inspect).toBeEnabled())
  fireEvent.click(inspect)
  await screen.findByText('30 帧 · 30 Hz · 1.0 秒')
  expect(postCommand).toHaveBeenLastCalledWith('/api/datasets/legacy/episodes/episode/replay/inspect', {
    speed: .25, participation: { version: 'appstation.participation.v1', arms: ['left'], grippers: [] },
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /确认工作区/ }))
  expect(screen.getByRole('button', { name: '对齐起点并真机回放' })).toBeEnabled()
  fireEvent.click(screen.getByRole('checkbox', { name: '左夹爪参与' }))
  expect(screen.getByRole('button', { name: '对齐起点并真机回放' })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: /确认工作区/ })).not.toBeChecked()
})


it('对齐超时直接展开未到位通道，不被长错误提示折叠', async () => {
  const detail = '目标位置对齐超时（30 秒，起点对齐）；以下参与通道未到位：\n'
    + '操作者右臂（硬件left）X：目标 100 μm，实际 75 μm，误差 25 μm，允许 10 μm\n'
    + '操作者右夹爪（硬件left）开口：目标 5 mm，实际 4 mm，误差 1 mm，允许 0.2 mm'
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: {
    phase: 'failed', active: false, frame: 0, totalFrames: 661, error: detail,
  } }) }))
  render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="episode" />)
  const alert = await screen.findByRole('alert')
  expect(alert.querySelector('details')).toHaveAttribute('open')
  expect(alert.querySelector('pre')).toHaveTextContent('误差 25 μm，允许 10 μm')
  expect(alert.querySelector('pre')).toHaveTextContent('右夹爪')
  expect(postCommand).not.toHaveBeenCalled()
})

it('运行超限直接展开故障帧、目标反馈和采样时间', async () => {
  const detail = '实际位置偏离回放目标，已停止（跟踪偏差超限）\n'
    + '比较目标：第 48 帧 action；准备下发第 49 帧\n'
    + '操作者右臂（硬件left）Roll [超限]：目标 2.4516 °，实际 1.1 °，误差 1.3516 °，允许 1 °；反馈时间 2026-09-21T15:30:14.920+08:00'
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: {
    phase: 'failed', active: false, frame: 48, totalFrames: 983, error: detail,
  } }) }))
  render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="episode" />)
  const alert = await screen.findByRole('alert')
  expect(alert.querySelector('details')).toHaveAttribute('open')
  expect(alert.querySelector('strong')).toHaveTextContent(detail.split('\n')[0])
  expect(alert.querySelector('pre')).toHaveTextContent('第 48 帧 action；准备下发第 49 帧')
  expect(alert.querySelector('pre')).toHaveTextContent('误差 1.3516 °，允许 1 °')
  expect(alert.querySelector('pre')).toHaveTextContent('反馈时间 2026-09-21T15:30:14.920+08:00')
  expect(postCommand).not.toHaveBeenCalled()
})

it('shows final gripper warning alongside completed status without a failure alert', async () => {
  const warning = '末帧夹爪未到位（仅提示，不判定夹取成功或失败）：右夹爪误差 3.3 mm'
  vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ data: {
    phase: 'completed', active: false, frame: 3, totalFrames: 3, datasetId: 'local', episodeId: 'e', warning,
  } }) } as Response)
  render(<DatasetReplayPanel recordedParticipation={dualParticipation} datasetId="local" episodeId="e" />)
  expect(await screen.findByText(warning)).toBeInTheDocument()
  expect(screen.getByText('已完成')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
