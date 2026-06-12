import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { formatApiErrorMessage, stopMotionSide } from './api'
import App from './App'
import { ActionCompareModal } from './components/ActionCompareModal'
import { LogPanel } from './components/LogPanel'
import { defaultConfig, defaultDiagnostics } from './data'
import { chartHistoryIntervalMs, uiFrameIntervalMs, useTelemetryStore } from './stores/telemetry'
import { isManualAxisDisabled } from './manualAxisRules'
import type { TelemetryFrame } from './types'
import { DatasetView } from './views/DatasetView'

afterEach(() => {
  useTelemetryStore.getState().parameterSnapshots.forEach((snapshot) => useTelemetryStore.getState().deleteParameterSnapshot(snapshot.id))
  useTelemetryStore.getState().setDangerOverride(null)
  useTelemetryStore.getState().stopMock()
  useTelemetryStore.getState().stopBackend()
  useTelemetryStore.getState().clearRecordSession()
  useTelemetryStore.setState((state) => ({
    config: structuredClone(defaultConfig),
    diagnostics: structuredClone(defaultDiagnostics),
    picoConnection: { state: 'pending', message: '尚未检查 PICO ADB', checkedAt: null },
    frame: {
      ...state.frame,
      teleopHands: state.frame.teleopHands.map((hand) => ({
        ...hand,
        connected: false,
        calibrated: false,
        openId: hand.side === 'left' ? defaultConfig.teleop.leftOpenId : defaultConfig.teleop.rightOpenId,
        deviceId: -1,
        pose: [0, 0, 0, 0, 0, 0],
        clutchPressed: false,
        gripperPressed: false,
        gripperGapMm: null,
        lastReadOk: false,
        message: 'logical teleop hand disconnected',
      })),
    },
  }))
  cleanup()
  window.localStorage.clear()
  window.history.pushState({}, '', '/')
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AppStation M0 frontend', () => {
  it('renders a before and after comparison before confirming a high-risk action', () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()

    render(
      <ActionCompareModal
        open
        title="应用相机参数"
        tone="warning"
        impact="将写入全局相机预览参数"
        current={[
          { label: 'Exposure', value: '-5.5' },
          { label: 'Gain', value: '0' },
        ]}
        proposed={[
          { label: 'Exposure', value: '-6.0' },
          { label: 'Gain', value: '12' },
        ]}
        confirmText="确认应用"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    )

    expect(screen.getByText('应用相机参数')).toBeInTheDocument()
    expect(screen.getByText('当前')).toBeInTheDocument()
    expect(screen.getByText('将应用')).toBeInTheDocument()
    expect(screen.getByText('将写入全局相机预览参数')).toBeInTheDocument()
    expect(screen.getByText('-5.5')).toBeInTheDocument()
    expect(screen.getByText('-6.0')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '确认应用' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

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

    const emergencyButton = screen.getByRole('button', { name: '全局急停' })
    expect(emergencyButton).toBeInTheDocument()
    fireEvent.click(emergencyButton)

    expect(useTelemetryStore.getState().frame.dangerIndex).toBe(1.1)
    const resetButton = screen.getByRole('button', { name: '确认安全态' })
    expect(resetButton).toBeInTheDocument()
    expect(resetButton).toHaveTextContent('确认安全态')
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

  it('exposes Kalman filter controls on the record page', () => {
    window.history.pushState({}, '', '/record')
    render(<App />)

    expect(screen.getByText('卡尔曼滤波')).toBeInTheDocument()
    expect(screen.getByText('遗忘因子 beta')).toBeInTheDocument()
    expect(screen.getByText('平移测量方差 R')).toBeInTheDocument()
    expect(screen.getByText('平移阈值 v_th')).toBeInTheDocument()
    expect(screen.getByText('旋转测量方差 R')).toBeInTheDocument()
    expect(screen.getByText('旋转阈值 v_th')).toBeInTheDocument()
    expect(document.querySelector('.record-page-side')).toContainElement(screen.getByText('卡尔曼滤波'))

    const toggle = screen.getByRole('switch', { name: '卡尔曼滤波开关' })
    expect(toggle).not.toBeChecked()
    fireEvent.click(toggle)

    expect(useTelemetryStore.getState().config.teleop.kalmanFilterEnabled).toBe(true)
  })

  it('returns left and right slave arms to recorded zero from the record controls', async () => {
    let resolveLeftReturn: (value: { ok: boolean }) => void = () => undefined
    const leftReturnPromise = new Promise<{ ok: boolean }>((resolve) => {
      resolveLeftReturn = resolve
    })
    const returnOriginSpy = vi.spyOn(api, 'returnMotionOriginSide').mockImplementation((side) => {
      if (side === 'left') return leftReturnPromise
      return Promise.resolve({ ok: true })
    })

    window.history.pushState({}, '', '/record')
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))
    render(<App />)
    useTelemetryStore.getState().stopMock()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    const leftButton = () => screen.getByText('左从臂回工作原点').closest('button')!
    const rightButton = () => screen.getByText('右从臂回工作原点').closest('button')!

    fireEvent.click(leftButton())
    await waitFor(() => expect(returnOriginSpy).toHaveBeenCalledWith('left'), { timeout: 1000 })
    expect(rightButton()).toBeDisabled()

    resolveLeftReturn({ ok: true })
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))
    await waitFor(() => expect(rightButton()).toBeEnabled(), { timeout: 1000 })
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))
    await waitFor(() => expect(rightButton()).toBeEnabled(), { timeout: 1000 })
    fireEvent.click(rightButton())
    expect(returnOriginSpy).toHaveBeenCalledWith('right')
  })

  it('disables record return-to-origin buttons when the motion side is not enabled', () => {
    window.history.pushState({}, '', '/record')
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: false, right: true },
        motionAxisEnabled: {
          left: [false, false, false, false, false, false],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    render(<App />)
    useTelemetryStore.getState().stopMock()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: false, right: true },
        motionAxisEnabled: {
          left: [false, false, false, false, false, false],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    expect(screen.getByText('左从臂回工作原点').closest('button')).toBeDisabled()
    expect(screen.getByText('右从臂回工作原点').closest('button')).toBeEnabled()
  })

  it('shows teleop connection, gripper teleop ports, and camera tuning controls in compact status rows', () => {
    window.history.pushState({}, '', '/settings')
    render(<App />)

    const teleopStrips = document.querySelectorAll('.teleop-connection-strip')
    expect(teleopStrips.length).toBeGreaterThanOrEqual(2)
    expect(teleopStrips[0].textContent).not.toContain('COM8')
    expect(teleopStrips[0].textContent).not.toContain('COM9')

    const gripperTeleopStrips = document.querySelectorAll('.gripper-teleop-strip')
    expect(gripperTeleopStrips.length).toBeGreaterThanOrEqual(2)
    expect(gripperTeleopStrips[0].textContent).toContain('COM8')
    expect(gripperTeleopStrips[0].textContent).toContain('slave 10')

    const cameraRows = document.querySelectorAll('.camera-tuning-control-row')
    expect(cameraRows.length).toBeGreaterThanOrEqual(6)
  })

  it('marks a logically connected but physically offline teleop hand as an error state', () => {
    window.history.pushState({}, '', '/settings#teleop-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: true },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) =>
          hand.side === 'left'
            ? { ...hand, connected: false, lastReadOk: false, message: 'physical device offline' }
            : hand,
        ),
      },
    }))

    render(<App />)

    const teleopConnection = document.querySelector<HTMLElement>('#teleop-left .teleop-connection-strip > div:first-child')
    expect(teleopConnection).toHaveClass('teleop-status-error')
    expect(teleopConnection).toHaveTextContent('物理离线')
  })

  it('makes a physically offline teleop hand obvious even before logical connection', () => {
    window.history.pushState({}, '', '/settings#teleop-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: false },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) =>
          hand.side === 'left'
            ? { ...hand, connected: false, lastReadOk: false, message: 'waiting for operator connection' }
            : hand,
        ),
      },
    }))

    render(<App />)

    const teleopCard = document.querySelector<HTMLElement>('#teleop-left')
    const teleopAlert = document.querySelector<HTMLElement>('#teleop-left .teleop-error-callout')
    const teleopConnection = document.querySelector<HTMLElement>('#teleop-left .teleop-connection-strip > div:first-child')
    expect(teleopCard).toHaveClass('hardware-config-card-state-error')
    expect(teleopAlert).toHaveClass('hardware-error-callout')
    expect(teleopAlert).toHaveTextContent('waiting for operator connection')
    expect(teleopConnection).toHaveClass('teleop-status-error')
    expect(teleopConnection).toHaveTextContent('物理离线')
  })

  it('uses live Omega telemetry for the displayed OpenID instead of the configured OpenID', () => {
    window.history.pushState({}, '', '/settings#teleop-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: true, leftOpenId: 0 },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) =>
          hand.side === 'left'
            ? {
                ...hand,
                connected: true,
                lastReadOk: true,
                openId: 1,
                deviceId: 1,
                serial: '22025',
                pose: [0, 0, 0, 0, 0, 0],
              }
            : hand,
        ),
      },
    }))

    render(<App />)

    const teleopCard = document.querySelector<HTMLElement>('#teleop-left')
    expect(teleopCard).toHaveTextContent('配置 dhdOpenID(0)')
    expect(teleopCard).toHaveTextContent('OpenID 1 / device 1')
    expect(teleopCard).not.toHaveTextContent('OpenID 0 / device 1')
  })

  it('shows the crossed target arm for each Omega teleop source hand', () => {
    window.history.pushState({}, '', '/settings')

    render(<App />)

    const leftTeleopCard = document.querySelector<HTMLElement>('#teleop-left')
    const rightTeleopCard = document.querySelector<HTMLElement>('#teleop-right')
    expect(leftTeleopCard).toBeTruthy()
    expect(rightTeleopCard).toBeTruthy()
    const leftActions = leftTeleopCard!.querySelector<HTMLElement>('.hardware-config-actions')
    const rightActions = rightTeleopCard!.querySelector<HTMLElement>('.hardware-config-actions')
    const leftMetrics = leftTeleopCard!.querySelector<HTMLElement>('.hardware-metric-grid')
    const rightMetrics = rightTeleopCard!.querySelector<HTMLElement>('.hardware-metric-grid')
    expect(leftActions).toHaveTextContent('目标臂')
    expect(leftActions).toHaveTextContent('右臂')
    expect(leftActions!.textContent!.indexOf('目标臂')).toBeLessThan(leftActions!.textContent!.indexOf('连接主手'))
    expect(rightActions).toHaveTextContent('目标臂')
    expect(rightActions).toHaveTextContent('左臂')
    expect(rightActions!.textContent!.indexOf('目标臂')).toBeLessThan(rightActions!.textContent!.indexOf('连接主手'))
    expect(leftMetrics).not.toHaveTextContent('目标臂')
    expect(rightMetrics).not.toHaveTextContent('目标臂')
  })

  it('returns only the selected slave arm to the work origin from Omega cards', async () => {
    let resolveLeftReturn: (value: { ok: boolean }) => void = () => undefined
    const leftReturnPromise = new Promise<{ ok: boolean }>((resolve) => {
      resolveLeftReturn = resolve
    })
    const returnOriginSpy = vi.spyOn(api, 'returnMotionOriginSide').mockImplementation((side) => {
      if (side === 'left') return leftReturnPromise
      return Promise.resolve({ ok: true })
    })
    const homeAllSpy = vi.spyOn(api, 'homeAll').mockResolvedValue({ ok: true })
    const captureOriginSpy = vi.spyOn(api, 'captureMotionOrigin').mockResolvedValue({ ok: true })

    window.history.pushState({}, '', '/settings')
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))
    render(<App />)
    useTelemetryStore.getState().stopMock()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    const getTeleopCard = (side: 'left' | 'right') => {
      const card = document.querySelector<HTMLElement>(`#teleop-${side}`)
      expect(card).toBeTruthy()
      return card!
    }
    const getReturnButton = (side: 'left' | 'right') => {
      const button = [...getTeleopCard(side).querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes('回工作原点'))
      expect(button).toBeTruthy()
      return button!
    }

    fireEvent.click(getReturnButton('left'))
    await waitFor(() => expect(returnOriginSpy).toHaveBeenCalledWith('left'), { timeout: 1000 })
    expect(getReturnButton('right')).toBeDisabled()

    resolveLeftReturn({ ok: true })
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))
    await waitFor(() => expect(getReturnButton('right')).toBeEnabled(), { timeout: 1000 })
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: true, right: true },
        motionAxisEnabled: {
          left: [true, true, true, true, true, true],
          right: [true, true, true, true, true, false],
        },
      },
    }))
    await waitFor(() => expect(getReturnButton('right')).toBeEnabled(), { timeout: 1000 })
    fireEvent.click(getReturnButton('right'))
    expect(returnOriginSpy.mock.calls).toEqual([['left'], ['right']])
    expect(returnOriginSpy).toHaveBeenCalledWith('right')
    expect(homeAllSpy).not.toHaveBeenCalled()
    expect(captureOriginSpy).not.toHaveBeenCalled()
  })

  it('disables Omega return-to-origin buttons when the motion side is not enabled', () => {
    window.history.pushState({}, '', '/settings')
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: false, right: true },
        motionAxisEnabled: {
          left: [false, false, false, false, false, false],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    render(<App />)
    useTelemetryStore.getState().stopMock()
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { left: false, right: true },
        motionAxisEnabled: {
          left: [false, false, false, false, false, false],
          right: [true, true, true, true, true, false],
        },
      },
    }))

    const getTeleopCard = (side: 'left' | 'right') => {
      const card = document.querySelector<HTMLElement>(`#teleop-${side}`)
      expect(card).toBeTruthy()
      return card!
    }
    const getReturnButton = (side: 'left' | 'right') => {
      const button = [...getTeleopCard(side).querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes('回工作原点'))
      expect(button).toBeTruthy()
      return button!
    }

    expect(getReturnButton('left')).toBeDisabled()
    expect(getReturnButton('right')).toBeEnabled()
  })

  it('does not show stale Omega pose values after the latest read failed', () => {
    window.history.pushState({}, '', '/settings#teleop-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: true },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) =>
          hand.side === 'left'
            ? {
                ...hand,
                connected: true,
                lastReadOk: false,
                pose: [0.01, 0.02, 0.03, 4, 5, 6],
                message: 'SDK read timeout',
              }
            : hand,
        ),
      },
    }))

    render(<App />)

    const teleopCard = document.querySelector<HTMLElement>('#teleop-left')
    expect(teleopCard).toHaveTextContent('读数待恢复')
    expect(teleopCard).not.toHaveTextContent('10.0, 20.0, 30.0 mm')
    expect(teleopCard).not.toHaveTextContent('4.00, 5.00, 6.00°')
  })

  it('marks dashboard Omega hand status from per-hand telemetry reads', () => {
    useTelemetryStore.setState((state) => ({
      diagnostics: state.diagnostics.map((item) => (item.key === 'omega7' ? { ...item, status: 'ok' } : item)),
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: true, rightConnected: true },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) =>
          hand.side === 'left'
            ? { ...hand, connected: true, lastReadOk: false, message: 'SDK read timeout' }
            : { ...hand, connected: true, lastReadOk: true },
        ),
      },
    }))

    render(<App />)

    const leftPanel = screen.getByText('左机械臂').closest('section')
    const teleopChip = leftPanel?.querySelector<HTMLElement>('.device-chip-row button:last-child')
    expect(teleopChip).toHaveClass('device-chip-warn')
    expect(teleopChip).toHaveTextContent('读数待恢复')
  })

  it('marks a failed gripper teleop port as an error state', async () => {
    vi.spyOn(api, 'fetchGripperTeleopStatus').mockResolvedValue({
      data: {
        running: false,
        requestedRunning: false,
        message: 'left gripper serial open failed',
        ports: [
          { side: 'left', port: 'COM8', slaveId: 10, baudrate: 115200, ok: false, message: 'serial open failed' },
        ],
      },
    })

    window.history.pushState({}, '', '/settings#gripper-left')
    render(<App />)

    await waitFor(() => expect(api.fetchGripperTeleopStatus).toHaveBeenCalled())
    const gripperAlert = document.querySelector<HTMLElement>('#gripper-left .gripper-error-callout')
    const gripperPort = document.querySelector<HTMLElement>('#gripper-left .gripper-teleop-strip > div:first-child')
    expect(gripperAlert).toHaveClass('hardware-error-callout')
    expect(gripperAlert).toHaveTextContent('serial open failed')
    expect(gripperPort).toHaveClass('gripper-status-error')
    expect(gripperPort).toHaveTextContent('serial open failed')
  })

  it('uses a danger style for the running gripper teleop stop button', async () => {
    vi.spyOn(api, 'fetchGripperTeleopStatus').mockResolvedValue({
      data: {
        running: true,
        requestedRunning: true,
        ports: [
          { side: 'left', port: 'COM8', slaveId: 10, baudrate: 115200, ok: true },
        ],
      },
    })

    window.history.pushState({}, '', '/settings#gripper-left')
    render(<App />)

    await waitFor(() => expect(screen.getAllByText('停止遥操').length).toBeGreaterThan(0))
    const stopButton = screen.getAllByText('停止遥操')[0].closest('button')
    expect(stopButton).toHaveClass('ant-btn-dangerous')
  })

  it('demotes gripper enable controls when HAL-native teleop owns the gripper', async () => {
    vi.spyOn(api, 'fetchGripperTeleopStatus').mockResolvedValue({
      data: {
        running: true,
        requestedRunning: true,
        ports: [
          { side: 'left', port: 'COM8', slaveId: 10, baudrate: 115200, ok: true },
        ],
      },
    })

    window.history.pushState({}, '', '/settings#gripper-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, engine: 'hal_native' },
        gripper: { ...state.config.gripper, leftEnabled: false },
      },
    }))
    render(<App />)

    await waitFor(() => expect(api.fetchGripperTeleopStatus).toHaveBeenCalled())
    const leftGripperCard = document.querySelector<HTMLElement>('#gripper-left')
    expect(leftGripperCard).toBeTruthy()
    expect(within(leftGripperCard!).getAllByText('随 Omega.7 自动遥操作').length).toBeGreaterThan(0)

    const mainActions = leftGripperCard!.querySelector<HTMLElement>('.gripper-action-section')
    expect(mainActions).toBeTruthy()
    expect(within(mainActions!).queryByRole('button', { name: '使能' })).not.toBeInTheDocument()
    expect(within(mainActions!).queryByRole('button', { name: '断使能' })).not.toBeInTheDocument()
    expect(within(leftGripperCard!).getByText('高级手动命令')).toBeInTheDocument()
    expect(within(leftGripperCard!).getByRole('button', { name: '手动下发使能' })).toBeInTheDocument()
  })

  it('runs the record precheck, save, and quality report flow', async () => {
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: true, rightConnected: true },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) => ({
          ...hand,
          connected: true,
          lastReadOk: true,
          message: 'test fixture',
        })),
      },
      diagnostics: state.diagnostics.map((item) =>
        item.key === 'omega7' || item.key === 'gripper' ? { ...item, status: 'ok' } : item,
      ),
    }))
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
    expect(useTelemetryStore.getState().recordSession.phase).toBe('saving')
    expect(screen.queryByText(/Episode #000/)).not.toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(useTelemetryStore.getState().recordSession.phase).toBe('saving')

    await waitFor(() => expect(screen.getByText(/Episode #000/)).toBeInTheDocument())
    fireEvent.click(screen.getByText('接受并继续').closest('button')!)
    await waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('resetting'))
  })

  it('starts the UI record timer only after the backend creates the session', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-22T00:00:00.000Z'))

    let resolveCreateSession: (value: api.RecordSessionCommandResponse) => void = () => undefined
    const createSessionPromise = new Promise<api.RecordSessionCommandResponse>((resolve) => {
      resolveCreateSession = resolve
    })
    vi.spyOn(api, 'fetchRecordStatus').mockResolvedValue({ active: false, recording: false })
    const createSessionSpy = vi.spyOn(api, 'createSession').mockReturnValue(createSessionPromise)

    useTelemetryStore.getState().startRecordSession('micro_assembly_v1', 'Assemble ICF target component')

    await vi.waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith('micro_assembly_v1', 'Assemble ICF target component'),
    )
    expect(useTelemetryStore.getState().recording).toBe(false)
    expect(useTelemetryStore.getState().recordSession.phase).toBe('starting')
    expect(useTelemetryStore.getState().recordSession.phaseStartedAt).toBeNull()
    expect(useTelemetryStore.getState().recordSession.recorderElapsedS).toBe(0)
    expect(useTelemetryStore.getState().recordSession.recorderTotalS).toBe(-1)

    vi.setSystemTime(new Date('2026-05-22T00:00:02.000Z'))
    resolveCreateSession({ ok: true })
    await Promise.resolve()
    await Promise.resolve()

    expect(useTelemetryStore.getState().recording).toBe(true)
    expect(useTelemetryStore.getState().recordSession.phase).toBe('recording')
    expect(useTelemetryStore.getState().recordSession.phaseStartedAt).toBe(Date.now())
  })

  it('ignores duplicate record session starts while the first start is pending', async () => {
    let resolveCreateSession: (value: api.RecordSessionCommandResponse) => void = () => undefined
    const createSessionPromise = new Promise<api.RecordSessionCommandResponse>((resolve) => {
      resolveCreateSession = resolve
    })
    vi.spyOn(api, 'fetchRecordStatus').mockResolvedValue({ active: false, recording: false })
    vi.spyOn(api, 'putConfig').mockResolvedValue(structuredClone(defaultConfig))
    const createSessionSpy = vi.spyOn(api, 'createSession').mockReturnValue(createSessionPromise)

    useTelemetryStore.getState().startRecordSession('micro_assembly_demo', 'Assemble ICF target component')
    useTelemetryStore.getState().startRecordSession('micro_assembly_demo', 'Assemble ICF target component')

    await vi.waitFor(() => expect(createSessionSpy).toHaveBeenCalledTimes(1))

    resolveCreateSession({ ok: true })
    await vi.waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('recording'))
  })

  it('syncs to backend recording when create reports an already-active session', async () => {
    const busyError = Object.assign(new Error('record session already active'), {
      status: 409,
      code: 'RECORDING_BUSY',
    }) as api.ApiCommandError
    const fetchRecordStatusSpy = vi.spyOn(api, 'fetchRecordStatus')
      .mockResolvedValueOnce({ active: false, recording: false })
      .mockResolvedValueOnce({
        active: true,
        recording: true,
        datasetName: 'micro_assembly_demo',
        task: 'Assemble ICF target component',
        elapsedS: 1.25,
        frameCount: 38,
        fps: 30,
      })
    vi.spyOn(api, 'putConfig').mockResolvedValue(structuredClone(defaultConfig))
    vi.spyOn(api, 'createSession').mockRejectedValue(busyError)

    useTelemetryStore.getState().startRecordSession('micro_assembly_demo', 'Assemble ICF target component')

    await vi.waitFor(() => expect(fetchRecordStatusSpy).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('recording'))
    expect(useTelemetryStore.getState().recording).toBe(true)
    expect(useTelemetryStore.getState().recordSession.recorderElapsedS).toBe(1.25)
    expect(useTelemetryStore.getState().recordSession.recorderFrameCount).toBe(38)
  })

  it('ignores duplicate return-to-origin requests while the first request is pending', async () => {
    let resolveReturnOrigin: (value: { ok: boolean }) => void = () => undefined
    const returnOriginPromise = new Promise<{ ok: boolean }>((resolve) => {
      resolveReturnOrigin = resolve
    })
    const returnOriginSpy = vi.spyOn(api, 'returnMotionOriginSide').mockReturnValue(returnOriginPromise)
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { ...state.frame.motionEnabled, right: true },
        motionAxisEnabled: {
          ...state.frame.motionAxisEnabled,
          right: [true, true, true, true, true, false],
        },
      },
    }))

    const first = useTelemetryStore.getState().returnRecordMotionOrigin('right')
    const second = useTelemetryStore.getState().returnRecordMotionOrigin('right')

    expect(returnOriginSpy).toHaveBeenCalledTimes(1)
    expect(returnOriginSpy).toHaveBeenCalledWith('right')

    resolveReturnOrigin({ ok: true })
    await Promise.all([first, second])
  })

  it('returns only required record arms through recorded-origin side commands', async () => {
    const returnOriginSpy = vi.spyOn(api, 'returnMotionOriginSide').mockResolvedValue({ ok: true })
    const homeAllSpy = vi.spyOn(api, 'homeAll').mockResolvedValue({ ok: true })
    const homeMotionSideSpy = vi.spyOn(api, 'homeMotionSide').mockResolvedValue({ ok: true })
    const captureOriginSpy = vi.spyOn(api, 'captureMotionOrigin').mockResolvedValue({ ok: true })
    useTelemetryStore.setState((state) => ({
      frame: {
        ...state.frame,
        motionEnabled: { ...state.frame.motionEnabled, left: true },
        motionAxisEnabled: {
          ...state.frame.motionAxisEnabled,
          left: [true, true, true, true, true, true],
        },
      },
      recordSession: {
        ...state.recordSession,
        resetRequiredSides: ['left'],
      },
    }))

    useTelemetryStore.getState().homeRecordArms()

    await waitFor(() => expect(returnOriginSpy).toHaveBeenCalledTimes(1))
    expect(returnOriginSpy.mock.calls).toEqual([['left']])
    expect(homeAllSpy).not.toHaveBeenCalled()
    expect(homeMotionSideSpy).not.toHaveBeenCalled()
    expect(captureOriginSpy).not.toHaveBeenCalled()
  })

  it('does not expose origin capture from the record workflow store', () => {
    expect(useTelemetryStore.getState()).not.toHaveProperty('captureRecordMotionOrigin')
  })

  it('saves the latest Kalman config before creating a record session', async () => {
    let resolveConfigSave: (value: typeof defaultConfig) => void = () => undefined
    const configSavePromise = new Promise<typeof defaultConfig>((resolve) => {
      resolveConfigSave = resolve
    })
    vi.spyOn(api, 'fetchRecordStatus').mockResolvedValue({ active: false, recording: false })
    const putConfigSpy = vi.spyOn(api, 'putConfig').mockReturnValue(configSavePromise)
    const createSessionSpy = vi.spyOn(api, 'createSession').mockResolvedValue({ ok: true })

    act(() => {
      useTelemetryStore.setState((state) => ({
        config: {
          ...state.config,
          teleop: { ...state.config.teleop, kalmanFilterEnabled: true },
        },
      }))
    })

    useTelemetryStore.getState().startRecordSession('micro_assembly_v1', 'Assemble ICF target component')

    await waitFor(() =>
      expect(putConfigSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          teleop: expect.objectContaining({ kalmanFilterEnabled: true }),
        }),
      ),
    )
    expect(createSessionSpy).not.toHaveBeenCalled()

    resolveConfigSave(structuredClone(useTelemetryStore.getState().config))
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    expect(createSessionSpy).toHaveBeenCalledWith('micro_assembly_v1', 'Assemble ICF target component')
  })

  it('aligns the UI record timer to the backend recorder elapsed time', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-22T00:00:02.000Z'))

    vi.spyOn(api, 'fetchRecordStatus').mockResolvedValue({ active: false, recording: false })
    vi.spyOn(api, 'createSession').mockResolvedValue({
      ok: true,
      data: {
        active: true,
        recording: true,
        elapsedS: 0.55,
        frameCount: 16,
        fps: 30,
      },
      ts: Date.now(),
    })

    useTelemetryStore.getState().startRecordSession('micro_assembly_v1', 'Assemble ICF target component')

    await vi.waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('recording'))
    const session = useTelemetryStore.getState().recordSession
    expect(session.phase).toBe('recording')
    expect(session.recorderElapsedS).toBeCloseTo(0.55, 3)
    expect(session.recorderFrameCount).toBe(16)
    expect(session.recorderFps).toBe(30)
    expect(Date.now() - (session.phaseStartedAt ?? 0)).toBeGreaterThanOrEqual(550)
  })

  it('keeps the recording progress bar visible when backend status omits the total duration', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-22T00:00:05.000Z'))
    window.history.pushState({}, '', '/record')
    useTelemetryStore.setState((state) => ({
      recording: true,
      recordSession: {
        ...state.recordSession,
        phase: 'recording',
        phaseStartedAt: Date.now() - 5000,
        episodeTimeS: 20,
        recorderElapsedS: 5,
        recorderTotalS: -1,
      },
    }))

    render(<App />)

    expect(
      screen.getAllByRole('progressbar').some((bar) => bar.getAttribute('aria-valuenow') === '25'),
    ).toBe(true)
  })

  it('blocks record precheck until Omega.7 and gripper diagnostics are recognized', async () => {
    window.history.pushState({}, '', '/record')
    useTelemetryStore.setState((state) => ({
      diagnostics: state.diagnostics.map((item) =>
        item.key === 'omega7' || item.key === 'gripper' ? { ...item, status: 'error' } : item,
      ),
    }))

    render(<App />)
    fireEvent.click(document.querySelector<HTMLButtonElement>('.record-action-stack button')!)

    const confirmButton = document.querySelector<HTMLButtonElement>('.ant-modal-footer .ant-btn-primary')!
    expect(confirmButton).toBeDisabled()
    const precheckDoneCheckbox = screen.getAllByRole('checkbox').at(-1)!
    fireEvent.click(precheckDoneCheckbox)
    await new Promise((resolve) => window.setTimeout(resolve, 0))
    expect(confirmButton).toBeDisabled()

    act(() => {
      useTelemetryStore.setState((state) => ({
        config: {
          ...state.config,
          teleop: { ...state.config.teleop, leftConnected: true, rightConnected: true },
        },
        frame: {
          ...state.frame,
          teleopHands: state.frame.teleopHands.map((hand) => ({
            ...hand,
            connected: true,
            lastReadOk: true,
            message: 'test fixture',
          })),
        },
        diagnostics: state.diagnostics.map((item) =>
          item.key === 'omega7' || item.key === 'gripper' ? { ...item, status: 'ok' } : item,
        ),
      }))
    })

    await waitFor(() => expect(confirmButton).not.toBeDisabled())
  })

  it('blocks record precheck until both Omega.7 logical hands are connected', async () => {
    window.history.pushState({}, '', '/record')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: false, rightConnected: false },
      },
      frame: {
        ...state.frame,
        teleopHands: state.frame.teleopHands.map((hand) => ({
          ...hand,
          connected: false,
          lastReadOk: false,
          message: 'logical teleop hand disconnected',
        })),
      },
      diagnostics: state.diagnostics.map((item) =>
        item.key === 'omega7' || item.key === 'gripper' ? { ...item, status: 'ok' } : item,
      ),
    }))

    render(<App />)
    fireEvent.click(document.querySelector<HTMLButtonElement>('.record-action-stack button')!)

    const confirmButton = document.querySelector<HTMLButtonElement>('.ant-modal-footer .ant-btn-primary')!
    const precheckDoneCheckbox = screen.getAllByRole('checkbox').at(-1)!
    fireEvent.click(precheckDoneCheckbox)
    await new Promise((resolve) => window.setTimeout(resolve, 500))

    expect(confirmButton).toBeDisabled()
  })

  it('keeps the quality report visible when finish is pressed during saving, then finishes after acceptance', async () => {
    useTelemetryStore.setState((state) => ({
      recording: true,
      recordSession: {
        ...state.recordSession,
        phase: 'recording',
        phaseStartedAt: Date.now(),
      },
    }))

    act(() => {
      useTelemetryStore.getState().saveRecordEpisode()
      useTelemetryStore.getState().finishRecordSession()
    })

    expect(useTelemetryStore.getState().recordSession.phase).toBe('saving')
    await waitFor(() => expect(useTelemetryStore.getState().recordSession.latestQualityReport).not.toBeNull())
    expect(useTelemetryStore.getState().recordSession.phase).toBe('reviewing')
    expect(useTelemetryStore.getState().recording).toBe(false)

    act(() => {
      useTelemetryStore.getState().acceptRecordQualityReport()
    })

    await waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('idle'))
    expect(useTelemetryStore.getState().recording).toBe(false)
  })

  it('does not resume recording in the UI when episode save fails after backend stopped recording', async () => {
    vi.spyOn(api, 'saveEpisode').mockRejectedValue(new Error('native LeRobot save_episode failed'))
    vi.spyOn(api, 'fetchRecordStatus').mockResolvedValue({ active: true, recording: false })
    useTelemetryStore.setState((state) => ({
      recording: true,
      recordSession: {
        ...state.recordSession,
        phase: 'recording',
        phaseStartedAt: Date.now() - 1000,
        recorderFrameCount: 30,
        recorderElapsedS: 1,
      },
    }))

    act(() => {
      useTelemetryStore.getState().saveRecordEpisode()
    })

    await waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('resetting'))
    expect(useTelemetryStore.getState().recording).toBe(false)
    expect(useTelemetryStore.getState().recordSession.latestQualityReport).toBeNull()
  })

  it('ignores reset skip unless the record workflow is resetting', () => {
    useTelemetryStore.setState((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'idle',
        phaseStartedAt: null,
      },
    }))

    act(() => {
      useTelemetryStore.getState().skipRecordReset()
    })

    expect(useTelemetryStore.getState().recordSession.phase).toBe('idle')
    expect(useTelemetryStore.getState().recording).toBe(false)
  })

  it('keeps reset workflow when backend rejects reset skip', async () => {
    const error = Object.assign(new Error('record reset work origin is not ready'), {
      status: 409,
      code: 'RECORD_RESET_NOT_READY',
    }) as api.ApiCommandError
    const skipSpy = vi.spyOn(api, 'skipReset').mockRejectedValue(error)
    useTelemetryStore.setState((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'resetting',
        resetPending: true,
        resetRequiredSides: ['left'],
        resetReturnedSides: ['left'],
        resetReady: true,
      },
    }))

    act(() => {
      useTelemetryStore.getState().skipRecordReset()
    })

    await waitFor(() => expect(skipSpy).toHaveBeenCalledTimes(1))
    expect(useTelemetryStore.getState().recordSession.phase).toBe('resetting')
    expect(useTelemetryStore.getState().recording).toBe(false)
  })

  it('does not call reset skip while required work origin is not returned', () => {
    const skipSpy = vi.spyOn(api, 'skipReset').mockResolvedValue({ ok: true })
    useTelemetryStore.setState((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'resetting',
        resetPending: true,
        resetRequiredSides: ['left'],
        resetReturnedSides: [],
        resetReady: false,
      },
    }))

    act(() => {
      useTelemetryStore.getState().skipRecordReset()
    })

    expect(skipSpy).not.toHaveBeenCalled()
    expect(useTelemetryStore.getState().recordSession.phase).toBe('resetting')
    expect(useTelemetryStore.getState().recording).toBe(false)
  })

  it('ignores duplicate reset skip while the first request is pending', async () => {
    let resolveSkip: (value: { ok: boolean; data: api.RecordStatusApi; ts: number }) => void = () => undefined
    const skipPromise = new Promise<{ ok: boolean; data: api.RecordStatusApi; ts: number }>((resolve) => {
      resolveSkip = resolve
    })
    const skipSpy = vi.spyOn(api, 'skipReset').mockReturnValue(skipPromise)
    useTelemetryStore.setState((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'resetting',
        resetPending: true,
        resetRequiredSides: ['left'],
        resetReturnedSides: ['left'],
        resetReady: true,
      },
    }))

    act(() => {
      useTelemetryStore.getState().skipRecordReset()
      useTelemetryStore.getState().skipRecordReset()
    })

    expect(skipSpy).toHaveBeenCalledTimes(1)

    resolveSkip({ ok: true, data: { active: true, recording: true, fps: 30 }, ts: Date.now() })
    await waitFor(() => expect(useTelemetryStore.getState().recordSession.phase).toBe('recording'))
  })

  it('pauses for reset after discarding an active record episode', () => {
    const discardSpy = vi.spyOn(api, 'discardEpisode').mockResolvedValue({ ok: true, data: {}, ts: Date.now() })
    useTelemetryStore.setState((state) => ({
      recording: true,
      recordSession: {
        ...state.recordSession,
        phase: 'recording',
        phaseStartedAt: Date.now() - 1000,
        recorderFrameCount: 12,
        recorderElapsedS: 1,
      },
    }))

    act(() => {
      useTelemetryStore.getState().discardRecordEpisode()
    })

    const state = useTelemetryStore.getState()
    expect(discardSpy).toHaveBeenCalledTimes(1)
    expect(state.recording).toBe(false)
    expect(state.recordSession.phase).toBe('resetting')
    expect(state.recordSession.recorderTotalS).toBe(state.recordSession.resetTimeS)
  })

  it('ignores quality report acceptance when no report is pending', () => {
    useTelemetryStore.setState((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'saving',
        latestQualityReport: null,
      },
    }))

    act(() => {
      useTelemetryStore.getState().acceptRecordQualityReport()
    })

    expect(useTelemetryStore.getState().recordSession.phase).toBe('saving')
    expect(useTelemetryStore.getState().recordSession.latestQualityReport).toBeNull()
  })

  it('does not skip reset from a repeated Space shortcut after saving', () => {
    window.history.pushState({}, '', '/record')
    render(<App />)
    useTelemetryStore.setState((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'resetting',
        phaseStartedAt: Date.now(),
        latestQualityReport: null,
      },
    }))

    fireEvent.keyDown(window, { key: ' ', repeat: true })

    expect(useTelemetryStore.getState().recordSession.phase).toBe('resetting')
    expect(useTelemetryStore.getState().recording).toBe(false)
  })

  it('does not bind F12 to emergency stop', () => {
    render(<App />)
    fireEvent.keyDown(window, { key: 'F12' })
    expect(screen.queryByText('安全恢复确认')).not.toBeInTheDocument()
    expect(useTelemetryStore.getState().frame.dangerIndex).not.toBe(1.1)
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

  it('submits transient Hugging Face upload settings from the dataset page', async () => {
    const hubToggleSpy = vi.spyOn(api, 'updateDatasetHubApi').mockResolvedValue({ ok: true, data: { pushToHub: true }, ts: Date.now() })
    const pushSpy = vi.spyOn(api, 'pushDatasetApi').mockResolvedValue({ ok: true, data: { queued: true, jobId: 'job-1' }, ts: Date.now() })

    useTelemetryStore.setState({ config: structuredClone(defaultConfig) })
    render(<DatasetView />)
    fireEvent.click(screen.getByRole('button', { name: /Hub 上传/ }))
    fireEvent.click(screen.getByRole('switch', { name: 'Hub 上传开关' }))
    fireEvent.change(screen.getByPlaceholderText('org/dataset-name'), { target: { value: 'lab/micro_assembly_v1' } })
    fireEvent.change(screen.getByLabelText('Local path'), { target: { value: 'E:\\data group\\micro_assembly_v1' } })
    fireEvent.change(screen.getByPlaceholderText('hf_xxx'), { target: { value: 'hf_transient_secret' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Private' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Dry-run' }))
    fireEvent.click(screen.getByRole('button', { name: '开始上传' }))

    await waitFor(() => expect(hubToggleSpy).toHaveBeenCalledWith(true))
    await waitFor(() => expect(pushSpy).toHaveBeenCalledWith('micro_assembly_v1', {
      repoId: 'lab/micro_assembly_v1',
      localPath: 'E:\\data group\\micro_assembly_v1',
      token: 'hf_transient_secret',
      private: true,
      dryRun: false,
    }))
    expect(await screen.findByText('Hub upload queued')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('hf_transient_secret')).not.toBeInTheDocument()
  }, 30000)

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

  it('formats backend command errors with detail codes and messages', () => {
    expect(formatApiErrorMessage(409, { detail: { code: 'RECORDING_BUSY', message: 'record session already active' } })).toBe(
      '录制会话已在运行，请先结束当前会话后再开始新的录制。（409 RECORDING_BUSY）',
    )
  })

  it('maps PICO API helpers to backend routes', async () => {
    await expect(api.connectPicoAdb()).resolves.toMatchObject({ path: '/pico/adb/connect' })
    await expect(api.checkPicoStatus()).resolves.toMatchObject({ path: '/pico/status/check' })
    await expect(api.startPicoVision()).resolves.toMatchObject({ path: '/pico/vision/start' })
    await expect(api.stopPicoVision()).resolves.toMatchObject({ path: '/pico/vision/stop' })
  })

  it('renders visual PICO status and current IMX335 camera hardware', () => {
    window.history.pushState({}, '', '/settings#teleop')
    render(<App />)

    const picoCard = document.querySelector<HTMLElement>('#teleop')
    const picoStrip = picoCard?.querySelector<HTMLElement>('.pico-status-strip')
    expect(picoStrip).toBeTruthy()
    expect(picoStrip).toHaveTextContent('10.90.129.166:5555')
    expect(picoStrip).toHaveTextContent('IF 13')
    expect(picoStrip).toHaveTextContent('IMX335 / index 1')

    const cameraCards = [
      document.querySelector<HTMLElement>('#camera-global'),
      document.querySelector<HTMLElement>('#camera-left'),
      document.querySelector<HTMLElement>('#camera-right'),
    ]
    for (const card of cameraCards) {
      expect(card).toBeTruthy()
      expect(card).toHaveTextContent('IMX335')
      expect(card?.querySelector('.camera-status-strip')).toBeTruthy()
    }
    expect(defaultConfig.cameras.global).toBe('IMX335 / index 1')
    expect(defaultConfig.cameras.wristLeft).toBe('IMX335 / index 0')
    expect(defaultConfig.cameras.wristRight).toBe('IMX335 / index 2')
  })

  it('syncs PICO connection results into the global status bar', async () => {
    window.history.pushState({}, '', '/settings#teleop')
    vi.spyOn(api, 'checkPicoStatus').mockResolvedValue({ ok: true, data: { ok: false, message: 'device offline' } })

    render(<App />)

    const statusBar = document.querySelector<HTMLElement>('.status-bar')
    expect(statusBar).toBeTruthy()
    await waitFor(() => expect(statusBar).toHaveTextContent('CAM 3/3'))
    expect(statusBar).toHaveTextContent('PICO 待检查')

    fireEvent.click((await screen.findByText('检查状态')).closest('button')!)

    await waitFor(() => expect(statusBar).toHaveTextContent('PICO 离线'))
    expect(statusBar).toHaveTextContent('10.90.129.166')
  })

  it('renders hardware-specific settings from the reference manual', () => {
    window.history.pushState({}, '', '/settings#motion-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        teleop: { ...state.config.teleop, leftConnected: false, rightConnected: false },
      },
    }))
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
    expect(screen.getAllByText('相对工作原点位置').length).toBeGreaterThan(0)
    expect(screen.getAllByText('绝对软限位下限').length).toBeGreaterThan(0)
    expect(screen.getAllByText('命令力限制 N').length).toBeGreaterThan(0)
    expect(screen.getByText('HAL API 地址')).toBeInTheDocument()
    expect(screen.queryByText(/\?\?\?\?/)).not.toBeInTheDocument()
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
    expect(screen.getAllByText('记录工作原点')[0].closest('button')).toBeEnabled()
    expect(screen.getAllByText('连接主手').length).toBeGreaterThan(0)
    expect(screen.getAllByText('回工作原点').length).toBeGreaterThan(0)
    expect(screen.getAllByText('平移比例').length).toBeGreaterThan(0)
    expect(screen.getAllByText('旋转比例').length).toBeGreaterThan(0)
    expect(screen.queryByText('数据轮询周期 ms')).not.toBeInTheDocument()
    expect(screen.getAllByText('命令更新周期 ms').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Off / Free').length).toBeGreaterThan(0)
    expect(defaultConfig.teleop.stabilityMode).toBe('off')
    expect(defaultConfig.teleop.engine).toBe('hal_native')
    expect(defaultConfig.teleop.controlMode).toBe('incremental_position')
    expect(defaultConfig.teleop.mappingMode).toBe('direct')
    expect(defaultConfig.teleop.nativeLoopHz).toBe(100)
    expect(defaultConfig.teleop.swapHands).toBe(false)
    expect(defaultConfig.teleop.swapTeleopChannels).toBe(true)
    expect(defaultConfig.teleop.leftSoftLimitMin).toEqual([-25000, -37500, -37500, -5, -30, -7])
    expect(defaultConfig.teleop.leftSoftLimitMax).toEqual([25000, 37500, 37500, 95, 30, 7])
    expect(defaultConfig.teleop.rightEnabledAxes).toEqual([true, true, true, true, true, false])
    expect(defaultConfig.teleop.rightSoftLimitMin).toEqual([-25000, -37500, -37500, -95, -30, -7])
    expect(defaultConfig.teleop.rightSoftLimitMax).toEqual([25000, 37500, 37500, 5, 30, 7])
    expect(defaultConfig.motion.rotationWorkLimits.left.roll).toEqual({ min: -5, max: 95 })
    expect(defaultConfig.motion.rotationWorkLimits.left.pitch).toEqual({ min: -30, max: 30 })
    expect(defaultConfig.motion.rotationWorkLimits.right.roll).toEqual({ min: -95, max: 5 })
    expect(defaultConfig.motion.rotationWorkLimits.right.pitch).toEqual({ min: -30, max: 30 })
    expect(defaultConfig.teleop.leftTranslationScale).toBe(1)
    expect(defaultConfig.teleop.rightTranslationScale).toBe(1)
    expect(defaultConfig.teleop.leftRotationScale).toBe(1)
    expect(defaultConfig.teleop.rightRotationScale).toBe(1)
    expect(defaultConfig.teleop.leftAxisOutputScale).toEqual([0.6, 0.5, 0.375, 0.6, 0.08, 0.1])
    expect(defaultConfig.teleop.rightAxisOutputScale).toEqual([0.6, 0.5, 0.375, 0.6, 0.08, 0.001])
    expect(defaultConfig.teleop.translationStartVelocityUmS).toBe(600)
    expect(defaultConfig.teleop.translationMaxVelocityUmS).toBe(8000)
    expect(defaultConfig.teleop.rotationStartVelocityDegS).toBe(1)
    expect(defaultConfig.teleop.rotationMaxVelocityDegS).toBe(12)
    expect(defaultConfig.teleop.motionProfileAccSec).toBe(0.05)
    expect(defaultConfig.teleop.motionProfileDecSec).toBe(0.05)
    expect(defaultConfig.teleop.translationPulseDeadband).toBe(2)
    expect(defaultConfig.teleop.rotationPulseDeadband).toBe(2)
    expect(defaultConfig.teleop.translationInputEpsilon).toBe(0.00002)
    expect(defaultConfig.teleop.rotationInputEpsilon).toBe(0.03)
    expect(defaultConfig.teleop.translationMinActivePulse).toBe(3)
    expect(defaultConfig.teleop.rotationMinActivePulse).toBe(3)
    expect(defaultConfig.teleop.continuousMicroConfirmTicks).toBe(0)
    expect(defaultConfig.teleop.leftImpulseCoeff).toEqual([-5000000, -5000000, -10000000, 1667, 2500, -333.3333])
    expect(defaultConfig.teleop.rightImpulseCoeff).toEqual([-5000000, 10000000, -5000000, 1667, -2500, 3333.333])
    expect(defaultConfig.teleop.gripperTeleop.rightGapInvert).toBe(false)
    expect(defaultConfig.teleop.gripperTeleop.gripTorque).toBe(1)
    expect(defaultConfig.teleop.gripperTeleop.releaseTorque).toBe(1)
    expect(defaultConfig.gripper.icfTargetProtectionEnabled).toBe(true)
    expect(defaultConfig.gripper.icfTargetMinGapMm).toBe(1.02)
    expect(defaultConfig.motion.leftProfile.translation.maxSpeed).toBe(4000)
    expect(defaultConfig.motion.leftProfile.rotation.maxSpeed).toBe(6)
    expect(defaultConfig.motion.kinematics.rightPhysicalAxis).toEqual([2, 0, 5, 8, 1, 7])
    expect(screen.queryByText(/OpenXR/)).not.toBeInTheDocument()
  }, 30000)

  it('routes PICO vision controls through backend commands', async () => {
    window.history.pushState({}, '', '/settings#teleop')
    const connectSpy = vi.spyOn(api, 'connectPicoAdb').mockResolvedValue({ ok: true })
    const statusSpy = vi.spyOn(api, 'checkPicoStatus').mockResolvedValue({ ok: true, data: { ok: false, message: 'device offline' } })
    const startSpy = vi.spyOn(api, 'startPicoVision').mockResolvedValue({ ok: true })
    const stopSpy = vi.spyOn(api, 'stopPicoVision').mockResolvedValue({ ok: true })

    render(<App />)

    fireEvent.click((await screen.findByText('连接无线 ADB')).closest('button')!)
    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByText('检查状态').closest('button')!)
    await waitFor(() => expect(statusSpy).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByText('启动视觉').closest('button')!)
    await waitFor(() => expect(startSpy).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByText('停止视觉').closest('button')!)
    await waitFor(() => expect(stopSpy).toHaveBeenCalledTimes(1))

    expect(statusSpy).toHaveBeenCalledTimes(1)
    expect(startSpy).toHaveBeenCalledTimes(1)
    expect(stopSpy).toHaveBeenCalledTimes(1)
  })

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

  it('places camera parameter actions inside the tuning panel', () => {
    window.history.pushState({}, '', '/settings#camera-global')
    render(<App />)
    const globalCameraCard = document.querySelector<HTMLElement>('#camera-global')
    expect(globalCameraCard).toBeTruthy()
    const headerActions = globalCameraCard!.querySelector<HTMLElement>('.hardware-config-actions')
    const tuningPanel = globalCameraCard!.querySelector<HTMLElement>('.camera-tuning-panel')

    expect(tuningPanel).toBeTruthy()
    expect(within(tuningPanel!).getByText('曝光 / 增益')).toBeInTheDocument()
    expect(within(tuningPanel!).getByText('应用参数')).toBeInTheDocument()
    expect(within(tuningPanel!).getByText('重连预览')).toBeInTheDocument()
    expect(headerActions?.textContent ?? '').not.toContain('应用参数')
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
  }, 20000)

  it('shows before and after values before applying camera parameters', async () => {
    window.history.pushState({}, '', '/settings#camera-global')
    render(<App />)
    const globalCameraCard = document.querySelector<HTMLElement>('#camera-global')
    expect(globalCameraCard).toBeTruthy()

    fireEvent.click(within(globalCameraCard!).getByRole('button', { name: '应用参数' }))

    const dialog = (await screen.findByText('应用全局相机参数')).closest('[role="dialog"]') as HTMLElement
    expect(dialog).toBeTruthy()
    expect(within(dialog).getByText('当前')).toBeInTheDocument()
    expect(within(dialog).getByText('将应用')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Exposure').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('Gain').length).toBeGreaterThan(0)
    expect(within(dialog).getByRole('button', { name: '确认应用' })).toBeInTheDocument()
  }, 10000)

  it('shows before and after state before recording the work origin', async () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    const leftCard = document.querySelector<HTMLElement>('#motion-left')
    expect(leftCard).toBeTruthy()

    fireEvent.click(within(leftCard!).getByText('记录工作原点').closest('button')!)

    const dialog = (await screen.findByText('左臂记录工作原点')).closest('[role="dialog"]') as HTMLElement
    expect(dialog).toBeTruthy()
    expect(within(dialog).getByText('当前')).toBeInTheDocument()
    expect(within(dialog).getByText('将应用')).toBeInTheDocument()
    expect(within(dialog).getByText('确认记录工作原点')).toBeInTheDocument()
  }, 10000)

  it('uses no-write hardware home from the motion card home action', async () => {
    window.history.pushState({}, '', '/settings#motion-left')
    const homeSpy = vi.spyOn(api, 'homeMotionSide').mockResolvedValue({ ok: true })
    const captureSpy = vi.spyOn(api, 'captureMotionOrigin').mockResolvedValue({ ok: true })

    render(<App />)
    const leftCard = document.querySelector<HTMLElement>('#motion-left')
    expect(leftCard).toBeTruthy()

    fireEvent.click(within(leftCard!).getByText('回硬件零点').closest('button')!)
    const dialog = (await screen.findByText('左臂回硬件零点')).closest('[role="dialog"]') as HTMLElement
    fireEvent.click(within(dialog).getByText('确认回硬件零点').closest('button')!)

    await waitFor(() => expect(homeSpy).toHaveBeenCalledWith('left'))
    expect(captureSpy).not.toHaveBeenCalled()
  }, 10000)

  it('shows before and after values before executing a gripper target', async () => {
    window.history.pushState({}, '', '/settings#gripper-left')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        gripper: { ...state.config.gripper, leftEnabled: true },
      },
    }))
    render(<App />)
    const leftGripperCard = document.querySelector<HTMLElement>('#gripper-left')
    expect(leftGripperCard).toBeTruthy()

    fireEvent.click(within(leftGripperCard!).getByText('执行目标').closest('button')!)

    const dialog = (await screen.findByText('左臂夹爪执行目标')).closest('[role="dialog"]') as HTMLElement
    expect(dialog).toBeTruthy()
    expect(within(dialog).getByText('当前')).toBeInTheDocument()
    expect(within(dialog).getByText('将应用')).toBeInTheDocument()
    expect(within(dialog).getByText('目标开合')).toBeInTheDocument()
    expect(within(dialog).getByText('确认执行')).toBeInTheDocument()
  }, 10000)

  it('shows before and after values before executing a manual gripper target', async () => {
    window.history.pushState({}, '', '/settings#manual')
    useTelemetryStore.setState((state) => ({
      config: {
        ...state.config,
        gripper: { ...state.config.gripper, leftEnabled: true },
      },
    }))
    render(<App />)
    const leftGripperCard = screen.getByText('左臂夹爪手动控制').closest('article')
    expect(leftGripperCard).toBeTruthy()

    fireEvent.click(within(leftGripperCard as HTMLElement).getByRole('button', { name: '执行目标' }))

    const dialog = (await screen.findByText('左臂夹爪执行目标')).closest('[role="dialog"]') as HTMLElement
    expect(dialog).toBeTruthy()
    expect(within(dialog).getByText('当前')).toBeInTheDocument()
    expect(within(dialog).getByText('将应用')).toBeInTheDocument()
    expect(within(dialog).getByText('确认执行')).toBeInTheDocument()
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

  it('disables right-arm Yaw in manual controls', () => {
    expect(isManualAxisDisabled('left', 'Yaw')).toBe(false)
    expect(isManualAxisDisabled('right', 'Roll')).toBe(false)
    expect(isManualAxisDisabled('right', 'Yaw')).toBe(true)
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

  it('shows millisecond timestamps and full native diagnostic messages in the log panel', () => {
    useTelemetryStore.setState({
      logPanelOpen: true,
      logs: [
        {
          id: 9001,
          ts: Date.UTC(2026, 4, 19, 14, 18, 46, 827),
          channel: '[HAL]',
          level: 'INFO',
          msg: 'component=TELEOP event=teleop_status seq=9001 monoMs=42 session_id=test op_id=- sideMap=left->right raw=[X:0.001] reqPulse=[X:9] emitPulse=[X:8] filtered=[X:1.6]',
        },
      ],
    })

    render(<LogPanel />)

    expect(screen.getByText((text) => text.endsWith('.827'))).toBeInTheDocument()
    expect(screen.getByText(/event=teleop_status/)).toHaveAttribute(
      'title',
      'component=TELEOP event=teleop_status seq=9001 monoMs=42 session_id=test op_id=- sideMap=left->right raw=[X:0.001] reqPulse=[X:9] emitPulse=[X:8] filtered=[X:1.6]',
    )
  })

  it('filters teleop Roll diagnostics from quick log panel presets', () => {
    useTelemetryStore.setState({
      logPanelOpen: true,
      logs: [
        {
          id: 9001,
          ts: Date.UTC(2026, 4, 19, 14, 18, 46, 827),
          channel: '[HAL]',
          level: 'INFO',
          msg: 'component=TELEOP event=teleop_axis_trace seq=9001 monoMs=42 session_id=test op_id=- source=left target=right axis=Roll rawPose=[Roll:12.5] rawDelta=[Roll:0.5]',
        },
        {
          id: 9002,
          ts: Date.UTC(2026, 4, 19, 14, 18, 47, 827),
          channel: '[HAL]',
          level: 'INFO',
          msg: 'component=TELEOP event=teleop_axis_trace seq=9002 monoMs=43 session_id=test op_id=- source=left target=right axis=X rawPose=[X:0.001] rawDelta=[X:0.001]',
        },
      ],
    })

    render(<LogPanel />)

    expect(screen.getByText(/axis=Roll/)).toBeInTheDocument()
    expect(screen.getByText(/axis=X/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))

    expect(screen.getByText(/axis=Roll/)).toBeInTheDocument()
    expect(screen.queryByText(/axis=X/)).not.toBeInTheDocument()
  })

  it('toggles teleop hands with one logical connect button', async () => {
    window.history.pushState({}, '', '/settings#teleop-left')
    render(<App />)
    const teleopCard = document.querySelector<HTMLElement>('#teleop-left')
    expect(teleopCard).toBeTruthy()
    const teleopButton = teleopCard!.querySelectorAll<HTMLButtonElement>('.hardware-config-actions button')[1]
    expect(teleopButton).toBeTruthy()

    fireEvent.click(teleopButton!)
    await waitFor(() => expect(useTelemetryStore.getState().config.teleop.leftConnected).toBe(true))

    const disconnectButton = teleopCard!.querySelectorAll<HTMLButtonElement>('.hardware-config-actions button')[1]
    expect(disconnectButton).toBeTruthy()
    await waitFor(() => expect(disconnectButton!).not.toBeDisabled())
    fireEvent.click(disconnectButton!)
    await waitFor(() => expect(useTelemetryStore.getState().config.teleop.leftConnected).toBe(false))
  })

  it('renders work origin controls and the startup return switch', () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    expect(screen.getAllByText('记录工作原点')).toHaveLength(2)
    expect(screen.getAllByText('回硬件零点')).toHaveLength(2)
    expect(screen.getByText('恢复上个工作原点')).toBeInTheDocument()
    expect(screen.getByText('开机回工作原点')).toBeInTheDocument()
    expect(screen.queryByText('设为采集零点')).not.toBeInTheDocument()
    expect(screen.queryByText('清除零点')).not.toBeInTheDocument()
    expect(screen.getAllByText('Roll -5~95° / Pitch ±30° · Yaw ±7°').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Roll -95~5° / Pitch ±30° · Yaw disabled').length).toBeGreaterThan(0)
  })

  it('updates the work origin state when the side controls are used', async () => {
    window.history.pushState({}, '', '/settings#motion-left')
    render(<App />)
    const leftCard = document.querySelector<HTMLElement>('#motion-left')
    expect(leftCard).toBeTruthy()

    fireEvent.click(within(leftCard!).getByText('记录工作原点').closest('button')!)
    fireEvent.click((await screen.findByText('确认记录工作原点')).closest('button')!)
    await waitFor(() => expect(within(leftCard!).getByText('已记录')).toBeInTheDocument())
    await waitFor(() => expect(within(leftCard!).getByText(/工作原点位置：左\[/)).toBeInTheDocument())
    await waitFor(() => expect(useTelemetryStore.getState().logs.at(-1)?.msg).toContain('工作原点位置：左['))
  }, 10000)

  it('requires a second confirmation before overwriting a drifted motion origin', async () => {
    window.history.pushState({}, '', '/settings#motion-left')
    const drift: api.MotionOriginCaptureDrift = {
      requiresConfirmation: true,
      thresholds: { translationUm: 5000, rotationDeg: 1 },
      sides: [
        {
          side: 'left',
          baseline: 'current',
          axes: [
            {
              axis: 'X',
              deltaPulse: 100000,
              deltaUi: 20000,
              absDeltaUi: 20000,
              unit: 'um',
              threshold: 5000,
            },
          ],
        },
      ],
    }
    const nextOrigin = {
      ...defaultConfig.motion.origin,
      leftValid: true,
      leftPulse: [100000, 0, 0, 0, 0, 0],
    }
    const savedConfig = structuredClone(defaultConfig)
    savedConfig.motion = {
      ...savedConfig.motion,
      origin: nextOrigin,
    }
    const captureSpy = vi.spyOn(api, 'captureMotionOrigin')
      .mockRejectedValueOnce(Object.assign(new Error('command failed'), {
        code: 'ORIGIN_DRIFT_CONFIRM_REQUIRED',
        drift,
      }))
      .mockResolvedValueOnce({
        ok: true,
        data: {
          origin: nextOrigin,
          config: savedConfig,
          originCaptureDrift: drift,
        },
      })

    render(<App />)
    const leftCard = document.querySelector<HTMLElement>('#motion-left')
    expect(leftCard).toBeTruthy()

    fireEvent.click(within(leftCard!).getByText('记录工作原点').closest('button')!)
    fireEvent.click((await screen.findByText('确认记录工作原点')).closest('button')!)

    const driftDialog = (await screen.findByText('左臂工作原点漂移过大')).closest('[role="dialog"]') as HTMLElement
    expect(within(driftDialog).getByText('确认覆盖零点')).toBeInTheDocument()
    expect(within(driftDialog).getByText('左.X 20000 um')).toBeInTheDocument()
    expect(captureSpy).toHaveBeenNthCalledWith(1, 'left', undefined)

    fireEvent.click(within(driftDialog).getByText('确认覆盖零点').closest('button')!)

    await waitFor(() => expect(captureSpy).toHaveBeenNthCalledWith(2, 'left', { confirmLargeDrift: true }))
    await waitFor(() => expect(useTelemetryStore.getState().config.motion.origin.leftPulse[0]).toBe(100000))
    expect(useTelemetryStore.getState().config.motion.homeReference.leftPulse).toEqual(defaultConfig.motion.homeReference.leftPulse)
  }, 10000)
})
