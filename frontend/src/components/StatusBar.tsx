/*
 * 阅读导航 01｜入口与界面
 * 职责：显示连接、进程及遥测相关的全局状态摘要。
 * 先看：StatusBar。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { useTelemetryStore } from '../stores/telemetry'
import { telemetryStaleAfterMs } from '../hardwareStatus'
import { MetricPill } from './MetricPill'
import { UiTag, UiText } from './ui'
import { controlLeaseBlockReason } from '../stores/controlLease'
/** 渲染当前界面单元，并连接所需数据。 */
export function StatusBar() {
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const wsHz = useTelemetryStore((state) => state.frame.resource.wsHz)
  const cameras = useTelemetryStore((state) => state.frame.cameras)
  const picoVision = useTelemetryStore((state) => state.config.picoVision)
  const picoConnection = useTelemetryStore((state) => state.picoConnection)
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const safetyLatched = useTelemetryStore((state) => Boolean(state.frame.forceStatus?.safety?.latched))
  const emergencyRequested = useTelemetryStore((state) => state.controlSafety.emergencyRequested)
  const leaseBlocked = useTelemetryStore((state) => Boolean(controlLeaseBlockReason(state.controlLease)))
  const telemetryLive = useTelemetryStore((state) => state.telemetryLink.state === 'live' && state.frame.wsOk
    && state.telemetryLink.lastFrameReceivedAt !== null
    && Date.now() - state.telemetryLink.lastFrameReceivedAt <= telemetryStaleAfterMs)
  const episodeCount = useTelemetryStore((state) => state.frame.episodeCount)
  const frameCount = useTelemetryStore((state) => state.frame.frameCount)
  const uiFps = useTelemetryStore((state) => state.frame.resource.uiFps)
  const phase = useTelemetryStore((state) => state.recordSession.phase)
  const recorderFps = useTelemetryStore((state) => state.recordSession.recorderFps)
  const recorderLateFrames = useTelemetryStore((state) => state.recordSession.recorderLateFrames)
  const safetyLocked = emergencyRequested || safetyLatched
  const safetyAvailable = telemetryLive && halOk && !leaseBlocked
  const dangerState = safetyLocked ? 'error' : !safetyAvailable ? 'pending' : dangerIndex >= 1 ? 'error' : dangerIndex > 0.7 ? 'warn' : 'ok'
  const recordingFpsState = recorderFps >= 28 ? 'ok' : recorderFps >= 20 ? 'warn' : 'error'
  const cameraTotal = cameras.length || 3
  const cameraOk = cameras.filter((camera) => camera.health === 'ok').length
  const cameraState = cameras.some((camera) => camera.health === 'error')
    ? 'error'
    : cameraOk === cameraTotal
      ? 'ok'
      : cameraOk > 0
        ? 'warn'
        : 'pending'
  const picoStateText =
    picoConnection.state === 'ok'
      ? '在线'
      : picoConnection.state === 'warn'
        ? '离线'
        : picoConnection.state === 'error'
          ? '错误'
          : picoConnection.state === 'checking'
            ? '检查中'
            : '待检查'

  return (
    <footer className="status-bar">
      <MetricPill state={!telemetryLive ? 'pending' : halOk ? 'ok' : 'error'} label={telemetryLive ? 'HAL' : 'HAL 未知'} />
      <MetricPill state={telemetryLive ? 'ok' : 'error'} label={`WS ${telemetryLive ? `${wsHz}Hz` : '不可用'}`} />
      <MetricPill state={cameraState} label={`CAM ${cameraOk}/${cameraTotal}`} />
      <MetricPill
        state={picoConnection.state}
        label={`PICO ${picoStateText} ${picoVision.ip}`}
        tip={`${picoVision.ip}:${picoVision.adbPort} · ${picoConnection.message}`}
      />
      <MetricPill state={dangerState} label={`Safety ${safetyLocked ? 'LOCK' : safetyAvailable ? dangerIndex.toFixed(2) : '不可用'}`} />
      {phase === 'recording' && (
        <UiTag tone="error" style={{ animation: 'blink 1s step-end infinite' }}>
          ● REC
        </UiTag>
      )}
      {phase === 'saving' && <UiTag tone="processing">保存中</UiTag>}
      {phase === 'resetting' && <UiTag tone="muted">复位中</UiTag>}
      {phase !== 'idle' && (
        <MetricPill state={recordingFpsState} label={`录制 ${recorderFps.toFixed(1)}Hz`} />
      )}
      {recorderLateFrames > 0 && <UiTag tone="warning">迟帧 {recorderLateFrames}</UiTag>}
      <UiText secondary>
        Episode #{episodeCount} · Frame {frameCount} · UI {uiFps.toFixed(1)} FPS · Backend M0
      </UiText>
    </footer>
  )
}
