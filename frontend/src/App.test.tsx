import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stopMotionSide } from './api'
import App from './App'
import { defaultConfig } from './data'
import { chartHistoryIntervalMs, uiFrameIntervalMs, useTelemetryStore } from './stores/telemetry'
import type { TelemetryFrame } from './types'

afterEach(() => {
  useTelemetryStore.getState().parameterSnapshots.forEach((snapshot) => useTelemetryStore.getState().deleteParameterSnapshot(snapshot.id))
  useTelemetryStore.getState().setDangerOverride(null)
  useTelemetryStore.getState().stopMock()
  useTelemetryStore.getState().stopBackend()
  useTelemetryStore.getState().clearRecordSession()
  cleanup()
  window.localStorage.clear()
  window.history.pushState({}, '', '/')
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('AppStation M0 frontend', () => {
  it('renders the dashboard workbench', () => {
    render(<App />)
    expect(screen.getByText('AppStation')).toBeInTheDocument()
    expect(screen.getByText('全局硬件状态')).toBeInTheDocument()
    expect(screen.getByText('左机械臂')).toBeInTheDocument()
    expect(screen.getByText('右机械臂')).toBeInTheDocument()
    expect(screen.getByText('Backend 30Hz')).toBeInTheDocument()
  })

  it('coalesces backend telemetry before committing UI frame and chart history updates', async () => {
    vi.useFakeTimers()

    class MockWebSocket {
      static instances: MockWebSocket[] = []
      readonly url: string
      onopen: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null

      constructor(url: string) {
        this.url = url
        MockWebSocket.instances.push(this)
        window.setTimeout(() => this.onopen?.(new Event('open')), 0)
      }

      close() {
        this.onclose?.({ code: 1000 } as CloseEvent)
      }

      emitTelemetry(frame: TelemetryFrame) {
        this.onmessage?.({ data: JSON.stringify({ type: 'telemetry', data: frame }) } as MessageEvent)
      }
    }

    vi.stubGlobal('WebSocket', MockWebSocket)

    const baseFrame = structuredClone(useTelemetryStore.getState().frame)
    useTelemetryStore.setState({
      tick: 0,
      history: [],
      frame: {
        ...baseFrame,
        frameCount: 0,
        episodeCount: 0,
        recording: false,
        resource: { ...baseFrame.resource, wsHz: 30 },
      },
    })

    useTelemetryStore.getState().startBackend()
    await vi.advanceTimersByTimeAsync(0)
    const ws = MockWebSocket.instances[0]
    expect(ws).toBeTruthy()

    for (let frameCount = 1; frameCount <= 30; frameCount += 1) {
      ws.emitTelemetry({
        ...baseFrame,
        timestamp: Date.now(),
        elapsedSec: frameCount / 30,
        frameCount,
        episodeCount: 0,
        recording: false,
        resource: { ...baseFrame.resource, wsHz: 30 },
      })
      await vi.advanceTimersByTimeAsync(33)
    }

    await vi.advanceTimersByTimeAsync(uiFrameIntervalMs * 2)
    const state = useTelemetryStore.getState()
    expect(state.frame.frameCount).toBe(30)
    expect(state.tick).toBeGreaterThanOrEqual(10)
    expect(state.tick).toBeLessThan(30)
    expect(state.history.length).toBeGreaterThanOrEqual(6)
    expect(state.history.length).toBeLessThanOrEqual(Math.ceil((30 * 33) / chartHistoryIntervalMs) + 2)
    expect(state.history.length).toBeLessThan(state.tick)
  })

  it('renders the operator navigation in the requested order', () => {
    render(<App />)
    const nav = screen.getByLabelText('主导航')
    const labels = within(nav).getAllByRole('link').map((link) => link.textContent)
    expect(labels).toEqual(['主页', '录制', '数据集', '模型', '微调', '自动', '设置'])
  })

  it('keeps the global emergency stop available across pages', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('link', { name: '数据集' }))

    const emergencyButton = screen.getByRole('button', { name: '全局急停 F12' })
    expect(emergencyButton).toBeInTheDocument()
    fireEvent.click(emergencyButton)

    expect(useTelemetryStore.getState().frame.dangerIndex).toBe(1.1)
    const resetButton = screen.getByRole('button', { name: '确认安全复位' })
    expect(resetButton).toBeInTheDocument()
    fireEvent.click(resetButton)

    expect(useTelemetryStore.getState().frame.dangerIndex).toBe(0)
  })

  it('navigates to the record workflow', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('link', { name: '录制' }))
    expect(screen.getByText('相机预览')).toBeInTheDocument()
    expect(screen.getByText('运动与力觉监看')).toBeInTheDocument()
    expect(screen.getByText('录制控制')).toBeInTheDocument()
    expect(screen.getByText('开始采集会话')).toBeInTheDocument()
    expect(screen.getByText('力觉安全监控')).toBeInTheDocument()
    expect(document.querySelector('.record-camera-placeholder-wrist_left')).toBeTruthy()
    expect(document.querySelector('.record-camera-placeholder-wrist_right')).toBeTruthy()
  })

  it('runs the record precheck, save, and quality report flow', async () => {
    render(<App />)
    fireEvent.click(screen.getByRole('link', { name: '录制' }))
    fireEvent.click(screen.getByText('开始采集会话').closest('button')!)

    expect(screen.getByText('采集会话开始前硬件检查')).toBeInTheDocument()
    expect(useTelemetryStore.getState().recordSession.phase).toBe('idle')

    const confirmButton = screen.getByText('确认开始').closest('button')!
    expect(confirmButton).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: '已完成' }))
    await waitFor(() => expect(confirmButton).not.toBeDisabled())
    fireEvent.click(confirmButton)

    await waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('recording'))
    fireEvent.click(screen.getByText(/保存/).closest('button')!)

    expect(screen.getByText('Episode #000 质量报告')).toBeInTheDocument()
    fireEvent.click(screen.getByText('接受并继续').closest('button')!)
    await waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('resetting'))
  })

  it('does not block the UI when danger_index is high', () => {
    render(<App />)
    fireEvent.keyDown(window, { key: 'F12' })
    expect(screen.queryByText('安全恢复确认')).not.toBeInTheDocument()
    expect(screen.getByText('Safety Off')).toBeInTheDocument()
    expect(useTelemetryStore.getState().frame.dangerIndex).toBe(1.1)
  })

  it('renders the dataset quality review workbench', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('link', { name: '数据集' }))
    expect(screen.getByText('数据集质检 Dataset')).toBeInTheDocument()
    expect(screen.getByText('同步视频检查')).toBeInTheDocument()
    expect(screen.getByText('左机械臂轨迹')).toBeInTheDocument()
    expect(screen.getByText('右机械臂轨迹')).toBeInTheDocument()
    expect(screen.getByText('左力传感器实时曲线')).toBeInTheDocument()
    expect(screen.getByText('右力传感器实时曲线')).toBeInTheDocument()
  })

  it('opens focused hardware settings from dashboard module buttons', () => {
    render(<App />)
    const motionButton = document.querySelector<HTMLButtonElement>('.arm-hardware-panel .device-chip-row button')
    expect(motionButton).toBeTruthy()
    fireEvent.click(motionButton!)
    expect(window.location.pathname).toBe('/settings')
    expect(window.location.hash).toBe('#motion-left')
    expect(screen.getByText('左臂运动控制卡 · Card 1')).toBeInTheDocument()
  })

  it('maps manual motion stop to the backend command route', async () => {
    await expect(stopMotionSide('left')).resolves.toMatchObject({ path: '/motion/left/stop' })
  })

  it('renders hardware-specific settings from the reference manual', () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    expect(screen.getByText('左臂运动控制卡 · Card 1')).toBeInTheDocument()
    expect(screen.getByText('右臂运动控制卡 · Card 0')).toBeInTheDocument()
    expect(screen.getByText('0,1,3,5,4,2')).toBeInTheDocument()
    expect(screen.getByText('2,0,5,8,1,7')).toBeInTheDocument()
    expect(screen.getAllByText(/640x480/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/mN·m/).length).toBeGreaterThan(0)
    expect(screen.getByDisplayValue('COM8')).toBeInTheDocument()
    expect(screen.getByDisplayValue('COM9')).toBeInTheDocument()
    expect(screen.getAllByText('0-26 mm').length).toBeGreaterThan(0)
    expect(screen.getAllByText('初始速度').length).toBeGreaterThan(0)
    expect(screen.queryByText('最大加速度')).not.toBeInTheDocument()
    expect(screen.getAllByText('加速时间').length).toBeGreaterThan(0)
    expect(screen.getAllByText('减速时间').length).toBeGreaterThan(0)
    expect(screen.getAllByText('软限位下限').length).toBeGreaterThan(0)
    expect(screen.getAllByText('命令力限制 N').length).toBeGreaterThan(0)
    expect(screen.getAllByText('手册未给出').length).toBeGreaterThan(0)
    expect(screen.getByText('PICO-4 视觉推流')).toBeInTheDocument()
    expect(screen.getByText('连接无线 ADB')).toBeInTheDocument()
    expect(screen.getByText('启动视觉')).toBeInTheDocument()
    expect(screen.getAllByText('相机采集目标 FPS').length).toBeGreaterThan(0)
    expect(screen.getAllByText('录制 FPS').length).toBeGreaterThan(0)
    expect(screen.queryByText('目标 FPS')).not.toBeInTheDocument()
    expect(screen.queryByText('HAL API 已验证')).not.toBeInTheDocument()
    const globalCameraCard = document.querySelector<HTMLElement>('#camera-global')
    const leftCameraCard = document.querySelector<HTMLElement>('#camera-left')
    const rightCameraCard = document.querySelector<HTMLElement>('#camera-right')
    expect(globalCameraCard).toBeTruthy()
    expect(leftCameraCard).toBeTruthy()
    expect(rightCameraCard).toBeTruthy()
    for (const card of [globalCameraCard, leftCameraCard, rightCameraCard]) {
      expect(card!.textContent).toContain('640x480')
      expect(card!.textContent).toContain('应用参数')
      expect(card!.textContent).toContain('重连预览')
      expect(card!.textContent).toContain('Exposure')
      expect(card!.textContent).toContain('Gain')
    }
    expect(within(globalCameraCard!).queryByRole('button', { name: '枚举' })).not.toBeInTheDocument()
    expect(within(globalCameraCard!).queryByRole('button', { name: '重连' })).not.toBeInTheDocument()
    expect(screen.getByText('选择硬件快照')).toBeInTheDocument()
    expect(screen.getByText('保存硬件快照')).toBeInTheDocument()
    expect(screen.getAllByText('选择运动参数')).toHaveLength(2)
    expect(screen.getAllByText('保存运动参数')).toHaveLength(2)
    expect(screen.getAllByText('使能全部')[0].closest('button')).toBeEnabled()
    expect(screen.getAllByText('回零')[0].closest('button')).toBeEnabled()
    expect(screen.getAllByText('连接主手').length).toBeGreaterThan(0)
    expect(screen.getAllByText('平移比例').length).toBeGreaterThan(0)
    expect(screen.getAllByText('旋转比例').length).toBeGreaterThan(0)
    expect(screen.queryByText('数据轮询周期 ms')).not.toBeInTheDocument()
    expect(screen.getAllByText('命令更新周期 ms').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Off / Free').length).toBeGreaterThan(0)
    expect(defaultConfig.teleop.stabilityMode).toBe('free')
    expect(defaultConfig.teleop.leftSoftLimitMin).toEqual([-200000000, -200000000, -200000000, -200000000, -200000000, -200000000])
    expect(screen.queryByText(/OpenXR/)).not.toBeInTheDocument()
  }, 10000)

  it('only keeps camera grid overlays on the global preview', () => {
    render(<App />)
    const globalFrame = document.querySelector<HTMLElement>('.camera-frame-global')
    const leftWristFrame = document.querySelector<HTMLElement>('.camera-frame-wrist_left')
    const rightWristFrame = document.querySelector<HTMLElement>('.camera-frame-wrist_right')

    expect(globalFrame?.querySelector('.camera-grid')).toBeTruthy()
    expect(globalFrame?.querySelector('.camera-reticle')).toBeTruthy()
    expect(leftWristFrame?.querySelector('.camera-grid')).toBeNull()
    expect(leftWristFrame?.querySelector('.camera-reticle')).toBeNull()
    expect(rightWristFrame?.querySelector('.camera-grid')).toBeNull()
    expect(rightWristFrame?.querySelector('.camera-reticle')).toBeNull()
  })

  it('uses a single force certificate confirmation control', () => {
    window.history.pushState({}, '', '/settings#force-left')
    render(<App />)
    const forceCard = document.querySelector<HTMLElement>('#force-left')
    expect(forceCard).toBeTruthy()
    expect(within(forceCard!).queryByText('标定证书待确认')).not.toBeInTheDocument()
    expect(within(forceCard!).getByText('标定证书')).toBeInTheDocument()
    expect(within(forceCard!).getByText('待确认')).toBeInTheDocument()

    const switches = within(forceCard!).getAllByRole('switch')
    fireEvent.click(switches.at(-1)!)

    expect(within(forceCard!).getByText('已确认')).toBeInTheDocument()
  })

  it('saves and applies a motion parameter snapshot from settings', async () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    fireEvent.click(screen.getAllByText('保存运动参数')[0].closest('button')!)
    const dialog = (await screen.findByText('保存左臂运动控制卡参数')).closest('[role="dialog"]') as HTMLElement
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: '微装配运动默认' } })
    fireEvent.click(within(dialog).getByText('保 存').closest('button')!)
    await waitFor(() => expect(useTelemetryStore.getState().parameterSnapshots.some((snapshot) => snapshot.name === '微装配运动默认')).toBe(true))

    fireEvent.click(screen.getAllByText('选择运动参数')[0].closest('button')!)
    fireEvent.click(await screen.findByText('微装配运动默认'))
    expect(useTelemetryStore.getState().logs.at(-1)?.msg).toContain('左臂运动控制卡快照已应用')

    fireEvent.click(screen.getAllByText('选择运动参数')[0].closest('button')!)
    fireEvent.click(await screen.findByLabelText('删除 微装配运动默认'))
    await waitFor(() => expect(useTelemetryStore.getState().parameterSnapshots.some((snapshot) => snapshot.name === '微装配运动默认')).toBe(false))
  }, 10000)

  it('renders manual control instead of old jog/developer pages', () => {
    window.history.pushState({}, '', '/settings#manual')
    render(<App />)
    expect(screen.getAllByText('手动控制').length).toBeGreaterThan(0)
    expect(screen.getByText('左臂手动控制')).toBeInTheDocument()
    expect(screen.getByText('右臂手动控制')).toBeInTheDocument()
    expect(screen.getByText('左臂夹爪手动控制')).toBeInTheDocument()
    expect(screen.getByText('右臂夹爪手动控制')).toBeInTheDocument()
    expect(screen.getAllByText(/um/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/°/).length).toBeGreaterThan(0)
    expect(screen.queryByText('单轴点动')).not.toBeInTheDocument()
    expect(screen.queryByText('力觉示波器')).not.toBeInTheDocument()
    expect(screen.queryByText('开发者模式')).not.toBeInTheDocument()
  })

  it('records arm and gripper manual actions into replay memory', () => {
    window.history.pushState({}, '', '/settings#manual')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        gripper: { ...state.config.gripper, leftEnabled: true },
      },
    }))
    render(<App />)
    fireEvent.click(screen.getByText('开始记录').closest('button')!)
    const leftArmCard = screen.getByText('左臂手动控制').closest('article')
    const leftGripperCard = screen.getByText('左臂夹爪手动控制').closest('article')
    expect(leftArmCard).toBeTruthy()
    expect(leftGripperCard).toBeTruthy()
    fireEvent.click(within(leftArmCard as HTMLElement).getByText('+100um').closest('button')!)
    fireEvent.click(within(leftGripperCard as HTMLElement).getByText(/打\s*开/).closest('button')!)
    fireEvent.click(screen.getByText('保存动作记忆').closest('button')!)
    expect(screen.getByText('动作记忆 1')).toBeInTheDocument()
    expect(screen.getByText(/2 steps/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('回放').closest('button')!)
    expect(useTelemetryStore.getState().manualControl.replayingMemoryId).not.toBeNull()
    expect(useTelemetryStore.getState().logs.at(-1)?.msg).toContain('Manual memory replay queued')
  })

  it('runs test fixture hardware commands without backend calls', () => {
    window.history.pushState({}, '', '/settings#force-left')
    render(<App />)
    const before = useTelemetryStore.getState().logs.length
    const tareButton = document.querySelector<HTMLButtonElement>('#force-left .hardware-config-actions button')
    expect(tareButton).toBeTruthy()
    fireEvent.click(tareButton!)
    expect(useTelemetryStore.getState().logs.length).toBeGreaterThan(before)
    expect(useTelemetryStore.getState().logs.at(-1)?.msg).toContain('test fixture')
  })

  it('toggles teleop hands with one logical connect button', async () => {
    window.history.pushState({}, '', '/settings#teleop-left')
    render(<App />)
    const teleopButton = document.querySelector<HTMLButtonElement>('#teleop-left .hardware-config-actions button')
    expect(teleopButton).toBeTruthy()

    fireEvent.click(teleopButton!)
    await waitFor(() => expect(useTelemetryStore.getState().config.teleop.leftConnected).toBe(true))

    fireEvent.click(teleopButton!)
    await waitFor(() => expect(useTelemetryStore.getState().config.teleop.leftConnected).toBe(false))
  })

  it('renders motion origin controls and the startup return switch', () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    expect(screen.getAllByText('设为采集零点')).toHaveLength(2)
    expect(screen.getAllByText('清除零点')).toHaveLength(2)
    expect(screen.getByText('开机回工作原点')).toBeInTheDocument()
  })

  it('updates the motion origin state when the side controls are used', async () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    const leftCard = document.querySelector<HTMLElement>('#motion-left')
    expect(leftCard).toBeTruthy()

    fireEvent.click(within(leftCard!).getByText('设为采集零点').closest('button')!)
    await waitFor(() => expect(within(leftCard!).getByText('已设置')).toBeInTheDocument())

    fireEvent.click(within(leftCard!).getByText('清除零点').closest('button')!)
    await waitFor(() => expect(within(leftCard!).getByText('未设置')).toBeInTheDocument())
  })
})
