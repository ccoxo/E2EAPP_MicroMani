import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { defaultConfig } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import { initialControlSafety } from '../utils/controlSafety'
import { AutoView } from './AutoView'
import { MotionCard } from './settings/MotionCards'
import { TeleopHandCard } from './settings/TeleopHandCard'
import type { PendingComparison } from './settings/shared'

const initialState = useTelemetryStore.getState()

function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (error: Error) => void
  const promise = new Promise<unknown>((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}

function blockControls() {
  useTelemetryStore.setState((state) => ({
    controlSafety: { ...state.controlSafety, generation: state.controlSafety.generation + 1, emergencyRequested: true },
  }))
}

beforeEach(() => {
  const config = structuredClone(defaultConfig)
  config.motion.homeReference.rightAxisConfirmed = [true, true, true, true, true, true]
  config.motion.origin = { ...config.motion.origin, valid: true, leftValid: true, rightValid: true }
  config.teleop = { ...config.teleop, leftConnected: false, leftGravityCompensation: false, leftForceFeedback: false }
  useTelemetryStore.setState({
    ...initialState,
    config,
    controlSafety: initialControlSafety(),
    telemetryLink: { state: 'live', lastFrameReceivedAt: Date.now() },
    frame: {
      ...initialState.frame,
      wsOk: true,
      halOk: true,
      cameras: [],
      forceStatus: { ...initialState.frame.forceStatus, safety: { latched: false } },
      motionEnabled: { left: true, right: true },
      motionAxisEnabled: { left: Array<boolean>(6).fill(true), right: Array<boolean>(6).fill(true) },
    },
    history: [],
    logs: [],
  })
})

afterEach(() => {
  cleanup()
  useTelemetryStore.setState(initialState, true)
  vi.restoreAllMocks()
})

function renderMotionCard() {
  const injectLog = vi.fn()
  const requestComparison = vi.fn<(comparison: PendingComparison) => void>()
  const state = useTelemetryStore.getState()
  render(<MotionCard
    side="left" config={state.config} positions={state.frame.jointPositions} focusHash=""
    updateConfig={vi.fn()} injectLog={injectLog} triggerEmergencyStop={vi.fn()}
    snapshotMenu={() => ({ items: [], onClick: vi.fn(), onDelete: vi.fn() })}
    openSnapshotModal={vi.fn()} requestComparison={requestComparison}
    previousRestoreStatus={null} refreshMotionOriginStatus={vi.fn().mockResolvedValue(undefined)}
  />)
  return { injectLog, requestComparison }
}

function renderTeleopCard() {
  const injectLog = vi.fn()
  const updateConfig = vi.fn()
  const state = useTelemetryStore.getState()
  render(<TeleopHandCard
    side="left" config={state.config} frame={state.frame} focusHash=""
    updateConfig={updateConfig} injectLog={injectLog}
    pendingReturnOriginSide={null} setPendingReturnOriginSide={vi.fn().mockReturnValue(true)}
  />)
  return { injectLog, updateConfig }
}

describe('直接 API 页面入口的安全约束', () => {
  it.each(['emergency', 'offline'] as const)('Auto %s 禁止启动和注入，保留暂停/停止', async (reason) => {
    const queue = vi.spyOn(api, 'queueAutoAction').mockResolvedValue({ ok: true })
    const setAutoRunning = vi.fn()
    if (reason === 'emergency') blockControls()
    else useTelemetryStore.setState({ telemetryLink: { state: 'offline', lastFrameReceivedAt: null } })
    useTelemetryStore.setState({ setAutoRunning })
    render(<AutoView />)

    expect(screen.getByRole('button', { name: '启动' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '注入动作' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '暂停' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(setAutoRunning).toHaveBeenCalledWith(false)
    expect(queue).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).not.toBeEmptyDOMElement()
  })

  it('排队期间急停，即使后来解除门闩也不能由旧成功响应续发动作', async () => {
    const queued = deferred()
    vi.spyOn(api, 'queueAutoAction').mockReturnValue(queued.promise)
    const dispatch = vi.spyOn(api, 'dispatchNextAutoAction').mockResolvedValue({ ok: true })
    render(<AutoView />)
    fireEvent.click(screen.getByRole('button', { name: '注入动作' }))
    await act(async () => {
      blockControls()
      useTelemetryStore.setState((state) => ({ controlSafety: { ...state.controlSafety, emergencyRequested: false } }))
      queued.resolve({ ok: true })
    })

    expect(dispatch).not.toHaveBeenCalled()
    expect(useTelemetryStore.getState().logs.some((log) => log.level === 'WARNING' && log.msg.includes('动作注入已取消'))).toBe(true)
  })

  it('排队期间遥测失联，不继续派发动作', async () => {
    const queued = deferred()
    vi.spyOn(api, 'queueAutoAction').mockReturnValue(queued.promise)
    const dispatch = vi.spyOn(api, 'dispatchNextAutoAction').mockResolvedValue({ ok: true })
    render(<AutoView />)
    fireEvent.click(screen.getByRole('button', { name: '注入动作' }))
    await act(async () => {
      useTelemetryStore.setState({ telemetryLink: { state: 'offline', lastFrameReceivedAt: null } })
      queued.resolve({ ok: true })
    })
    expect(dispatch).not.toHaveBeenCalled()
  })

  it.each(['queue', 'dispatch'] as const)('动作注入 %s 失败写入错误日志并捕获拒绝', async (stage) => {
    const queue = vi.spyOn(api, 'queueAutoAction')
    const dispatch = vi.spyOn(api, 'dispatchNextAutoAction')
    if (stage === 'queue') queue.mockRejectedValue(new Error('queue unavailable'))
    else {
      queue.mockResolvedValue({ ok: true })
      dispatch.mockRejectedValue(new Error('dispatch unavailable'))
    }
    render(<AutoView />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '注入动作' })) })

    expect(useTelemetryStore.getState().logs.some((log) => log.level === 'ERROR' && log.msg.includes(`${stage} unavailable`))).toBe(true)
    if (stage === 'queue') expect(dispatch).not.toHaveBeenCalled()
  })

  it('无安全事件时排队成功后继续派发', async () => {
    vi.spyOn(api, 'queueAutoAction').mockResolvedValue({ ok: true })
    const dispatch = vi.spyOn(api, 'dispatchNextAutoAction').mockResolvedValue({ ok: true })
    render(<AutoView />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '注入动作' })) })
    expect(dispatch).toHaveBeenCalledTimes(1)
    expect(useTelemetryStore.getState().logs.some((log) => log.msg === '动作注入请求已接受')).toBe(true)
  })

  it('HOME 确认弹窗打开后急停，确认回调重新检查门闩', async () => {
    const home = vi.spyOn(api, 'returnHardwareReferenceSide').mockResolvedValue({ ok: true })
    const { injectLog, requestComparison } = renderMotionCard()
    fireEvent.click(screen.getByRole('button', { name: '返回机械参考点' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pitch' }))
    fireEvent.click(screen.getByRole('button', { name: '审阅返回动作' }))
    const comparison = requestComparison.mock.calls[0][0]
    await act(async () => {
      blockControls()
      await comparison.onConfirm()
    })

    expect(home).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '返回机械参考点' })).toBeDisabled()
    expect(injectLog).toHaveBeenCalledWith('WARNING', expect.stringContaining('返回机械参考点受阻'), '[HAL]')
  })

  it('急停后主手回原点、连接和重力补偿启用均受阻', () => {
    const connect = vi.spyOn(api, 'connectTeleopHand').mockResolvedValue({ ok: true })
    const origin = vi.spyOn(api, 'returnMotionOriginSide').mockResolvedValue({ ok: true })
    const gravity = vi.spyOn(api, 'setTeleopGravityCompensation').mockResolvedValue({ ok: true })
    blockControls()
    renderTeleopCard()

    for (const name of ['回工作原点', '连接主手', '重力补偿']) {
      const button = screen.getByRole('button', { name })
      expect(button).toBeDisabled()
      fireEvent.click(button)
    }
    expect(connect).not.toHaveBeenCalled()
    expect(origin).not.toHaveBeenCalled()
    expect(gravity).not.toHaveBeenCalled()
  })

  it('急停后仍能断开主手、关闭重力补偿', async () => {
    const disconnect = vi.spyOn(api, 'disconnectTeleopHand').mockResolvedValue({ ok: true })
    const gravity = vi.spyOn(api, 'setTeleopGravityCompensation').mockResolvedValue({ ok: true })
    useTelemetryStore.setState((state) => ({
      config: { ...state.config, teleop: { ...state.config.teleop, leftConnected: true, leftGravityCompensation: true } },
    }))
    blockControls()
    renderTeleopCard()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '断开主手' }))
      fireEvent.click(screen.getByRole('button', { name: '重力补偿' }))
    })

    expect(disconnect).toHaveBeenCalledWith('left')
    expect(gravity).toHaveBeenCalledWith('left', expect.objectContaining({ enabled: false }))
  })

  it('主手连接迟到成功不能在急停后回写连接配置', async () => {
    const connected = deferred()
    vi.spyOn(api, 'connectTeleopHand').mockReturnValue(connected.promise)
    const { updateConfig, injectLog } = renderTeleopCard()
    fireEvent.click(screen.getByRole('button', { name: '连接主手' }))
    await act(async () => {
      blockControls()
      connected.resolve({ data: { connected: true } })
    })

    expect(updateConfig).not.toHaveBeenCalled()
    expect(injectLog).toHaveBeenCalledWith('WARNING', expect.stringContaining('主手连接确认已取消'), '[HAL]')
  })
})


describe('机械参考点选轴', () => {
  it('默认不选轴，机械寻零可全选六轴且只在确认后发送', async () => {
    const home = vi.spyOn(api, 'homeMotionSide').mockResolvedValue({ ok: true })
    vi.spyOn(api, 'fetchMotionOrigin').mockResolvedValue({ ok: true })
    const { requestComparison } = renderMotionCard()
    fireEvent.click(screen.getByRole('button', { name: '机械寻零' }))
    expect(screen.getByRole('button', { name: '审阅寻零动作' })).toBeDisabled()
    for (const axis of ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']) expect(screen.getByRole('checkbox', { name: axis })).not.toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '全选六轴' }))
    fireEvent.click(screen.getByRole('button', { name: '审阅寻零动作' }))
    expect(home).not.toHaveBeenCalled()
    await act(async () => { await requestComparison.mock.calls[0][0].onConfirm() })
    expect(home).toHaveBeenCalledExactlyOnceWith('right', ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw'])
    expect(screen.getByRole('status', { name: '左臂原点操作状态' })).toHaveTextContent('机械寻零完成')
  })

  it('只返回选择的旋转轴，不附带 XYZ 或启动机械寻零', async () => {
    const move = vi.spyOn(api, 'returnHardwareReferenceSide').mockResolvedValue({ ok: true })
    const home = vi.spyOn(api, 'homeMotionSide').mockResolvedValue({ ok: true })
    const { requestComparison } = renderMotionCard()
    fireEvent.click(screen.getByRole('button', { name: '返回机械参考点' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Roll' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pitch' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Yaw' }))
    fireEvent.click(screen.getByRole('button', { name: '审阅返回动作' }))
    await act(async () => { await requestComparison.mock.calls[0][0].onConfirm() })
    expect(move).toHaveBeenCalledExactlyOnceWith('right', ['Roll', 'Pitch', 'Yaw'])
    expect(home).not.toHaveBeenCalled()
  })

  it('任一所选轴待确认时拒绝返回，允许改为重新寻零', () => {
    useTelemetryStore.getState().config.motion.homeReference.rightAxisConfirmed = [false, false, false, true, true, true]
    const move = vi.spyOn(api, 'returnHardwareReferenceSide')
    const { requestComparison } = renderMotionCard()
    fireEvent.click(screen.getByRole('button', { name: '返回机械参考点' }))
    fireEvent.click(screen.getByRole('button', { name: '全选六轴' }))
    expect(screen.getByRole('button', { name: '审阅返回动作' })).toBeDisabled()
    expect(screen.getByText('待确认的所选轴：X、Y、Z')).toBeInTheDocument()
    expect(requestComparison).not.toHaveBeenCalled()
    expect(move).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '机械寻零' }))
    expect(screen.getByRole('button', { name: '审阅寻零动作' })).toBeDisabled()
  })

  it('寻零请求失败时显示失败并重新读取确认状态', async () => {
    vi.spyOn(api, 'homeMotionSide').mockRejectedValue(new Error('homing interrupted'))
    const fetch = vi.spyOn(api, 'fetchMotionOrigin').mockResolvedValue({ ok: true })
    const { requestComparison } = renderMotionCard()
    fireEvent.click(screen.getByRole('button', { name: '机械寻零' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Pitch' }))
    fireEvent.click(screen.getByRole('button', { name: '审阅寻零动作' }))
    await act(async () => { await requestComparison.mock.calls[0][0].onConfirm() })
    expect(fetch).toHaveBeenCalled()
    expect(screen.getByRole('status', { name: '左臂原点操作状态' })).toHaveTextContent('失败')
    expect(screen.queryByText('机械寻零完成')).not.toBeInTheDocument()
  })
})


it('寻零进行中急停，迟到的成功应答不能显示完成', async () => {
  let finish!: (response: api.MotionOriginResponse) => void
  vi.spyOn(api, 'homeMotionSide').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  vi.spyOn(api, 'fetchMotionOrigin').mockResolvedValue({ ok: true })
  const { requestComparison } = renderMotionCard()
  fireEvent.click(screen.getByRole('button', { name: '机械寻零' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Pitch' }))
  fireEvent.click(screen.getByRole('button', { name: '审阅寻零动作' }))
  let pending: unknown
  await act(async () => { pending = requestComparison.mock.calls[0][0].onConfirm() })
  expect(screen.getByRole('status', { name: '左臂原点操作状态' })).toHaveTextContent('正在寻零：Pitch')
  await act(async () => { blockControls(); finish({ ok: true }); await pending })
  expect(screen.getByRole('status', { name: '左臂原点操作状态' })).toHaveTextContent('失败')
})
