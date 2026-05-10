import { Tag, Typography } from 'antd'
import { useTelemetryStore } from '../stores/telemetry'
import { MetricPill } from './MetricPill'

export function StatusBar() {
  const frame = useTelemetryStore((state) => state.frame)
  const recordSession = useTelemetryStore((state) => state.recordSession)
  const dangerState = frame.dangerIndex >= 1 ? 'error' : frame.dangerIndex > 0.7 ? 'warn' : 'ok'
  const recordingFpsState = recordSession.recorderFps >= 28 ? 'ok' : recordSession.recorderFps >= 20 ? 'warn' : 'error'

  return (
    <footer className="status-bar">
      <MetricPill state={frame.halOk ? 'ok' : 'error'} label="HAL" />
      <MetricPill state={frame.wsOk ? 'ok' : 'error'} label={`WS ${frame.resource.wsHz}Hz`} />
      <MetricPill state={dangerState} label={`Safety ${frame.dangerIndex.toFixed(2)}`} />
      {recordSession.phase === 'recording' && (
        <Tag color="error" style={{ animation: 'blink 1s step-end infinite' }}>
          ● REC
        </Tag>
      )}
      {recordSession.phase === 'saving' && <Tag color="processing">保存中</Tag>}
      {recordSession.phase === 'resetting' && <Tag color="purple">复位中</Tag>}
      {recordSession.phase !== 'idle' && (
        <MetricPill state={recordingFpsState} label={`录制 ${recordSession.recorderFps.toFixed(1)}Hz`} />
      )}
      {recordSession.recorderLateFrames > 0 && <Tag color="warning">迟帧 {recordSession.recorderLateFrames}</Tag>}
      <Typography.Text type="secondary">
        Episode #{frame.episodeCount} · Frame {frame.frameCount} · UI {frame.resource.uiFps.toFixed(1)} FPS · Backend M0
      </Typography.Text>
    </footer>
  )
}
