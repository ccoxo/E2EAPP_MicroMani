/*
 * 阅读导航 01｜入口与界面
 * 职责：组织导航、页面内容和全局状态区域，提供所有页面共用的布局。
 * 先看：TopStatus → AppLayout。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { telemetryStaleAfterMs } from '../hardwareStatus'
import { useTelemetryStore } from '../stores/telemetry'
import { LogPanel } from './LogPanel'
import { MetricPill } from './MetricPill'
import { GlobalEmergencyStopButton } from './GlobalEmergencyStopButton'
import { RouteErrorBoundary } from './RouteErrorBoundary'
import { ModuleErrorBoundary } from './ModuleErrorBoundary'
import { SafetyOverlay } from './SafetyOverlay'
import { StatusBar } from './StatusBar'
import { ControlLeaseStatus } from './ControlLeaseStatus'
import { controlLeaseBlockReason } from '../stores/controlLease'
import { UiButton, UiSegmented, UiSpace, UiTag, UiText } from './ui'

const navItems = [
  { to: '/', label: '主页', icon: Home },
  { to: '/record', label: '录制', icon: RadioTower },
  { to: '/dataset', label: '数据集', icon: Database },
  { to: '/model', label: '模型', icon: Bot },
  { to: '/fine-tune', label: '微调', icon: FlaskConical },
  { to: '/auto', label: '自动', icon: PlayCircle },
  { to: '/settings', label: '设置', icon: Settings },
]

type WorkMode = 'Record' | 'Auto' | 'Manual'
const workModeRoutes: Record<WorkMode, string> = {
  Record: '/record',
  Auto: '/auto',
  Manual: '/settings#manual',
}
/** 由 URL 推导工作模式；主页/数据集等无对应模式时不高亮。 */
function workModeFromLocation(pathname: string, hash: string): WorkMode | null {
  if (pathname === '/record') return 'Record'
  if (pathname === '/auto') return 'Auto'
  if (pathname === '/settings' && hash.replace('#', '') === 'manual') return 'Manual'
  return null
}
/** 渲染当前界面单元，并连接所需数据。 */
function TopStatus({ clock }: { clock: string }) {
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const wsHz = useTelemetryStore((state) => state.frame.resource.wsHz)
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const safetyLatched = useTelemetryStore((state) => Boolean(state.frame.forceStatus?.safety?.latched))
  const emergencyRequested = useTelemetryStore((state) => state.controlSafety.emergencyRequested)
  const leaseBlocked = useTelemetryStore((state) => Boolean(controlLeaseBlockReason(state.controlLease)))
  const telemetryLive = useTelemetryStore((state) => state.telemetryLink.state === 'live' && state.frame.wsOk
    && state.telemetryLink.lastFrameReceivedAt !== null
    && Date.now() - state.telemetryLink.lastFrameReceivedAt <= telemetryStaleAfterMs)
  const phase = useTelemetryStore((state) => state.recordSession.phase)
  const recorderFps = useTelemetryStore((state) => state.recordSession.recorderFps)
  const safetyLocked = emergencyRequested || safetyLatched
  const safetyAvailable = telemetryLive && halOk && !leaseBlocked

  return (
    <UiSpace className="top-status" size={8}>
      <MetricPill state={!telemetryLive ? 'pending' : halOk ? 'ok' : 'error'} label={telemetryLive ? 'HAL' : 'HAL 未知'} />
      <MetricPill state={telemetryLive ? 'ok' : 'error'} label={`WS ${telemetryLive ? `${wsHz}Hz` : '不可用'}`} />
      <MetricPill
        state={safetyLocked ? 'error' : !safetyAvailable ? 'pending' : dangerIndex >= 1 ? 'error' : dangerIndex > 0.7 ? 'warn' : 'ok'}
        label={`Safety ${safetyLocked ? 'LOCK' : safetyAvailable ? dangerIndex.toFixed(2) : '不可用'}`}
      />
      {phase === 'recording' && (
        <UiTag tone="error" style={{ animation: 'blink 1s step-end infinite' }}>
          ● REC
        </UiTag>
      )}
      {phase === 'saving' && <UiTag tone="processing">保存中</UiTag>}
      {phase === 'resetting' && <UiTag tone="muted">复位中</UiTag>}
      {phase !== 'idle' && <UiTag>{recorderFps.toFixed(1)} Hz</UiTag>}
      <UiTag tone={telemetryLive ? 'processing' : 'default'}>Backend {telemetryLive && Number.isFinite(wsHz) ? `${wsHz}Hz` : '频率未知'}</UiTag>
      <UiText>{clock}</UiText>
    </UiSpace>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export function AppLayout() {
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const [clock, setClock] = useState(() => dayjs().format('HH:mm:ss'))
  const navigate = useNavigate()
  const location = useLocation()
  const workMode = workModeFromLocation(location.pathname, location.hash)

  useEffect(() => {
    const timer = window.setInterval(() => setClock(dayjs().format('HH:mm:ss')), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div className="brand-lockup" onClick={() => navigate('/')} role="button" tabIndex={0}>
          <SquareStack size={20} />
          <div>
            <UiText strong>AppStation</UiText>
            <UiText secondary>Robot Hardware Console</UiText>
          </div>
        </div>
        <UiSegmented
          value={workMode}
          options={[
            { label: 'Record', value: 'Record' as const },
            { label: 'Auto', value: 'Auto' as const },
            { label: 'Manual', value: 'Manual' as const },
          ]}
          onChange={(value) => navigate(workModeRoutes[value])}
        />
        <ModuleErrorBoundary name="顶部状态"><TopStatus clock={clock} /></ModuleErrorBoundary>
      </header>

      <div className="work-area">
        <nav className="left-nav" aria-label="主导航">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} to={to} title={label}>
              <Icon size={19} />
              <span>{label}</span>
            </NavLink>
          ))}
          <UiButton
            aria-label="侧栏急停"
            className="nav-emergency"
            danger
            icon={<Activity size={16} />}
            onClick={triggerEmergencyStop}
          >
            急停
          </UiButton>
        </nav>
        <main className="main-content">
          <ModuleErrorBoundary name="控制租约提示"><ControlLeaseStatus /></ModuleErrorBoundary>
          <RouteErrorBoundary key={location.pathname}>
            <Outlet />
          </RouteErrorBoundary>
        </main>
      </div>

      <ModuleErrorBoundary name="日志面板"><LogPanel /></ModuleErrorBoundary>
      <ModuleErrorBoundary name="底部状态"><StatusBar /></ModuleErrorBoundary>
      <ModuleErrorBoundary name="安全提示"><SafetyOverlay /></ModuleErrorBoundary>
      <ModuleErrorBoundary name="急停显示" fallback={
        <div className="floating-emergency-stack">
          <div role="alert">急停状态显示异常</div>
          <button type="button" className="ui-btn ui-btn-danger floating-emergency-stop" aria-label="全局急停"
            onClick={() => useTelemetryStore.getState().triggerEmergencyStop()}>
            急停
          </button>
        </div>
      }>
        <GlobalEmergencyStopButton />
      </ModuleErrorBoundary>
      <UiButton aria-label="打开硬件设置" className="floating-settings" icon={<SlidersHorizontal size={16} />} onClick={() => navigate('/settings')} />
    </div>
  )
}
