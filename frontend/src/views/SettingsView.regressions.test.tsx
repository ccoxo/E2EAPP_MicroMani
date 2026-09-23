import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { defaultConfig } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import { initialControlSafety } from '../utils/controlSafety'
import { SettingsView } from './SettingsView'

const initialFrame = structuredClone(useTelemetryStore.getState().frame)
const snapshotActions = {
  applyParameterSnapshot: useTelemetryStore.getState().applyParameterSnapshot,
  deleteParameterSnapshot: useTelemetryStore.getState().deleteParameterSnapshot,
}

function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (error: Error) => void
  const promise = new Promise((finish, fail) => { resolve = finish; reject = fail })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.spyOn(api, 'fetchMotionOrigin').mockResolvedValue({ ok: true, data: { origin: defaultConfig.motion.origin } })
  useTelemetryStore.setState({
    config: structuredClone(defaultConfig), frame: structuredClone(initialFrame),
    history: [], logs: [], parameterSnapshots: [],
    controlSafety: initialControlSafety(),
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    ...snapshotActions,
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  useTelemetryStore.setState(snapshotActions)
  window.localStorage.clear()
})

async function show(hash: string) {
  await act(async () => {
    render(<MemoryRouter initialEntries={['/settings#' + hash]}><SettingsView /></MemoryRouter>)
  })
}

function returnButton(side: 'left' | 'right') {
  return within(document.getElementById(`teleop-${side}`)!).getByRole('button', { name: '回工作原点' })
}

describe('设置迁移回归', () => {
  it('主手后台连接在物理反馈到达后解除等待，后续掉线仍可断开', async () => {
    const connect = vi.spyOn(api, 'connectTeleopHand').mockResolvedValue({
      data: { connected: true, backgroundSync: true, physicalConnected: false },
    })
    useTelemetryStore.setState((state) => ({
      config: { ...state.config, teleop: { ...state.config.teleop, homeBeforeStart: false } },
    }))
    await show('teleop-left')
    const left = within(document.getElementById('teleop-left')!)
    await act(async () => { fireEvent.click(left.getByRole('button', { name: '连接主手' })) })
    expect(connect).toHaveBeenCalledWith('left')
    expect(left.getByRole('button', { name: '断开主手' })).toBeDisabled()

    await act(async () => {
      useTelemetryStore.setState((state) => ({ frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) => hand.side === 'left' ? { ...hand, connected: true } : hand),
      } }))
    })
    expect(left.getByRole('button', { name: '断开主手' })).toBeEnabled()

    await act(async () => {
      useTelemetryStore.setState((state) => ({ frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) => hand.side === 'left' ? { ...hand, connected: false } : hand),
      } }))
    })
    expect(left.getByRole('button', { name: '断开主手' })).toBeEnabled()
  })

  it('默认显示 HKVL 主用串口，并可切换 NI-DAQ 备用后保留选择', async () => {
    await show('force-left')
    const left = () => within(document.getElementById('force-left')!)
    const right = () => within(document.getElementById('force-right')!)

    expect(left().getByText('左臂 HKVL-36A 六维力')).toBeInTheDocument()
    expect(left().getByLabelText('数据源')).toHaveValue('hkvl_serial')
    expect(left().getByRole('option', { name: 'HKVL-36A / HAL 串口（主用）' })).toBeInTheDocument()
    expect(left().getByRole('option', { name: 'ATI Nano-17 / NI-DAQ（备用）' })).toBeInTheDocument()
    expect(left().getByLabelText('串口')).toHaveValue(defaultConfig.force.serial.rightPort)
    expect(right().getByLabelText('串口')).toHaveValue(defaultConfig.force.serial.leftPort)
    expect(left().queryByLabelText('DAQ 通道')).not.toBeInTheDocument()

    fireEvent.change(left().getByLabelText('数据源'), { target: { value: 'nidaq' } })
    expect(left().getByText('左臂 Nano-17 六维力')).toBeInTheDocument()
    expect(left().getByLabelText('DAQ 通道')).toHaveValue(defaultConfig.force.rightIp)
    expect(right().getByLabelText('DAQ 通道')).toHaveValue(defaultConfig.force.leftIp)
    expect(left().queryByLabelText('串口')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '系统连接' }))
    fireEvent.click(screen.getByRole('tab', { name: '安全与力觉' }))
    expect(left().getByLabelText('数据源')).toHaveValue('nidaq')
    expect(right().getByLabelText('数据源')).toHaveValue('nidaq')

    fireEvent.change(right().getByLabelText('数据源'), { target: { value: 'hkvl_serial' } })
    expect(left().getByLabelText('串口')).toHaveValue(defaultConfig.force.serial.rightPort)
    expect(right().getByLabelText('串口')).toHaveValue(defaultConfig.force.serial.leftPort)
  })

  it('六维力数值、危险色、图表和 Tare 使用相同的硬件侧', async () => {
    const tare = vi.spyOn(api, 'tareForceSensor').mockResolvedValue({ ok: true })
    useTelemetryStore.setState((state) => ({
      config: { ...state.config, force: { ...state.config.force, source: 'nidaq' }, safety: { ...state.config.safety, fxyStopN: 1 } },
      frame: { ...state.frame, forceLeft: [0.014, 0, 0, 0, 0, 0], forceRight: [1.527, 0, 0, 0, 0, 0] },
      history: [{ time: 1, joints: Array(12).fill(0), forceLeft: [0.014, 0, 0, 0, 0, 0], forceRight: [1.527, 0, 0, 0, 0, 0], danger: 0, queueLeft: 0, queueRight: 0 }],
    }))
    await show('force-left')
    const left = document.getElementById('force-left')!
    const right = document.getElementById('force-right')!
    expect(left.querySelector('.force-current-grid span')).toHaveTextContent('1527 mN')
    expect(right.querySelector('.force-current-grid span')).toHaveTextContent('14 mN')
    expect(left).toHaveClass('hardware-config-card-state-error')
    expect(right).toHaveClass('hardware-config-card-state-ok')
    expect(within(left).getByTestId('live-chart')).toHaveAttribute('data-fx', '[1527]')
    fireEvent.click(within(left).getByRole('button', { name: 'Tare' }))
    expect(tare).toHaveBeenCalledExactlyOnceWith('right')
  })

  it.each([[12, 0, 200], [400, 400, 400]])(
    'NI-DAQ 的 %i 个 Tare 样本切回 HKVL 时使用兼容值并保留其余力配置',
    async (previousSamples, expectedSamples, displayedSamples) => {
      const force = { ...structuredClone(defaultConfig.force), source: 'nidaq' as const,
        tareSamples: previousSamples, lowpassCutoffHz: 7.5 }
      useTelemetryStore.setState((state) => ({ config: { ...state.config, force } }))
      await show('force-left')
      const left = within(document.getElementById('force-left')!)
      expect(left.getByLabelText('Tare 样本')).toHaveValue(previousSamples)
      fireEvent.change(left.getByLabelText('数据源'), { target: { value: 'hkvl_serial' } })
      expect(useTelemetryStore.getState().config.force).toEqual({
        ...force, source: 'hkvl_serial', tareSamples: expectedSamples,
      })
      const samples = left.getByLabelText('Tare 样本（200–1000）')
      expect(samples).toHaveValue(displayedSamples)
      expect(samples).toHaveAttribute('min', '200')
      expect(samples).toHaveAttribute('max', '1000')
    },
  )

  it.each(['resolve', 'reject'] as const)('回原点跨 Tab 防重入，并在 %s 后释放双方按钮', async (outcome) => {
    const request = deferred()
    const returnOrigin = vi.spyOn(api, 'returnMotionOriginSide').mockReturnValueOnce(request.promise).mockResolvedValue({ ok: true })
    useTelemetryStore.setState((state) => ({ frame: {
      ...state.frame, motionEnabled: { left: true, right: true },
      motionAxisEnabled: { left: Array(6).fill(true), right: Array(6).fill(true) },
    } }))
    await show('teleop-left')
    fireEvent.click(returnButton('left'))
    fireEvent.click(returnButton('left'))
    fireEvent.click(returnButton('right'))
    expect(returnOrigin).toHaveBeenCalledTimes(1)
    expect(returnButton('left')).toBeDisabled()
    expect(returnButton('right')).toBeDisabled()
    fireEvent.click(screen.getByRole('tab', { name: '系统连接' }))
    fireEvent.click(screen.getByRole('tab', { name: '遥操作' }))
    expect(returnButton('left')).toBeDisabled()
    expect(returnButton('right')).toBeDisabled()
    fireEvent.click(returnButton('right'))
    expect(returnOrigin).toHaveBeenCalledTimes(1)
    await act(async () => {
      if (outcome === 'resolve') request.resolve({ ok: true })
      else request.reject(new Error('HAL refused'))
    })
    expect(returnButton('left')).toBeEnabled()
    expect(returnButton('right')).toBeEnabled()
    if (outcome === 'reject') {
      expect(useTelemetryStore.getState().logs.some((log) => log.level === 'ERROR' && log.msg.includes('HAL refused'))).toBe(true)
    }
    await act(async () => { fireEvent.click(returnButton('right')) })
    expect(returnOrigin).toHaveBeenLastCalledWith('left')
    expect(returnOrigin).toHaveBeenCalledTimes(2)
  })

  it('应用完整配置，等待期间防重复，拒绝后保留错误并允许重试', async () => {
    const first = deferred()
    const second = deferred()
    const apply = vi.spyOn(api, 'applyConfig').mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const reconnect = vi.spyOn(api, 'reconnectHal')
    await show('safety')
    fireEvent.change(screen.getByLabelText('Fx/Fy 急停 N（暂按量程）'), { target: { value: '9' } })
    const currentConfig = useTelemetryStore.getState().config
    const button = () => screen.getByRole('button', { name: '应用配置' })
    const status = () => screen.getByRole('status', { name: '配置应用状态' })
    fireEvent.click(button())
    fireEvent.click(button())
    expect(apply).toHaveBeenCalledExactlyOnceWith(currentConfig)
    expect(button()).toBeDisabled()
    expect(status()).toHaveTextContent('正在应用配置')
    fireEvent.click(screen.getByRole('tab', { name: '系统连接' }))
    expect(button()).toBeDisabled()
    await act(async () => { first.reject(new Error('servos enabled')) })
    expect(button()).toBeEnabled()
    expect(status()).toHaveTextContent('配置应用失败：servos enabled')
    expect(status()).not.toHaveTextContent('已保存')
    fireEvent.click(button())
    expect(apply).toHaveBeenCalledTimes(2)
    await act(async () => { second.resolve({ ok: true }) })
    expect(status()).toHaveTextContent('配置已保存并应用')
    expect(button()).toBeEnabled()
    expect(reconnect).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('数据集根目录'), { target: { value: 'D:/new-dataset' } })
    expect(screen.queryByRole('status', { name: '配置应用状态' })).not.toBeInTheDocument()
  })

  it('数据目录和录制 FPS 可编辑且不会覆盖相机采集 FPS', async () => {
    await show('storage')
    const card = within(document.getElementById('storage')!)
    const cameraFps = useTelemetryStore.getState().config.cameras.fps
    fireEvent.change(card.getByLabelText('数据集根目录'), { target: { value: 'D:/datasets/session' } })
    fireEvent.change(card.getByLabelText('录制 FPS'), { target: { value: '15' } })
    expect(useTelemetryStore.getState().config.storage).toMatchObject({ datasetRoot: 'D:/datasets/session', recordFps: 15 })
    expect(useTelemetryStore.getState().config.cameras.fps).toBe(cameraFps)
    for (const [input, expected] of [['0', 1], ['999', 60], ['2.7', 3]] as const) {
      fireEvent.change(card.getByLabelText('录制 FPS'), { target: { value: input } })
      expect(useTelemetryStore.getState().config.storage.recordFps).toBe(expected)
    }
  })

  it.each(['teleop', 'pico', 'teleop-left', 'teleop-right'])('%s hash 挂载对应的视觉或主手面板', async (hash) => {
    await show(hash)
    const isPico = hash === 'teleop' || hash === 'pico'
    expect(screen.getByRole('tab', { name: isPico ? '视觉' : '遥操作' })).toHaveAttribute('aria-selected', 'true')
    expect(document.getElementById(isPico ? 'teleop' : hash)).not.toBeNull()
  })

  it('移除开机回工作原点设置', async () => {
    await show('hal')
    expect(screen.queryByText('开机回工作原点')).not.toBeInTheDocument()
    expect(useTelemetryStore.getState().config.motion).not.toHaveProperty('homeOnStartup')
  })

  it('全局快照可恢复和删除，运动快照不混入全局列表', async () => {
    useTelemetryStore.getState().saveParameterSnapshot('all', '全局审查快照')
    useTelemetryStore.getState().saveParameterSnapshot('motion-left', '运动审查快照')
    const snapshot = useTelemetryStore.getState().parameterSnapshots.find((item) => item.scope === 'all')!
    const apply = vi.spyOn(useTelemetryStore.getState(), 'applyParameterSnapshot')
    const remove = vi.spyOn(useTelemetryStore.getState(), 'deleteParameterSnapshot')
    await show('hal')
    const dropdown = screen.getByText('选择硬件快照').closest('details')!
    fireEvent.click(screen.getByText('选择硬件快照'))
    expect(within(dropdown).queryByText('运动审查快照')).not.toBeInTheDocument()
    fireEvent.click(within(dropdown).getByRole('button', { name: '全局审查快照' }))
    expect(apply).toHaveBeenCalledExactlyOnceWith(snapshot.id)
    fireEvent.click(within(dropdown).getByRole('button', { name: '删除 全局审查快照' }))
    expect(remove).toHaveBeenCalledExactlyOnceWith(snapshot.id)
    expect(apply).toHaveBeenCalledTimes(1)
  })

  it('运动快照删除不会应用该快照', async () => {
    useTelemetryStore.getState().saveParameterSnapshot('motion-left', '待删除运动快照')
    const snapshot = useTelemetryStore.getState().parameterSnapshots[0]
    const apply = vi.spyOn(useTelemetryStore.getState(), 'applyParameterSnapshot')
    await show('motion-right')
    const card = within(document.getElementById('motion-right')!)
    fireEvent.click(card.getByText('选择运动参数'))
    fireEvent.click(card.getByRole('button', { name: '删除 待删除运动快照' }))
    expect(useTelemetryStore.getState().parameterSnapshots.find((item) => item.id === snapshot.id)).toBeUndefined()
    expect(apply).not.toHaveBeenCalled()
  })

  it('手动时钟只在运动 Tab 挂载，并在离开时停止', async () => {
    vi.useFakeTimers()
    const interval = vi.spyOn(window, 'setInterval')
    const clear = vi.spyOn(window, 'clearInterval')
    await show('storage')
    expect(interval.mock.calls.filter((call) => call[1] === 250)).toHaveLength(0)
    fireEvent.click(screen.getByRole('tab', { name: '运动控制' }))
    const callIndex = interval.mock.calls.findIndex((call) => call[1] === 250)
    expect(callIndex).toBeGreaterThanOrEqual(0)
    const timer = interval.mock.results[callIndex].value
    fireEvent.click(screen.getByRole('tab', { name: '系统连接' }))
    expect(clear).toHaveBeenCalledWith(timer)
  })
})
