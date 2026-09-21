import type { Participation } from '../../types'
import { ParticipationSelector } from '../ParticipationSelector'
/*
 * 阅读导航 01｜入口与界面
 * 职责：提供录制会话、episode 保存/丢弃及复位流程的主要操作入口。
 * 先看：EpisodeControlPanelProps → EpisodeControlPanel。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { Crosshair } from 'lucide-react'
import React from 'react'
import { hardwareSideForOperatorSide } from '../../data'
import { motionSideReturnOriginReady } from '../../motionReturnReady'
import { useTelemetryStore } from '../../stores/telemetry'
import { UiButton, UiCard, UiProgress, UiSpin } from '../ui'

const PRESET_TASKS = [
  { value: 'Assemble ICF target component', label: 'Assemble ICF target component' },
  { value: 'Pick and place micro component', label: 'Pick and place micro component' },
  { value: 'Precision insertion task', label: 'Precision insertion task' },
]

const phaseConfig = {
  interrupted: { label: '采集中断，待核验；未保存片段可保存或丢弃', color: '#fa8c16', barColor: '#fa8c16' },
  idle: { label: '就绪', color: '#8c8c8c', barColor: '#d9d9d9' },
  starting: { label: '启动中', color: '#1677ff', barColor: '#1677ff' },
  recording: { label: '录制中', color: '#cf1322', barColor: '#cf1322' },
  reviewing: { label: '质检中', color: '#fa8c16', barColor: '#fa8c16' },
  resetting: { label: '复位中', color: '#722ed1', barColor: '#722ed1' },
  saving: { label: '保存中', color: '#1677ff', barColor: '#1677ff' },
  discarding: { label: '丢弃中，等待遥操作停止', color: '#1677ff', barColor: '#1677ff' },
  finishing: { label: '结束中', color: '#52c41a', barColor: '#52c41a' },
}

const kbdStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.15)',
  border: '1px solid rgba(255,255,255,0.3)',
  borderRadius: 3,
  padding: '0 4px',
  fontSize: 10,
  fontFamily: 'monospace',
}

const HINTS = [
  { key: 'Ctrl', desc: '离合器切换' },
  { key: '1/2/3', desc: '速度粗/中/细' },
  { key: 'R', desc: '回工作原点' },
  { key: 'P', desc: '暂停遥操作' },
]
/** 格式化对应数值用于界面展示。 */
const formatTime = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}.${Math.floor((s % 1) * 10)}`

interface EpisodeControlPanelProps {
  onStartSession: () => void
}
/** 渲染当前界面单元，并连接所需数据。 */
export default function EpisodeControlPanel({ onStartSession }: EpisodeControlPanelProps) {
  const participation: Participation = useTelemetryStore((s) => s.recordSession.participation) ?? { version: 'appstation.participation.v1', arms: [], grippers: [] }
  const setParticipation = useTelemetryStore((s) => s.setRecordParticipation)
  const phase = useTelemetryStore((s) => s.recordSession.phase)
  const startError = useTelemetryStore((s) => s.recordSession.startError)
  const elapsedS = useTelemetryStore((s) => s.recordSession.recorderElapsedS)
  const totalS = useTelemetryStore((s) => s.recordSession.recorderTotalS)
  const episodeTimeS = useTelemetryStore((s) => s.recordSession.episodeTimeS)
  const resetTimeS = useTelemetryStore((s) => s.recordSession.resetTimeS)
  const task = useTelemetryStore((s) => s.recordSession.task)
  const setTask = useTelemetryStore((s) => s.setRecordTask)
  const datasetName = useTelemetryStore((s) => s.recordSession.datasetName)
  const setDatasetName = useTelemetryStore((s) => s.setRecordDatasetName)
  const currentEpisode = useTelemetryStore((s) => s.recordSession.currentEpisode)
  const savedEpisodes = useTelemetryStore((s) => s.recordSession.savedEpisodes)
  const targetEpisodes = useTelemetryStore((s) => s.recordSession.targetEpisodes)
  const saveRecordEpisode = useTelemetryStore((s) => s.saveRecordEpisode)
  const discardRecordEpisode = useTelemetryStore((s) => s.discardRecordEpisode)
  const finishRecordSession = useTelemetryStore((s) => s.finishRecordSession)
  const skipRecordReset = useTelemetryStore((s) => s.skipRecordReset)
  const toggleRecordClutch = useTelemetryStore((s) => s.toggleRecordClutch)
  const setRecordSpeedMode = useTelemetryStore((s) => s.setRecordSpeedMode)
  const returnRecordMotionOrigin = useTelemetryStore((s) => s.returnRecordMotionOrigin)
  const motionEnabled = useTelemetryStore((s) => s.frame.motionEnabled)
  const motionAxisEnabled = useTelemetryStore((s) => s.frame.motionAxisEnabled)
  const resetReady = useTelemetryStore((s) => s.recordSession.resetReady)
  const returnOriginInFlight = useTelemetryStore((s) => s.recordSession.returnOriginInFlight)
  const [pendingOriginSide, setPendingOriginSide] = React.useState<'left' | 'right' | null>(null)

  const cfg = phaseConfig[phase]
  const busy = phase === 'starting' || phase === 'saving' || phase === 'discarding' || phase === 'finishing'
  const progressTotalS =
    totalS >= 0 ? totalS : phase === 'recording' ? episodeTimeS : phase === 'resetting' ? resetTimeS : -1
  const phasePercent = progressTotalS > 0 ? Math.max(0, Math.min(100, Math.round((elapsedS / progressTotalS) * 100))) : 0
  const leftHardwareSide = hardwareSideForOperatorSide('left')
  const rightHardwareSide = hardwareSideForOperatorSide('right')
  const leftReturnReady = motionSideReturnOriginReady(leftHardwareSide, motionEnabled, motionAxisEnabled)
  const rightReturnReady = motionSideReturnOriginReady(rightHardwareSide, motionEnabled, motionAxisEnabled)
  const handleReturnOrigin = async (side: 'left' | 'right') => {
    const hardwareSide = hardwareSideForOperatorSide(side)
    if (!motionSideReturnOriginReady(hardwareSide, motionEnabled, motionAxisEnabled)) return
    setPendingOriginSide(side)
    try {
      await returnRecordMotionOrigin(hardwareSide)
    } finally {
      setPendingOriginSide(null)
    }
  }

  return (
    <UiCard title="录制控制" bodyStyle={{ padding: '10px 12px' }}>
      <label style={{ display: 'block', marginBottom: 8 }}>
        <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: '#7a8b9c', marginBottom: 4 }}>任务描述</span>
        <select
          className="ui-select"
          style={{ width: '100%' }}
          value={task}
          onChange={(event) => setTask(event.target.value)}
          disabled={phase !== 'idle'}
        >
          {PRESET_TASKS.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>
      </label>
      <label style={{ display: 'block' }}>
        <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: '#7a8b9c', marginBottom: 4 }}>数据集名称</span>
        <input
          className="ui-input"
          style={{ width: '100%' }}
          value={datasetName}
          onChange={(event) => setDatasetName(event.target.value)}
          placeholder="micro_assembly_v1"
          disabled={phase !== 'idle'}
        />
      </label>

      <ParticipationSelector value={participation} onChange={setParticipation} disabled={phase !== 'idle'} />
      <hr className="ui-divider" />

      <div className="record-episode-progress-head">
        <span>#{String(currentEpisode).padStart(3, '0')}</span>
        <small>
          目标 {targetEpisodes} 条 / 已完成 {savedEpisodes}
        </small>
      </div>
      <UiProgress percent={targetEpisodes > 0 ? Math.round((savedEpisodes / targetEpisodes) * 100) : 0} />

      <hr className="ui-divider" />

      <div className="record-phase-row">
        <div>
          <div style={{ fontSize: 10, color: cfg.color }}>{cfg.label}</div>
          <div className="record-phase-time">{formatTime(elapsedS)}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 10, color: '#8c8c8c' }}>剩余</div>
          <div className="record-phase-remaining">
            {progressTotalS >= 0 ? formatTime(Math.max(0, progressTotalS - elapsedS)) : '--:--.-'}
          </div>
        </div>
      </div>
      <div
        role="progressbar"
        aria-label="当前阶段进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={phasePercent}
        style={{ background: '#e8eef5', height: 6, borderRadius: 99, overflow: 'hidden', marginTop: 4 }}
      >
        <div style={{ width: `${phasePercent}%`, height: '100%', background: cfg.barColor }} />
      </div>

      <hr className="ui-divider" />

      {busy && (
        <div style={{ textAlign: 'center', padding: '8px 0' }}>
          <UiSpin />
          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4 }}>{cfg.label}</div>
        </div>
      )}

      {phase === 'idle' && (
        <div className="record-action-stack">
          {startError && (
            <div role="alert" style={{ color: '#cf1322', background: '#fff1f0', border: '1px solid #ffa39e', borderRadius: 4, padding: 8, overflowWrap: 'anywhere' }}>
              <strong>录制启动失败</strong>
              <div>{startError}</div>
            </div>
          )}
          <UiButton variant="primary" block disabled={!participation.arms.length} onClick={onStartSession}>
            开始采集会话
          </UiButton>
        </div>
      )}

      {(phase === 'recording' || phase === 'interrupted') && (
        <div className="record-action-stack">
          <div className="record-action-grid">
            <UiButton variant="primary" onClick={saveRecordEpisode} block disabled={busy}>
              保存 <kbd style={kbdStyle}>Space</kbd>
            </UiButton>
            <UiButton onClick={discardRecordEpisode} block disabled={busy}>
              丢弃重录
            </UiButton>
          </div>
          <UiButton danger block onClick={finishRecordSession} disabled={busy}>
            ESC - 结束采集会话并 finalize()
          </UiButton>
        </div>
      )}

      {phase === 'resetting' && (
        <div className="record-action-stack">
          <UiButton variant="primary" block onClick={skipRecordReset} disabled={busy || returnOriginInFlight || !resetReady}>
            跳过复位，立即开始
          </UiButton>
          <UiButton danger block onClick={finishRecordSession} disabled={busy}>
            ESC - 结束采集会话并 finalize()
          </UiButton>
        </div>
      )}

      {phase === 'recording' && (
        <div className="record-shortcut-grid">
          {HINTS.map(({ key, desc }) => (
            <div key={key}>
              <kbd style={{ ...kbdStyle, background: 'rgba(0,0,0,0.12)', border: '1px solid rgba(0,0,0,0.2)', color: '#595959' }}>
                {key}
              </kbd>
              <span>{desc}</span>
            </div>
          ))}
        </div>
      )}

      {phase !== 'idle' && (
        <div className="record-teleop-buttons">
          <UiButton onClick={toggleRecordClutch}>离合器</UiButton>
          <UiButton onClick={() => setRecordSpeedMode('coarse')}>粗</UiButton>
          <UiButton onClick={() => setRecordSpeedMode('medium')}>中</UiButton>
          <UiButton onClick={() => setRecordSpeedMode('fine')}>细</UiButton>
        </div>
      )}
      <hr className="ui-divider" />

      <div className="record-origin-actions">
        <UiButton
          icon={<Crosshair size={13} />}
          loading={pendingOriginSide === 'left'}
          disabled={busy || returnOriginInFlight || pendingOriginSide !== null || !leftReturnReady}
          onClick={() => void handleReturnOrigin('left')}
        >
          左从臂回工作原点
        </UiButton>
        <UiButton
          icon={<Crosshair size={13} />}
          loading={pendingOriginSide === 'right'}
          disabled={busy || returnOriginInFlight || pendingOriginSide !== null || !rightReturnReady}
          onClick={() => void handleReturnOrigin('right')}
        >
          右从臂回工作原点
        </UiButton>
      </div>
    </UiCard>
  )
}
