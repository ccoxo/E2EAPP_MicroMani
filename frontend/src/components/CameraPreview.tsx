import { useEffect } from 'react'
import { Tag, Typography } from 'antd'
import { Camera, Clock3 } from 'lucide-react'
import { useLiveCameraSnapshot } from '../hooks/useLiveCameraSnapshot'
import type { CameraTelemetry } from '../types'
import { MetricPill } from './MetricPill'

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
          <Tag className="camera-resolution-tag">{displayResolution}</Tag>
          <Tag className="compact-tag camera-age-tag" icon={<Clock3 size={13} />}>
            age {Math.max(0, camera.frameAgeMs).toFixed(0)} ms
          </Tag>
        </div>
        {!compact && <Typography.Text type="secondary">Preview frames keep the original aspect ratio.</Typography.Text>}
      </div>
    </section>
  )
}
