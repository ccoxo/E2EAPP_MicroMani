import { Tag, Typography } from 'antd'
import { useTelemetryStore } from '../stores/telemetry'
import { MetricPill } from './MetricPill'

export function StatusBar() {
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const wsOk = useTelemetryStore((state) => state.frame.wsOk)
  const wsHz = useTelemetryStore((state) => state.frame.resource.wsHz)
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const episodeCount = useTelemetryStore((state) => state.frame.episodeCount)
  const frameCount = useTelemetryStore((state) => state.frame.frameCount)
  const uiFps = useTelemetryStore((state) => state.frame.resource.uiFps)
  const phase = useTelemetryStore((state) => state.recordSession.phase)
  const recorderFps = useTelemetryStore((state) => state.recordSession.recorderFps)
  const recorderLateFrames = useTelemetryStore((state) => state.recordSession.recorderLateFrames)
  const dangerState = dangerIndex >= 1 ? 'error' : dangerIndex > 0.7 ? 'warn' : 'ok'
  const recordingFpsState = recorderFps >= 28 ? 'ok' : recorderFps >= 20 ? 'warn' : 'error'

  return (
    <footer className="status-bar">
      <MetricPill state={halOk ? 'ok' : 'error'} label="HAL" />
      <MetricPill state={wsOk ? 'ok' : 'error'} label={`WS ${wsHz}Hz`} />
      <MetricPill state={dangerState} label={`Safety ${dangerIndex.toFixed(2)}`} />
      {phase === 'recording' && (
        <Tag color="error" style={{ animation: 'blink 1s step-end infinite' }}>
          ● REC
        </Tag>
      )}
      {phase === 'saving' && <Tag color="processing">保存中</Tag>}
      {phase === 'resetting' && <Tag color="purple">复位中</Tag>}
      {phase !== 'idle' && (
        <MetricPill state={recordingFpsState} label={`录制 ${recorderFps.toFixed(1)}Hz`} />
      )}
      {recorderLateFrames > 0 && <Tag color="warning">迟帧 {recorderLateFrames}</Tag>}
      <Typography.Text type="secondary">
        Episode #{episodeCount} · Frame {frameCount} · UI {uiFps.toFixed(1)} FPS · Backend M0
      </Typography.Text>
    </footer>
  )
}
