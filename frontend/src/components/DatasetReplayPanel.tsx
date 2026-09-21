import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Play, RotateCcw, ShieldCheck, Square } from 'lucide-react'
import { apiBase, mockMode, postCommand } from '../api'
import { UiButton, UiTag, UiTitle } from './ui'

type ReplayStatus = {
  active: boolean
  phase: string
  frame: number
  totalFrames: number
  datasetId?: string
  episodeId?: string
  error?: string
}
const phaseLabels: Record<string, string> = {
  idle: '待命', loading: '校验中', aligning: '起点对齐中', running: '回放中',
  settling: '等待末帧到位', completed: '已完成', stopped: '已停止', failed: '失败',
}

export function DatasetReplayPanel({ datasetId, episodeId }: { datasetId: string; episodeId: string }) {
  const [status, setStatus] = useState<ReplayStatus | null>(null)
  const [verified, setVerified] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [speed, setSpeed] = useState('0.25')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [summary, setSummary] = useState('')
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    setVerified(false)
    setConfirmed(false)
    setSummary('')
    setError('')
  }, [datasetId, episodeId])
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
      const result = await postCommand(path, operation === 'start' ? { speed: Number(speed), confirmMotion: confirmed } : {}) as {
        data: ReplayStatus & { frames: number; fps: number; durationS: number }
      }
      if (operation === 'inspect') {
        setVerified(true)
        setSummary(`${result.data.frames} 帧 · ${result.data.fps} Hz · ${result.data.durationS.toFixed(1)} 秒`)
      } else {
        setStatus(result.data)
        setVerified(false)
        setConfirmed(false)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '回放操作失败')
      setVerified(false)
    } finally { setPending(false) }
  }
  const errorDetail = error || status?.error || ''
  const errorMessage = errorDetail.includes('Parquet magic bytes')
    ? '数据文件无法读取，请检查 Parquet 文件是否完整。'
    : errorDetail.length > 120 ? '回放操作未完成，请展开详情查看原因。' : errorDetail
  const progress = status?.totalFrames ? Math.min(100, Math.max(0, status.frame / status.totalFrames * 100)) : 0
  return (
    <section className="panel-surface dataset-replay" aria-label="示教数据真机回放测试">
      <header className="dataset-replay-header">
        <span className="dataset-replay-icon"><RotateCcw size={20} aria-hidden="true" /></span>
        <div className="dataset-replay-heading">
          <UiTitle level={3}>示教数据真机回放测试</UiTitle>
          <p>对齐录制起点，再按帧执行双臂与夹爪动作</p>
        </div>
        <UiTag tone={!connected ? 'muted' : status?.phase === 'failed' ? 'error' : status?.active ? 'processing' : status?.phase === 'completed' ? 'success' : 'default'}>
          {!connected ? '未连接' : status ? phaseLabels[status.phase] ?? status.phase : '待命'}
        </UiTag>
      </header>
      <div className="dataset-replay-toolbar">
        <div className="dataset-replay-actions">
          <UiButton icon={<CheckCircle2 size={15} aria-hidden="true" />} disabled={mockMode || pending || status?.active || !connected} onClick={() => void run('inspect')}>校验回放数据</UiButton>
          <UiButton variant="primary" icon={<Play size={15} aria-hidden="true" />} disabled={!verified || !confirmed || pending || status?.active || !connected} onClick={() => void run('start')}>对齐起点并真机回放</UiButton>
          <UiButton danger icon={<Square size={14} aria-hidden="true" />} disabled={!status?.active} onClick={() => void run('stop')}>停止真机回放</UiButton>
        </div>
        <fieldset className="dataset-replay-speed" disabled={pending || status?.active}>
          <legend>回放倍率</legend>
          <div>
            {['0.1', '0.25', '0.5', '1'].map((value) => (
              <label key={value}>
                <input type="radio" name={`replay-speed-${datasetId}-${episodeId}`} aria-label={`${value} 倍速`} value={value} checked={speed === value} onChange={() => setSpeed(value)} />
                <span>{value}×</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      <label className={`dataset-replay-confirm${confirmed ? ' is-confirmed' : ''}`}>
        <input type="checkbox" checked={confirmed} disabled={!verified || pending || status?.active} onChange={(event) => setConfirmed(event.target.checked)} />
        <span><strong>确认工作区可安全运动</strong><span>请先停止示教、录制和策略运行；勾选后允许双臂与夹爪移动到录制起点并回放。</span></span>
      </label>
      <div className="dataset-replay-status">
        <span>{pending ? '正在处理请求…' : verified ? '数据校验通过' : '回放进度'}{summary && <span className="dataset-replay-summary">{summary}</span>}</span>
        <span className="dataset-replay-frames">{status?.frame ?? 0}<span> / {status?.totalFrames ?? 0} 帧</span></span>
      </div>
      <progress className="dataset-replay-progress" aria-label="真机回放进度" max={100} value={progress} />
      {status?.active && <p className="dataset-replay-episode">执行片段：{status.datasetId} / {status.episodeId}</p>}
      {errorDetail && <div className="dataset-replay-error" role="alert">
        <AlertCircle size={17} aria-hidden="true" />
        <div><strong>{errorMessage}</strong><details><summary>查看错误详情</summary><pre>{errorDetail}</pre></details></div>
      </div>}
      <p className="dataset-replay-footnote"><ShieldCheck size={15} aria-hidden="true" /><span>停止或运行故障将请求硬件急停并锁存。检查设备后，需在手动控制页确认安全才能再次运动。</span></p>
    </section>
  )
}
