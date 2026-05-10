import { Button, Card, Divider, Form, Input, Progress, Select, Spin } from 'antd'
import React from 'react'
import { useTelemetryStore } from '../../stores/telemetry'

const PRESET_TASKS = [
  { value: 'Assemble ICF target component', label: 'Assemble ICF target component' },
  { value: 'Pick and place micro component', label: 'Pick and place micro component' },
  { value: 'Precision insertion task', label: 'Precision insertion task' },
]

const phaseConfig = {
  idle: { label: '就绪', color: '#8c8c8c', barColor: '#d9d9d9' },
  recording: { label: '录制中', color: '#cf1322', barColor: '#cf1322' },
  resetting: { label: '复位中', color: '#722ed1', barColor: '#722ed1' },
  saving: { label: '保存中', color: '#1677ff', barColor: '#1677ff' },
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
  { key: 'T', desc: '力觉 Tare' },
  { key: 'R', desc: '回工作原点' },
  { key: 'F12', desc: '硬件急停' },
  { key: 'P', desc: '暂停遥操作' },
]

const formatTime = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}.${Math.floor((s % 1) * 10)}`

interface EpisodeControlPanelProps {
  onStartSession: () => void
}

export default function EpisodeControlPanel({ onStartSession }: EpisodeControlPanelProps) {
  const phase = useTelemetryStore((s) => s.recordSession.phase)
  const elapsedS = useTelemetryStore((s) => s.recordSession.recorderElapsedS)
  const totalS = useTelemetryStore((s) => s.recordSession.recorderTotalS)
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
  const tareRecordForceSensors = useTelemetryStore((s) => s.tareRecordForceSensors)
  const toggleRecordClutch = useTelemetryStore((s) => s.toggleRecordClutch)
  const setRecordSpeedMode = useTelemetryStore((s) => s.setRecordSpeedMode)

  const cfg = phaseConfig[phase]
  const busy = phase === 'saving' || phase === 'finishing'

  return (
    <Card size="small" title="录制控制" styles={{ body: { padding: '10px 12px' } }}>
      <Form layout="vertical" size="small" style={{ marginBottom: 0 }}>
        <Form.Item label="任务描述" style={{ marginBottom: 8 }}>
          <Select
            value={task}
            onChange={setTask}
            options={PRESET_TASKS}
            showSearch
            allowClear={false}
            disabled={phase !== 'idle'}
          />
        </Form.Item>
        <Form.Item label="数据集名称" style={{ marginBottom: 0 }}>
          <Input
            value={datasetName}
            onChange={(e) => setDatasetName(e.target.value)}
            placeholder="micro_assembly_v1"
            disabled={phase !== 'idle'}
          />
        </Form.Item>
      </Form>

      <Divider style={{ margin: '10px 0' }} />

      <div className="record-episode-progress-head">
        <span>#{String(currentEpisode).padStart(3, '0')}</span>
        <small>
          目标 {targetEpisodes} 条 / 已完成 {savedEpisodes}
        </small>
      </div>
      <Progress
        percent={targetEpisodes > 0 ? Math.round((savedEpisodes / targetEpisodes) * 100) : 0}
        showInfo={false}
        strokeColor="#185FA5"
        size="small"
      />

      <Divider style={{ margin: '10px 0' }} />

      <div className="record-phase-row">
        <div>
          <div style={{ fontSize: 10, color: cfg.color }}>{cfg.label}</div>
          <div className="record-phase-time">{formatTime(elapsedS)}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 10, color: '#8c8c8c' }}>剩余</div>
          <div className="record-phase-remaining">
            {totalS >= 0 ? formatTime(Math.max(0, totalS - elapsedS)) : '--:--.-'}
          </div>
        </div>
      </div>
      <Progress
        percent={totalS >= 0 && totalS > 0 ? Math.round((elapsedS / totalS) * 100) : 0}
        showInfo={false}
        strokeColor={cfg.barColor}
        size="small"
      />

      <Divider style={{ margin: '10px 0' }} />

      {busy && (
        <div style={{ textAlign: 'center', padding: '8px 0' }}>
          <Spin size="small" />
          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4 }}>{cfg.label}</div>
        </div>
      )}

      {phase === 'idle' && (
        <div className="record-action-stack">
          <Button type="primary" block onClick={onStartSession}>
            开始采集会话
          </Button>
          <Button size="small" block onClick={tareRecordForceSensors}>
            力觉 Tare
          </Button>
        </div>
      )}

      {phase === 'recording' && (
        <div className="record-action-stack">
          <div className="record-action-grid">
            <Button type="primary" onClick={saveRecordEpisode} block disabled={busy}>
              保存 <kbd style={kbdStyle}>Space</kbd>
            </Button>
            <Button onClick={discardRecordEpisode} block disabled={busy}>
              丢弃重录
            </Button>
          </div>
          <Button danger block size="small" onClick={finishRecordSession} disabled={busy}>
            ESC - 结束采集会话并 finalize()
          </Button>
        </div>
      )}

      {phase === 'resetting' && (
        <div className="record-action-stack">
          <Button type="primary" block onClick={skipRecordReset} disabled={busy}>
            跳过复位，立即开始
          </Button>
          <Button danger block size="small" onClick={finishRecordSession} disabled={busy}>
            ESC - 结束采集会话并 finalize()
          </Button>
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
          <Button size="small" onClick={toggleRecordClutch}>离合器</Button>
          <Button size="small" onClick={() => setRecordSpeedMode('coarse')}>粗</Button>
          <Button size="small" onClick={() => setRecordSpeedMode('medium')}>中</Button>
          <Button size="small" onClick={() => setRecordSpeedMode('fine')}>细</Button>
        </div>
      )}
    </Card>
  )
}
