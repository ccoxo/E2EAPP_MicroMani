/*
 * 阅读导航 01｜入口与界面
 * 职责：组织录制页面的全局与双腕相机区域，复用相机预览组件。
 * 先看：CameraSlotProps → CameraSlot → CameraPanel。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { useLiveCameraSnapshot } from '../../hooks/useLiveCameraSnapshot'
import { useTelemetryStore } from '../../stores/telemetry'
import type { AppConfig, CameraTelemetry } from '../../types'
import { UiCard, UiTag } from '../ui'

const cameraSpecs = {
  global: { label: '全局', title: '全局相机', model: 'IMX335' },
  wrist_left: { label: '左腕', title: '左腕相机', model: 'IMX335' },
  wrist_right: { label: '右腕', title: '右腕相机', model: 'IMX335' },
} as const
/** 计算对应的业务值或展示值。 */
const cameraResolution = (config: AppConfig, kind: keyof typeof cameraSpecs) => {
  if (kind === 'global') return config.cameras.globalResolution ?? config.cameras.previewResolution
  if (kind === 'wrist_left') return config.cameras.wristLeftResolution ?? config.cameras.previewResolution
  return config.cameras.wristRightResolution ?? config.cameras.previewResolution
}
/** 计算对应的业务值或展示值。 */
const getFpsColor = (hz: number) => {
  if (hz >= 25) return '#52c41a'
  if (hz >= 20) return '#E65100'
  return '#cf1322'
}

interface CameraSlotProps {
  kind: keyof typeof cameraSpecs
  camera: CameraTelemetry | undefined
  resolution: string
}
/** 渲染当前界面单元，并连接所需数据。 */
function CameraSlot({ kind, camera, resolution }: CameraSlotProps) {
  const spec = cameraSpecs[kind]
  const fps = camera?.fps ?? 0
  const { liveImageEnabled, snapshotUrl, handleLoad, handleError } = useLiveCameraSnapshot(kind, camera?.health)

  return (
    <div className={`record-camera-slot record-camera-${kind}`}>
      {liveImageEnabled && snapshotUrl ? (
        <img
          className="record-camera-image"
          data-testid={`record-camera-image-${kind}`}
          src={snapshotUrl}
          alt={`${spec.title} live frame`}
          onLoad={handleLoad}
          onError={handleError}
        />
      ) : (
        <div className={`record-camera-placeholder record-camera-placeholder-${kind}`}>
          <div className="record-camera-placeholder-copy">
            <div>{spec.model}</div>
            <small>{camera ? '等待画面' : '无信号'}</small>
          </div>
        </div>
      )}

      <div className="record-camera-label">{spec.label}</div>
      <div className="record-camera-fps" style={{ color: getFpsColor(fps) }}>
        {fps.toFixed(1)} Hz
      </div>
      <div className="record-camera-resolution">{resolution}</div>
    </div>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export default function CameraPanel() {
  const cameras = useTelemetryStore((s) => s.frame.cameras)
  const config = useTelemetryStore((s) => s.config)
  const globalCamera = cameras.find((camera) => camera.key === 'global')
  const wristLeftCamera = cameras.find((camera) => camera.key === 'wrist_left')
  const wristRightCamera = cameras.find((camera) => camera.key === 'wrist_right')
  const globalResolution = cameraResolution(config, 'global')
  const wristLeftResolution = cameraResolution(config, 'wrist_left')
  const wristRightResolution = cameraResolution(config, 'wrist_right')

  return (
    <UiCard title="相机预览" bodyStyle={{ padding: 6 }} style={{ flexShrink: 0 }}>
      <div className="record-camera-layout">
        <div className="record-camera-panel">
          <div className="record-camera-meta-row">
            <span>{cameraSpecs.global.title}</span>
            <UiTag>{globalResolution}</UiTag>
          </div>
          <CameraSlot kind="global" camera={globalCamera} resolution={globalResolution} />
        </div>
        <div className="record-camera-panel">
          <div className="record-camera-meta-row">
            <span>{cameraSpecs.wrist_left.title}</span>
            <UiTag>{wristLeftResolution}</UiTag>
          </div>
          <CameraSlot kind="wrist_left" camera={wristLeftCamera} resolution={wristLeftResolution} />
        </div>
        <div className="record-camera-panel">
          <div className="record-camera-meta-row">
            <span>{cameraSpecs.wrist_right.title}</span>
            <UiTag>{wristRightResolution}</UiTag>
          </div>
          <CameraSlot kind="wrist_right" camera={wristRightCamera} resolution={wristRightResolution} />
        </div>
      </div>
    </UiCard>
  )
}
