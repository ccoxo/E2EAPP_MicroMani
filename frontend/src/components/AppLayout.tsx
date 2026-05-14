import { Button, Segmented, Space, Tag, Tooltip, Typography } from 'antd'
import dayjs from 'dayjs'
import {
  Activity,
  Bot,
  Database,
  FlaskConical,
  Home,
  PlayCircle,
  RadioTower,
  Settings,
  SlidersHorizontal,
  SquareStack,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useTelemetryStore } from '../stores/telemetry'
import { LogPanel } from './LogPanel'
import { MetricPill } from './MetricPill'
import { GlobalEmergencyStopButton } from './GlobalEmergencyStopButton'
import { SafetyOverlay } from './SafetyOverlay'
import { StatusBar } from './StatusBar'

const navItems = [
  { to: '/', label: '主页', icon: Home },
  { to: '/record', label: '录制', icon: RadioTower },
  { to: '/dataset', label: '数据集', icon: Database },
  { to: '/model', label: '模型', icon: Bot },
  { to: '/fine-tune', label: '微调', icon: FlaskConical },
  { to: '/auto', label: '自动', icon: PlayCircle },
  { to: '/settings', label: '设置', icon: Settings },
]

function TopStatus({ clock }: { clock: string }) {
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const wsOk = useTelemetryStore((state) => state.frame.wsOk)
  const wsHz = useTelemetryStore((state) => state.frame.resource.wsHz)
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const phase = useTelemetryStore((state) => state.recordSession.phase)
  const recorderFps = useTelemetryStore((state) => state.recordSession.recorderFps)

  return (
    <Space className="top-status" size={8}>
      <MetricPill state={halOk ? 'ok' : 'error'} label="HAL" />
      <MetricPill state={wsOk ? 'ok' : 'error'} label={`WS ${wsHz}Hz`} />
      <MetricPill state={dangerIndex > 0.7 ? 'warn' : 'ok'} label={`Safety ${dangerIndex.toFixed(2)}`} />
      {phase === 'recording' && (
        <Tag color="error" style={{ animation: 'blink 1s step-end infinite' }}>
          ● REC
        </Tag>
      )}
      {phase === 'saving' && <Tag color="processing">保存中</Tag>}
      {phase === 'resetting' && <Tag color="purple">复位中</Tag>}
      {phase !== 'idle' && <Tag>{recorderFps.toFixed(1)} Hz</Tag>}
      <Tag color="processing">Backend 30Hz</Tag>
      <Typography.Text>{clock}</Typography.Text>
    </Space>
  )
}

export function AppLayout() {
  const selectedMode = useTelemetryStore((state) => state.selectedMode)
  const setMode = useTelemetryStore((state) => state.setMode)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const [clock, setClock] = useState(() => dayjs().format('HH:mm:ss'))
  const navigate = useNavigate()

  useEffect(() => {
    const timer = window.setInterval(() => setClock(dayjs().format('HH:mm:ss')), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'F12') return
      event.preventDefault()
      triggerEmergencyStop()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [triggerEmergencyStop])

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div className="brand-lockup" onClick={() => navigate('/')} role="button" tabIndex={0}>
          <SquareStack size={22} />
          <div>
            <Typography.Text strong>AppStation</Typography.Text>
            <Typography.Text type="secondary">Robot Hardware Console</Typography.Text>
          </div>
        </div>
        <Segmented
          size="small"
          value={selectedMode}
          options={[
            { label: 'Record', value: 'Record' },
            { label: 'Auto', value: 'Auto' },
            { label: 'Manual', value: 'Manual' },
          ]}
          onChange={(value) => setMode(value as 'Record' | 'Auto' | 'Manual')}
        />
        <TopStatus clock={clock} />
      </header>

      <div className="work-area">
        <nav className="left-nav" aria-label="主导航">
          {navItems.map(({ to, label, icon: Icon }) => (
            <Tooltip key={to} title={label} placement="right">
              <NavLink className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} to={to}>
                <Icon size={19} />
                <span>{label}</span>
              </NavLink>
            </Tooltip>
          ))}
          <Button
            className="nav-emergency"
            danger
            icon={<Activity size={17} />}
            onClick={triggerEmergencyStop}
          >
            F12
          </Button>
        </nav>
        <main className="main-content">
          <Outlet />
        </main>
      </div>

      <LogPanel />
      <StatusBar />
      <SafetyOverlay />
      <GlobalEmergencyStopButton />
      <Button className="floating-settings" icon={<SlidersHorizontal size={16} />} onClick={() => navigate('/settings')} />
    </div>
  )
}
