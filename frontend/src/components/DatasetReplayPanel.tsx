import type { Participation } from '../types'
import { ParticipationSelector } from './ParticipationSelector'
import { useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Play, RotateCcw, ShieldCheck, Square } from 'lucide-react'
import { apiBase, mockMode, postCommand } from '../api'
import { UiButton, UiTag, UiTitle } from './ui'

type ReplayTiming = {
  requestedSpeed: number
  plannedDurationS: number
  plannedAverageSpeed: number
  minimumSpeed: number
  limitedFrames: number
  limitingChannels: string[]
  gripperFeedbackLimited: boolean
  profileSource: string
  hardwareValidated: boolean
  profile: { translationVelocityUiPerSec: number; rotationVelocityUiPerSec: number; accTimeSec: number; decTimeSec: number }
}
type ReplayStatus = {
  active: boolean
  phase: string
  frame: number
  totalFrames: number
  datasetId?: string
  episodeId?: string
  error?: string
  warning?: string
  timing?: ReplayTiming
  elapsedS?: number
  effectiveSpeed?: number
  feedbackWaitS?: number
  waitingChannels?: string[]
}
const phaseLabels: Record<string, string> = {
  idle: '待命', loading: '校验中', aligning: '起点对齐中', running: '回放中',
  settling: '等待末帧到位', completed: '已完成', stopped: '已停止', failed: '失败',
  following: '等待设备跟随',
}

export function DatasetReplayPanel({ datasetId, episodeId, recordedParticipation }: { datasetId: string; episodeId: string; recordedParticipation?: Participation | null }) {
  const [selected, setSelected] = useState<Participation>(recordedParticipation ?? { version: 'appstation.participation.v1', arms: [], grippers: [] })
  const [status, setStatus] = useState<ReplayStatus | null>(null)
  const [verified, setVerified] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [speed, setSpeed] = useState('0.25')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState('')
  const [inspectedTiming, setInspectedTiming] = useState<ReplayTiming | null>(null)
  const selectionKey = `${datasetId}/${episodeId}`
  const currentSelection = useRef(selectionKey)
  currentSelection.current = selectionKey
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    setSelected(recordedParticipation ?? { version: 'appstation.participation.v1', arms: [], grippers: [] })
    setVerified(false)
    setConfirmed(false)
    setSummary('')
    setInspectedTiming(null)
    setError('')
  }, [datasetId, episodeId, recordedParticipation])
  useEffect(() => {
    if (mockMode) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const response = await fetch(`${apiBase}/api/replay/status`, { signal: AbortSignal.timeout(3000) })
        if (!response.ok) throw new Error('无法获取回放状态')
        const payload = await response.json() as { data: ReplayStatus }
        if (!disposed) { setStatus(payload.data); setConnected(true) }
      } catch {
        if (!disposed) { setConnected(false); setError('回放状态连接中断，请核验设备状态') }
      } finally {
        if (!disposed) timer = setTimeout(poll, 500)
      }
    }
    void poll()
    return () => { disposed = true; clearTimeout(timer) }
  }, [])
  const run = async (operation: 'inspect' | 'start' | 'stop') => {
    setPending(true)
    setError('')
    try {
      const path = operation === 'stop' ? '/api/replay/stop'
        : `/api/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}/replay/${operation}`
      const result = await postCommand(path, operation === 'stop' ? {} : { participation: selected, speed: Number(speed), ...(operation === 'start' ? { confirmMotion: confirmed } : {}) }) as {
        data: ReplayStatus & { frames: number; fps: number; durationS: number }
      }
      if (operation === 'inspect') {
        if (currentSelection.current !== selectionKey) return
        setVerified(true)
        setSummary(`${result.data.frames} 帧 · ${result.data.fps} Hz · ${result.data.durationS.toFixed(1)} 秒`)
        setInspectedTiming(result.data.timing ?? null)
      } else {
        setStatus(result.data)
        setInspectedTiming(null)
        setVerified(false)
        setConfirmed(false)
      }
    } catch (cause) {
      setInspectedTiming(null)
      setError(cause instanceof Error ? cause.message : '回放操作失败')
      setVerified(false)
    } finally { setPending(false) }
  }
  const errorDetail = error || status?.error || ''
  const diagnosticError = errorDetail.startsWith('目标位置对齐超时') || errorDetail.startsWith('实际位置偏离回放目标') || errorDetail.startsWith('回放跟随等待超时')
  const errorMessage = diagnosticError ? errorDetail.split('\n')[0] : errorDetail.includes('Parquet magic bytes')
    ? '数据文件无法读取，请检查 Parquet 文件是否完整。'
    : errorDetail.length > 120 ? '回放操作未完成，请展开详情查看原因。' : errorDetail
  const progress = status?.totalFrames ? Math.min(100, Math.max(0, status.frame / status.totalFrames * 100)) : 0
  const timing = status?.active ? (status.datasetId === datasetId && status.episodeId === episodeId ? status.timing : null) : inspectedTiming
  return (
    <section className="panel-surface dataset-replay" aria-label="示教数据真机回放测试">
      <header className="dataset-replay-header">
        <span className="dataset-replay-icon"><RotateCcw size={20} aria-hidden="true" /></span>
        <div className="dataset-replay-heading">
          <UiTitle level={3}>示教数据真机回放测试</UiTitle>
          <p>对齐录制起点，再按帧执行参与臂与夹爪动作</p>
        </div>
        <UiTag tone={!connected ? 'muted' : status?.phase === 'failed' ? 'error' : status?.active ? 'processing' : status?.phase === 'completed' ? 'success' : 'default'}>
          {!connected ? '未连接' : status ? phaseLabels[status.phase] ?? status.phase : '待命'}
        </UiTag>
      </header>
      <ParticipationSelector value={selected} disabled={Boolean(recordedParticipation) || pending || status?.active} onChange={(value) => { setSelected(value); setVerified(false); setConfirmed(false); setSummary(''); setInspectedTiming(null) }} />
      {!recordedParticipation && <p>此片段未记录参与侧，请明确选择参与臂与夹爪后校验。未选设备不会接收回放动作。</p>}
      <div className="dataset-replay-toolbar">
        <div className="dataset-replay-actions">
          <UiButton icon={<CheckCircle2 size={15} aria-hidden="true" />} disabled={!selected.arms.length || mockMode || pending || status?.active || !connected} onClick={() => void run('inspect')}>校验回放数据</UiButton>
          <UiButton variant="primary" icon={<Play size={15} aria-hidden="true" />} disabled={!verified || !confirmed || pending || status?.active || !connected} onClick={() => void run('start')}>对齐起点并真机回放</UiButton>
          <UiButton danger icon={<Square size={14} aria-hidden="true" />} disabled={!status?.active} onClick={() => void run('stop')}>停止真机回放</UiButton>
        </div>
        <fieldset className="dataset-replay-speed" disabled={pending || status?.active}>
          <legend>回放倍率</legend>
          <div>
            {['0.1', '0.25', '0.5', '1'].map((value) => (
              <label key={value}>
                <input type="radio" name={`replay-speed-${datasetId}-${episodeId}`} aria-label={`${value} 倍速`} value={value} checked={speed === value} onChange={() => { setSpeed(value); setVerified(false); setConfirmed(false); setSummary(''); setInspectedTiming(null) }} />
                <span>{value}×</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      {timing && <div className="dataset-replay-timing" aria-label="回放时间规划">
        <p>请求 {timing.requestedSpeed}× · 计划平均 {timing.plannedAverageSpeed.toFixed(3)}× · 最慢段 {timing.minimumSpeed.toFixed(3)}× · 预计 {timing.plannedDurationS.toFixed(1)} 秒</p>
        <p>{timing.limitedFrames ? `${timing.limitedFrames} 段延时，受限通道：${timing.limitingChannels.join('、')}` : '轨迹无需额外延时'}。预计时长不含起点对齐、通信与反馈等待{timing.gripperFeedbackLimited ? '；夹爪耗时以实际反馈为准' : ''}。</p>
        <p>参数来源：{timing.profileSource === 'teleop' ? '当前示教配置' : timing.profileSource} · 平移 {timing.profile.translationVelocityUiPerSec} μm/s · 旋转 {timing.profile.rotationVelocityUiPerSec}°/s · 加/减速 {timing.profile.accTimeSec}/{timing.profile.decTimeSec} 秒。{!timing.hardwareValidated && '尚无逐轴及负载实机验收记录。'}</p>
      </div>}
      <label className={`dataset-replay-confirm${confirmed ? ' is-confirmed' : ''}`}>
        <input type="checkbox" checked={confirmed} disabled={!verified || pending || status?.active} onChange={(event) => setConfirmed(event.target.checked)} />
        <span><strong>确认工作区可安全运动</strong><span>请先停止示教、录制和策略运行；勾选后允许所选臂与夹爪移动到录制起点并回放。</span></span>
      </label>
      <div className="dataset-replay-status">
        <span>{pending ? '正在处理请求…' : verified ? '数据校验通过' : '回放进度'}{summary && <span className="dataset-replay-summary">{summary}</span>}</span>
        <span className="dataset-replay-frames">{status?.frame ?? 0}<span> / {status?.totalFrames ?? 0} 帧</span></span>
      </div>
      <progress className="dataset-replay-progress" aria-label="真机回放进度" max={100} value={progress} />
      {status?.active && <p className="dataset-replay-episode">执行片段：{status.datasetId} / {status.episodeId}</p>}
      {status?.elapsedS != null && <p className="dataset-replay-episode">任务已运行 {status.elapsedS.toFixed(1)} 秒 · 实际平均 {(status.effectiveSpeed ?? 0).toFixed(3)}× · 跟随等待 {(status.feedbackWaitS ?? 0).toFixed(1)} 秒{status.phase === 'following' && status.waitingChannels?.length ? ` · 等待：${status.waitingChannels.join('、')}` : ''}</p>}
      {status?.warning && <p className="dataset-replay-episode" role="status">{status.warning}</p>}
      {errorDetail && <div className="dataset-replay-error" role="alert">
        <AlertCircle size={17} aria-hidden="true" />
        <div><strong>{errorMessage}</strong><details open={diagnosticError}><summary>查看错误详情</summary><pre>{errorDetail}</pre></details></div>
      </div>}
      <p className="dataset-replay-footnote"><ShieldCheck size={15} aria-hidden="true" /><span>停止或运行故障将请求硬件急停并锁存。检查设备后，需在手动控制页确认安全才能再次运动。</span></p>
    </section>
  )
}
