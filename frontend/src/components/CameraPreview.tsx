import { Space, Tag, Typography } from 'antd'
import { Camera, Clock3 } from 'lucide-react'
import { useLiveCameraSnapshot } from '../hooks/useLiveCameraSnapshot'
import type { CameraTelemetry } from '../types'
import { MetricPill } from './MetricPill'

interface CameraPreviewProps {
  camera: CameraTelemetry
  compact?: boolean
  showGrid?: boolean
  showReticle?: boolean
  onClick?: () => void
}

const cameraResolution: Record<CameraTelemetry['key'], string> = {
  global: '640x480',
  wrist_left: '640x480',
  wrist_right: '640x480',
}

export function CameraPreview({ camera, compact, showGrid, showReticle, onClick }: CameraPreviewProps) {
  const skewState = Math.abs(camera.timestampSkewMs) > 16 ? 'warn' : 'ok'
  const { liveImageEnabled, snapshotUrl, handleLoad, handleError } = useLiveCameraSnapshot(camera.key, camera.health)
  const gridVisible = showGrid ?? camera.key === 'global'
  const reticleVisible = showReticle ?? camera.key === 'global'

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
      <div className={`camera-frame camera-frame-${camera.key} ${liveImageEnabled ? 'camera-frame-live' : ''}`}>
        {liveImageEnabled && (
          <img
            className="camera-image"
            data-testid={`camera-image-${camera.key}`}
            src={snapshotUrl}
            alt={`${camera.label} live frame`}
            onLoad={handleLoad}
            onError={handleError}
          />
        )}
        {!liveImageEnabled && (
          <div className="camera-placeholder">
            <Camera size={24} />
            <span>无信号</span>
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
        <Space size={6} wrap>
          <MetricPill state={camera.health} label={`${camera.fps.toFixed(1)} FPS`} />
          <MetricPill state={skewState} label={`${camera.timestampSkewMs.toFixed(1)} ms`} tip="相对主时钟偏差" />
          <Tag>{cameraResolution[camera.key]}</Tag>
          <Tag className="compact-tag" icon={<Clock3 size={13} />}>
            age {Math.max(0, camera.frameAgeMs).toFixed(0)} ms
          </Tag>
        </Space>
        {!compact && <Typography.Text type="secondary">完整画面按原始比例显示，原始帧由后端写入 LeRobotDataset。</Typography.Text>}
      </div>
    </section>
  )
}
