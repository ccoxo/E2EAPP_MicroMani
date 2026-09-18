/*
 * 阅读导航 01｜入口与界面
 * 职责：显示 MJPEG 相机流，处理加载、错误占位和手动刷新。
 * 先看：CameraPreviewProps → CameraPreview。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { useEffect } from 'react'
import { Camera, Clock3 } from 'lucide-react'
import { useLiveCameraSnapshot } from '../hooks/useLiveCameraSnapshot'
import type { CameraTelemetry } from '../types'
import { MetricPill } from './MetricPill'
import { UiTag, UiText } from './ui'

interface CameraPreviewProps {
  camera: CameraTelemetry
  compact?: boolean
  showGrid?: boolean
  showReticle?: boolean
  resolution?: string
  onClick?: () => void
  onPreviewHealthChange?: (health: CameraTelemetry['health']) => void
}

const cameraResolution: Record<CameraTelemetry['key'], string> = {
  global: '640x480',
  wrist_left: '640x480',
  wrist_right: '640x480',
}
/** 渲染当前界面单元，并连接所需数据。 */
export function CameraPreview({
  camera,
  compact,
  showGrid,
  showReticle,
  resolution,
  onClick,
  onPreviewHealthChange,
}: CameraPreviewProps) {
  const skewState = Math.abs(camera.timestampSkewMs) > 16 ? 'warn' : 'ok'
  const { liveImageEnabled, previewHealth, snapshotUrl, handleLoad, handleError } = useLiveCameraSnapshot(camera.key, camera.health)
  const gridVisible = showGrid ?? camera.key === 'global'
  const reticleVisible = showReticle ?? camera.key === 'global'
  const displayResolution = resolution ?? cameraResolution[camera.key]

  useEffect(() => {
    onPreviewHealthChange?.(previewHealth)
  }, [onPreviewHealthChange, previewHealth])

  return (
    <section
      className={`camera-preview camera-preview-${camera.key} ${compact ? 'camera-preview-compact' : ''} ${onClick ? 'camera-preview-clickable' : ''}`}
      onClick={onClick}
      onKeyDown={(event) => {
        if (!onClick || (event.key !== 'Enter' && event.key !== ' ')) return
        event.preventDefault()
        onClick()
      }}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      <div className={`camera-frame camera-frame-${camera.key} ${liveImageEnabled && snapshotUrl ? 'camera-frame-live' : ''}`}>
        {liveImageEnabled && snapshotUrl && (
          <img
            className="camera-image"
            data-testid={`camera-image-${camera.key}`}
            src={snapshotUrl}
            alt={`${camera.label} live frame`}
            onLoad={handleLoad}
            onError={handleError}
          />
        )}
        {(!liveImageEnabled || !snapshotUrl) && (
          <div className="camera-placeholder">
            <Camera size={24} />
            <span>No signal</span>
          </div>
        )}
        {gridVisible && <div className="camera-grid" />}
        {reticleVisible && <div className="camera-reticle" />}
        <div className="camera-label">
          <Camera size={16} />
          {camera.label}
        </div>
      </div>
      <div className="camera-meta">
        <div className="camera-meta-grid">
          <MetricPill state={previewHealth} label={`${camera.fps.toFixed(1)} FPS`} />
          <MetricPill state={skewState} label={`${camera.timestampSkewMs.toFixed(1)} ms`} tip="Clock skew" />
          <UiTag className="camera-resolution-tag">{displayResolution}</UiTag>
          <UiTag className="compact-tag camera-age-tag">
            <Clock3 size={13} />
            age {Math.max(0, camera.frameAgeMs).toFixed(0)} ms
          </UiTag>
        </div>
        {!compact && <UiText secondary>Preview frames keep the original aspect ratio.</UiText>}
      </div>
    </section>
  )
}
