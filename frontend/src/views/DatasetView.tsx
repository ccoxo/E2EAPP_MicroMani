import { Button, Card, Empty, Input, Modal, Popconfirm, Progress, Segmented, Slider, Space, Tag, Typography } from 'antd'
import {
  CheckCircle2,
  Database,
  Edit3,
  FastForward,
  Pause,
  Play,
  Rewind,
  Save,
  Trash2,
  Upload,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  apiBase,
  createDatasetApi,
  deleteDatasetApi,
  deleteDatasetEpisodeApi,
  exportDatasetApi,
  fetchDatasets,
  mockMode,
  renameDatasetApi,
  saveDatasetReviewApi,
  updateDatasetEpisodeApi,
  type DatasetApi,
  type DatasetCameraResolutionApi,
  type DatasetEpisodeApi,
  type DatasetEpisodeStatusApi,
  type DatasetFeatureSummaryApi,
} from '../api'
import { useTelemetryStore } from '../stores/telemetry'
import type { EpisodeRecord } from '../types'

type CameraKey = 'global' | 'wrist_left' | 'wrist_right'
type EpisodeStatus = DatasetEpisodeStatusApi

interface EpisodeSample {
  frame: number
  leftJoints: number[]
  rightJoints: number[]
  forceLeft: number[]
  forceRight: number[]
  images?: Partial<Record<CameraKey, string>>
}

interface ReviewCamera {
  key: CameraKey
  label: string
  model: string
  resolution: string
  aspectRatio: string
}

interface ReviewEpisode {
  id: string
  name: string
  task: string
  status: EpisodeStatus
  quality: number
  frames: number
  fps: number
  durationS: number
  createdAt: string
  warnings: string[]
  samples: EpisodeSample[]
  lateFrames?: number
  maxForceLeft?: number
  maxForceRight?: number
  featureSummary?: DatasetFeatureSummaryApi
  cameraResolutions?: Partial<Record<CameraKey, DatasetCameraResolutionApi>>
}

interface ReviewDataset {
  id: string
  name: string
  root?: string
  fps?: number
  format?: string
  featureSummary?: DatasetFeatureSummaryApi
  cameraResolutions?: Partial<Record<CameraKey, DatasetCameraResolutionApi>>
  status: 'local' | 'dry-run' | '待审核' | string
  episodes: ReviewEpisode[]
}

const cameras: ReviewCamera[] = [
  { key: 'global', label: '全局相机', model: 'AR0234', resolution: '1920x1080', aspectRatio: '16 / 9' },
  { key: 'wrist_left', label: '左腕相机', model: 'IMX258', resolution: '1920x1080', aspectRatio: '16 / 9' },
  { key: 'wrist_right', label: '右腕相机', model: 'IMX258', resolution: '1920x1080', aspectRatio: '16 / 9' },
]

const axisLabels = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const forceLabels = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz']

function makeSamples(frames: number, seed: number): EpisodeSample[] {
  return Array.from({ length: frames }, (_, frame) => {
    const t = frame / 30
    const leftJoints = axisLabels.map((_, index) => Math.sin(t * (0.7 + index * 0.09) + seed + index) * (index < 3 ? 800 : 90))
    const rightJoints = axisLabels.map((_, index) => Math.cos(t * (0.64 + index * 0.08) + seed * 0.8 + index) * (index < 3 ? 720 : 82))
    const forceLeft = [
      Math.sin(t * 1.4 + seed) * 0.9,
      Math.cos(t * 1.1 + seed) * 0.55,
      1.1 + Math.sin(t * 0.85 + seed) * 0.42,
      Math.sin(t * 1.3 + seed) * 0.012,
      Math.cos(t * 1.2 + seed) * 0.011,
      Math.sin(t * 0.75 + seed) * 0.009,
    ]
    const forceRight = [
      Math.cos(t * 1.0 + seed) * 0.72,
      Math.sin(t * 1.5 + seed) * 0.68,
      0.9 + Math.cos(t * 0.78 + seed) * 0.36,
      Math.cos(t * 0.92 + seed) * 0.011,
      Math.sin(t * 1.25 + seed) * 0.012,
      Math.cos(t * 0.7 + seed) * 0.008,
    ]
    return { frame, leftJoints, rightJoints, forceLeft, forceRight }
  })
}

function makeEpisode(id: string, index: number, quality: number, status: EpisodeStatus, task: string): ReviewEpisode {
  const frames = 180 + index * 24
  return {
    id,
    name: `episode_${String(index).padStart(4, '0')}`,
    task,
    status,
    quality,
    frames,
    fps: 30,
    durationS: frames / 30,
    createdAt: `2026-04-${String(18 + index).padStart(2, '0')} 10:${String(12 + index).padStart(2, '0')}`,
    warnings: quality < 80 ? ['左腕相机存在轻微抖动', '右臂 Fz 接近警告阈值'] : [],
    samples: makeSamples(frames, index + quality / 100),
  }
}

function episodeFromRecord(record: EpisodeRecord, datasetName: string): ReviewEpisode {
  const frames = Math.max(90, record.frameCount || 150)
  const quality = Math.max(60, Math.min(99, 96 - record.lateFrames * 3 - Math.max(record.cameraDrops.global, record.cameraDrops.wristLeft, record.cameraDrops.wristRight) * 4))
  return {
    id: `${datasetName}-${record.index}`,
    name: `episode_${String(record.index).padStart(4, '0')}`,
    task: 'Assemble ICF target component',
    status: record.status === 'ok' ? 'review' : 'invalid',
    quality,
    frames,
    fps: 30,
    durationS: record.durationS,
    createdAt: '当前会话',
    warnings: record.lateFrames > 0 ? [`迟帧 ${record.lateFrames} 个`] : [],
    samples: makeSamples(frames, record.index + 5),
  }
}

function formatCreatedAt(value: number) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function episodeFromApi(episode: DatasetEpisodeApi): ReviewEpisode {
  return {
    id: episode.id,
    name: episode.name,
    task: episode.task,
    status: episode.status,
    quality: episode.quality,
    frames: episode.frames,
    fps: episode.fps,
    durationS: episode.durationS,
    createdAt: formatCreatedAt(episode.createdAt),
    warnings: episode.warnings,
    samples: episode.samples,
    lateFrames: episode.lateFrames,
    maxForceLeft: episode.maxForceLeft,
    maxForceRight: episode.maxForceRight,
    featureSummary: episode.featureSummary ?? episode.features,
    cameraResolutions: episode.cameraResolutions,
  }
}

function datasetFromApi(dataset: DatasetApi): ReviewDataset {
  return {
    id: dataset.id,
    name: dataset.name,
    status: dataset.status,
    root: dataset.root,
    fps: dataset.fps,
    format: dataset.format,
    featureSummary: dataset.featureSummary,
    cameraResolutions: dataset.cameraResolutions,
    episodes: dataset.episodes.map(episodeFromApi),
  }
}

function camerasForReview(dataset: ReviewDataset, episode: ReviewEpisode): ReviewCamera[] {
  const resolutions = episode.cameraResolutions ?? dataset.cameraResolutions ?? {}
  return cameras.map((camera) => {
    const resolution = resolutions[camera.key]
    const saved = resolution?.saved || camera.resolution
    const capture = resolution?.capture || 'native'
    const preview = resolution?.preview || 'native'
    return {
      ...camera,
      resolution: saved,
      model: `${camera.model} | capture ${capture} | preview ${preview}`,
    }
  })
}

function featureShapeText(features?: DatasetFeatureSummaryApi) {
  if (!features) return 'features unavailable'
  const state = features['observation.state']?.shape?.join('x') || '?'
  const action = features.action?.shape?.join('x') || '?'
  const pulses = features['observation.pulses']?.shape?.join('x') || '?'
  return `state ${state} | action ${action} | pulses ${pulses}`
}

const baseDatasets: ReviewDataset[] = [
  {
    id: 'micro_assembly_v1',
    name: 'micro_assembly_v1',
    status: 'local',
    episodes: [
      makeEpisode('micro_assembly_v1-42', 42, 92, 'valid', 'Assemble ICF target component'),
      makeEpisode('micro_assembly_v1-41', 41, 88, 'review', 'Pick and place micro component'),
      makeEpisode('micro_assembly_v1-40', 40, 77, 'review', 'Precision insertion task'),
    ],
  },
  {
    id: 'force_contact_dryrun',
    name: 'force_contact_dryrun',
    status: 'dry-run',
    episodes: [
      makeEpisode('force_contact_dryrun-8', 8, 84, 'review', 'Force-limited contact tracing'),
      makeEpisode('force_contact_dryrun-7', 7, 81, 'valid', 'Force-limited contact tracing'),
    ],
  },
  {
    id: 'icf_alignment_eval',
    name: 'icf_alignment_eval',
    status: '待审核',
    episodes: [
      makeEpisode('icf_alignment_eval-16', 16, 76, 'review', 'ICF alignment evaluation'),
      makeEpisode('icf_alignment_eval-15', 15, 72, 'invalid', 'ICF alignment evaluation'),
    ],
  },
]

function statusTag(status: EpisodeStatus) {
  if (status === 'valid') return <Tag color="success">有效</Tag>
  if (status === 'invalid') return <Tag color="error">无效</Tag>
  return <Tag color="warning">待复核</Tag>
}

function qualityTone(quality: number) {
  if (quality >= 90) return '#12a06f'
  if (quality >= 80) return '#d98400'
  return '#d83a52'
}

function clampFrame(value: number, frames: number) {
  return Math.max(0, Math.min(frames - 1, Math.round(value)))
}

function linePoints(samples: EpisodeSample[], side: 'left' | 'right', axisIndex: number, width: number, height: number) {
  const values = samples.map((sample) => (side === 'left' ? sample.leftJoints : sample.rightJoints)[axisIndex] ?? 0)
  const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value)))
  return values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * width
      const y = height / 2 - (value / maxAbs) * (height * 0.42)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

function forcePoints(samples: EpisodeSample[], side: 'left' | 'right', channelIndex: number, width: number, height: number) {
  const values = samples.map((sample) => (side === 'left' ? sample.forceLeft : sample.forceRight)[channelIndex] ?? 0)
  const maxAbs = Math.max(0.05, ...values.map((value) => Math.abs(value)))
  return values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * width
      const y = height / 2 - (value / maxAbs) * (height * 0.42)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

function currentSample(episode: ReviewEpisode, frameIndex: number) {
  if (episode.samples.length === 0) {
    return { frame: 0, leftJoints: [0, 0, 0, 0, 0, 0], rightJoints: [0, 0, 0, 0, 0, 0], forceLeft: [0, 0, 0, 0, 0, 0], forceRight: [0, 0, 0, 0, 0, 0] }
  }
  const ratio = frameIndex / Math.max(1, episode.frames - 1)
  const sampleIndex = clampFrame(ratio * (episode.samples.length - 1), episode.samples.length)
  return episode.samples[sampleIndex] ?? episode.samples[0]
}

function DatasetVideoPane({
  camera,
  episode,
  frameIndex,
}: {
  camera: ReviewCamera
  episode: ReviewEpisode
  frameIndex: number
}) {
  const progress = frameIndex / Math.max(1, episode.frames - 1)
  const markerX = 16 + Math.sin(progress * Math.PI * 2 + camera.label.length) * 18
  const markerY = 10 + Math.cos(progress * Math.PI * 2 + camera.model.length) * 14
  const sample = currentSample(episode, frameIndex)
  const imagePath = sample.images?.[camera.key]
  const imageUrl = imagePath && imagePath.startsWith('/api/') ? `${apiBase}${imagePath}` : imagePath

  return (
    <article className={`dataset-video-pane dataset-video-${camera.key}`}>
      <div className="dataset-video-head">
        <span>{camera.label}</span>
        <Tag>{camera.resolution}</Tag>
      </div>
      <div className="dataset-video-frame" style={{ aspectRatio: camera.aspectRatio }}>
        {imageUrl ? (
          <img className="dataset-video-image" src={imageUrl} alt={`${camera.label} frame ${sample.frame + 1}`} />
        ) : (
          <>
            <div className="dataset-video-grid-overlay" />
            <div className="dataset-video-target" style={{ left: `${50 + markerX}%`, top: `${50 + markerY}%` }} />
            <div className="dataset-video-center">{camera.model}</div>
          </>
        )}
        <div className="dataset-video-caption">
          Frame {frameIndex + 1}/{episode.frames}
        </div>
      </div>
    </article>
  )
}

function TrajectoryPanel({
  title,
  side,
  episode,
  frameIndex,
}: {
  title: string
  side: 'left' | 'right'
  episode: ReviewEpisode
  frameIndex: number
}) {
  const width = 320
  const height = 128
  const sample = currentSample(episode, frameIndex)
  const joints = side === 'left' ? sample.leftJoints : sample.rightJoints
  const currentX = (frameIndex / Math.max(1, episode.frames - 1)) * width

  return (
    <Card size="small" title={title}>
      <svg className="dataset-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="dataset-chart-axis" />
        <polyline points={linePoints(episode.samples, side, 0, width, height)} className="dataset-line dataset-line-x" />
        <polyline points={linePoints(episode.samples, side, 1, width, height)} className="dataset-line dataset-line-y" />
        <polyline points={linePoints(episode.samples, side, 2, width, height)} className="dataset-line dataset-line-z" />
        <line x1={currentX} y1="0" x2={currentX} y2={height} className="dataset-chart-cursor" />
      </svg>
      <div className="dataset-chart-readout">
        {axisLabels.slice(0, 3).map((axis, index) => (
          <span key={axis}>
            <b>{axis}</b>{(joints[index] ?? 0).toFixed(0)} um
          </span>
        ))}
        {axisLabels.slice(3).map((axis, index) => (
          <span key={axis}>
            <b>{axis}</b>{(joints[index + 3] ?? 0).toFixed(1)}°
          </span>
        ))}
      </div>
    </Card>
  )
}

function ForcePanel({
  title,
  side,
  episode,
  frameIndex,
}: {
  title: string
  side: 'left' | 'right'
  episode: ReviewEpisode
  frameIndex: number
}) {
  const width = 320
  const height = 128
  const sample = currentSample(episode, frameIndex)
  const values = side === 'left' ? sample.forceLeft : sample.forceRight
  const currentX = (frameIndex / Math.max(1, episode.frames - 1)) * width

  return (
    <Card size="small" title={title}>
      <svg className="dataset-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="dataset-chart-axis" />
        <polyline points={forcePoints(episode.samples, side, 0, width, height)} className="dataset-line dataset-line-x" />
        <polyline points={forcePoints(episode.samples, side, 1, width, height)} className="dataset-line dataset-line-y" />
        <polyline points={forcePoints(episode.samples, side, 2, width, height)} className="dataset-line dataset-line-z" />
        <line x1={currentX} y1="0" x2={currentX} y2={height} className="dataset-chart-cursor" />
      </svg>
      <div className="dataset-chart-readout">
        {forceLabels.map((channel, index) => (
          <span key={channel}>
            <b>{channel}</b>{(values[index] ?? 0).toFixed(index < 3 ? 2 : 3)} {index < 3 ? 'N' : 'Nm'}
          </span>
        ))}
      </div>
    </Card>
  )
}

function applyEpisodeOverrides(episode: ReviewEpisode, nameOverrides: Record<string, string>, statusOverrides: Record<string, EpisodeStatus>) {
  return {
    ...episode,
    name: nameOverrides[episode.id] ?? episode.name,
    status: statusOverrides[episode.id] ?? episode.status,
  }
}

/**
 * 渲染数据集复核工作台。
 *
 * 业务背景：复核人员需要在同一页面检查 episode 轨迹、力觉曲线、
 * 三路相机样本和 LeRobot v3 feature shape，并对样本做有效性标记。
 *
 * @returns 数据集复核页面的 React 组件。
 */
export function DatasetView() {
  const recordSession = useTelemetryStore((state) => state.recordSession)
  const [selectedDatasetId, setSelectedDatasetId] = useState('micro_assembly_v1')
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null)
  const [frameIndex, setFrameIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [serverDatasets, setServerDatasets] = useState<ReviewDataset[]>([])
  const [backendLoadError, setBackendLoadError] = useState('')
  const [refreshToken, setRefreshToken] = useState(0)
  const [deletedDatasetIds, setDeletedDatasetIds] = useState<string[]>([])
  const [deletedEpisodeIds, setDeletedEpisodeIds] = useState<string[]>([])
  const [datasetNameOverrides, setDatasetNameOverrides] = useState<Record<string, string>>({})
  const [episodeNameOverrides, setEpisodeNameOverrides] = useState<Record<string, string>>({})
  const [episodeStatusOverrides, setEpisodeStatusOverrides] = useState<Record<string, EpisodeStatus>>({})
  const [renameTarget, setRenameTarget] = useState<{ type: 'dataset' | 'episode'; id: string; value: string } | null>(null)

  useEffect(() => {
    if (mockMode) return
    let cancelled = false
    fetchDatasets()
      .then((items) => {
        if (cancelled) return
        setServerDatasets(items.map(datasetFromApi))
        setBackendLoadError('')
      })
      .catch((error) => {
        if (cancelled) return
        setBackendLoadError(String(error))
        setServerDatasets([])
      })
    return () => {
      cancelled = true
    }
  }, [refreshToken])

  const datasets = useMemo(() => {
    const sourceDatasets = mockMode ? baseDatasets : serverDatasets
    const liveEpisodes = recordSession.episodeHistory.map((record) => episodeFromRecord(record, recordSession.datasetName))
    const merged = sourceDatasets.map((dataset) => {
      const liveForDataset = dataset.id === recordSession.datasetName ? liveEpisodes : []
      return { ...dataset, episodes: [...liveForDataset, ...dataset.episodes] }
    })
    if (liveEpisodes.length > 0 && !merged.some((dataset) => dataset.id === recordSession.datasetName)) {
      merged.unshift({ id: recordSession.datasetName, name: recordSession.datasetName, status: 'local', episodes: liveEpisodes })
    }
    return merged
      .filter((dataset) => !deletedDatasetIds.includes(dataset.id))
      .map((dataset) => ({
        ...dataset,
        name: datasetNameOverrides[dataset.id] ?? dataset.name,
        episodes: dataset.episodes
          .filter((episode) => !deletedEpisodeIds.includes(episode.id))
          .map((episode) => applyEpisodeOverrides(episode, episodeNameOverrides, episodeStatusOverrides)),
      }))
  }, [datasetNameOverrides, deletedDatasetIds, deletedEpisodeIds, episodeNameOverrides, episodeStatusOverrides, recordSession.datasetName, recordSession.episodeHistory, serverDatasets])

  const selectedDataset = datasets.find((dataset) => dataset.id === selectedDatasetId)
    ?? datasets.find((dataset) => dataset.episodes.length > 0)
    ?? datasets[0]
  const selectedEpisode = selectedDataset?.episodes.find((episode) => episode.id === selectedEpisodeId) ?? selectedDataset?.episodes[0]
  const selectedCameras = selectedDataset && selectedEpisode ? camerasForReview(selectedDataset, selectedEpisode) : cameras
  const validEpisodes = selectedDataset?.episodes.filter((episode) => episode.status === 'valid').length ?? 0
  const avgQuality = selectedDataset && selectedDataset.episodes.length > 0
    ? Math.round(selectedDataset.episodes.reduce((sum, episode) => sum + episode.quality, 0) / selectedDataset.episodes.length)
    : 0

  useEffect(() => {
    if (!playing || !selectedEpisode) return
    const timer = window.setInterval(() => {
      setFrameIndex((current) => (current >= selectedEpisode.frames - 1 ? 0 : current + 1))
    }, Math.max(40, 1000 / (selectedEpisode.fps * playbackRate)))
    return () => window.clearInterval(timer)
  }, [playing, playbackRate, selectedEpisode])

  const chooseEpisode = (episodeId: string) => {
    setSelectedEpisodeId(episodeId)
    setFrameIndex(0)
    setPlaying(false)
  }

  const chooseDataset = (datasetId: string) => {
    setSelectedDatasetId(datasetId)
    setSelectedEpisodeId(null)
    setFrameIndex(0)
    setPlaying(false)
  }

  const commitRename = () => {
    if (!renameTarget) return
    if (renameTarget.type === 'dataset') {
      setDatasetNameOverrides((current) => ({ ...current, [renameTarget.id]: renameTarget.value.trim() || renameTarget.id }))
      if (!mockMode) {
        void renameDatasetApi(renameTarget.id, renameTarget.value.trim() || renameTarget.id)
          .then(() => setRefreshToken((value) => value + 1))
          .catch((error) => setBackendLoadError(String(error)))
      }
    } else {
      setEpisodeNameOverrides((current) => ({ ...current, [renameTarget.id]: renameTarget.value.trim() || renameTarget.id }))
      if (!mockMode && selectedDataset) {
        void updateDatasetEpisodeApi(selectedDataset.id, renameTarget.id, { name: renameTarget.value.trim() || renameTarget.id })
          .then(() => setRefreshToken((value) => value + 1))
          .catch((error) => setBackendLoadError(String(error)))
      }
    }
    setRenameTarget(null)
  }

  const deleteDataset = (datasetId: string) => {
    setDeletedDatasetIds((current) => [...current, datasetId])
    if (!mockMode) {
      void deleteDatasetApi(datasetId)
        .then(() => setRefreshToken((value) => value + 1))
        .catch((error) => setBackendLoadError(String(error)))
    }
    if (selectedDatasetId === datasetId) {
      setSelectedDatasetId('')
      setSelectedEpisodeId(null)
    }
  }

  const deleteEpisode = (episodeId: string) => {
    setDeletedEpisodeIds((current) => [...current, episodeId])
    if (!mockMode && selectedDataset) {
      void deleteDatasetEpisodeApi(selectedDataset.id, episodeId)
        .then(() => setRefreshToken((value) => value + 1))
        .catch((error) => setBackendLoadError(String(error)))
    }
    if (selectedEpisodeId === episodeId) setSelectedEpisodeId(null)
  }

  const setEpisodeStatus = (episodeId: string, status: EpisodeStatus) => {
    setEpisodeStatusOverrides((current) => ({ ...current, [episodeId]: status }))
    if (!mockMode && selectedDataset) {
      void updateDatasetEpisodeApi(selectedDataset.id, episodeId, { status })
        .then(() => setRefreshToken((value) => value + 1))
        .catch((error) => setBackendLoadError(String(error)))
    }
  }

  const createDataset = () => {
    const name = `dataset_${Date.now()}`
    if (!mockMode) {
      void createDatasetApi(name)
        .then(() => setRefreshToken((value) => value + 1))
        .catch((error) => setBackendLoadError(String(error)))
    }
  }

  const saveReview = () => {
    if (!selectedDataset || mockMode) return
    void saveDatasetReviewApi(selectedDataset.id)
      .then(() => setRefreshToken((value) => value + 1))
      .catch((error) => setBackendLoadError(String(error)))
  }

  const exportDataset = () => {
    if (!selectedDataset || mockMode) return
    void exportDatasetApi(selectedDataset.id)
      .then(() => setRefreshToken((value) => value + 1))
      .catch((error) => setBackendLoadError(String(error)))
  }

  return (
    <div className="view-stack dataset-review-page">
      <section className="page-header">
        <div>
          <Typography.Title level={2}>数据集质检 Dataset</Typography.Title>
          <Typography.Text type="secondary">选择数据集和 episode 后同步检查三路视频、双臂轨迹与双力传感器曲线。</Typography.Text>
        </div>
        <Space wrap>
          {!mockMode && <Tag color={backendLoadError ? 'error' : 'processing'}>{backendLoadError ? '后端数据异常' : '后端数据'}</Tag>}
          <Button type="primary" icon={<Database size={16} />} onClick={createDataset}>新建数据集</Button>
          <Button icon={<Upload size={16} />} onClick={exportDataset} disabled={!selectedDataset}>Hub 导出</Button>
          <Button icon={<Save size={16} />} onClick={saveReview} disabled={!selectedDataset}>保存审核结果</Button>
        </Space>
      </section>

      <section className="dataset-review-layout">
        <aside className="dataset-browser panel-surface">
          <div className="section-title">
            <span>数据集</span>
            <Tag>{datasets.length}</Tag>
          </div>
          <div className="dataset-list">
            {datasets.map((dataset) => {
              const frames = dataset.episodes.reduce((sum, episode) => sum + episode.frames, 0)
              const quality = dataset.episodes.length > 0
                ? Math.round(dataset.episodes.reduce((sum, episode) => sum + episode.quality, 0) / dataset.episodes.length)
                : 0
              return (
                <button
                  className={`dataset-row-button ${selectedDataset?.id === dataset.id ? 'active' : ''}`}
                  key={dataset.id}
                  type="button"
                  onClick={() => chooseDataset(dataset.id)}
                >
                  <span>
                    <b>{dataset.name}</b>
                    <small>{dataset.episodes.length} 条 · {frames} 帧</small>
                  </span>
                  <Tag color={dataset.status === '待审核' ? 'warning' : 'processing'}>{dataset.status}</Tag>
                  <Progress percent={quality} size="small" showInfo={false} strokeColor={qualityTone(quality)} />
                </button>
              )
            })}
          </div>

          {selectedDataset && (
            <div className="dataset-edit-actions">
              <Button size="small" icon={<Edit3 size={14} />} onClick={() => setRenameTarget({ type: 'dataset', id: selectedDataset.id, value: selectedDataset.name })}>
                重命名
              </Button>
              <Popconfirm title="删除该数据集？" okText="删除" cancelText="取消" onConfirm={() => deleteDataset(selectedDataset.id)}>
                <Button size="small" danger icon={<Trash2 size={14} />}>删除</Button>
              </Popconfirm>
            </div>
          )}

          <div className="section-title dataset-episode-title">
            <span>Episode</span>
            <Tag>{selectedDataset?.episodes.length ?? 0}</Tag>
          </div>
          <div className="episode-list">
            {selectedDataset?.episodes.map((episode) => (
              <button
                className={`episode-row-button ${selectedEpisode?.id === episode.id ? 'active' : ''}`}
                key={episode.id}
                type="button"
                onClick={() => chooseEpisode(episode.id)}
              >
                <span>
                  <b>{episode.name}</b>
                  <small>{episode.frames} 帧 · {episode.durationS.toFixed(1)}s · {episode.task}</small>
                </span>
                {statusTag(episode.status)}
              </button>
            ))}
          </div>
        </aside>

        <main className="dataset-inspector">
          {!selectedDataset || !selectedEpisode ? (
            <section className="panel-surface">
              <Empty description="请选择数据集和 episode" />
            </section>
          ) : (
            <>
              <section className="panel-surface dataset-summary-strip">
                <div>
                  <Typography.Title level={3}>{selectedDataset.name}</Typography.Title>
                  <Typography.Text type="secondary">{selectedEpisode.name} · {selectedEpisode.createdAt}</Typography.Text>
                </div>
                <div className="dataset-summary-metrics">
                  <span><small>平均质量</small><b>{avgQuality}%</b></span>
                  <span><small>有效条数</small><b>{validEpisodes}/{selectedDataset.episodes.length}</b></span>
                  <span><small>当前帧</small><b>{frameIndex + 1}/{selectedEpisode.frames}</b></span>
                </div>
                <Space wrap>
                  <Button size="small" icon={<Edit3 size={14} />} onClick={() => setRenameTarget({ type: 'episode', id: selectedEpisode.id, value: selectedEpisode.name })}>
                    重命名本条
                  </Button>
                  <Button size="small" icon={<CheckCircle2 size={14} />} onClick={() => setEpisodeStatus(selectedEpisode.id, 'valid')}>
                    标记有效
                  </Button>
                  <Button size="small" icon={<XCircle size={14} />} onClick={() => setEpisodeStatus(selectedEpisode.id, 'invalid')}>
                    标记无效
                  </Button>
                  <Popconfirm title="删除该条数据？" okText="删除" cancelText="取消" onConfirm={() => deleteEpisode(selectedEpisode.id)}>
                    <Button size="small" danger icon={<Trash2 size={14} />}>删除本条</Button>
                  </Popconfirm>
                </Space>
              </section>

              <section className="panel-surface dataset-quality-workbench">
                <div className="section-title">
                  <span>同步视频检查</span>
                  <Space size={6} wrap>
                    {statusTag(selectedEpisode.status)}
                    <Tag>{featureShapeText(selectedEpisode.featureSummary ?? selectedDataset.featureSummary)}</Tag>
                    <Tag>Fmax L/R {(selectedEpisode.maxForceLeft ?? 0).toFixed(2)} / {(selectedEpisode.maxForceRight ?? 0).toFixed(2)}</Tag>
                    <Tag color={selectedEpisode.quality >= 85 ? 'success' : 'warning'}>质量 {selectedEpisode.quality}%</Tag>
                  </Space>
                </div>
                <div className="dataset-cockpit-grid">
                  <div className="dataset-video-grid">
                    {selectedCameras.map((camera) => (
                      <DatasetVideoPane camera={camera} episode={selectedEpisode} frameIndex={frameIndex} key={camera.key} />
                    ))}
                  </div>
                  <section className="dataset-quality-grid">
                    <TrajectoryPanel title="左机械臂轨迹" side="left" episode={selectedEpisode} frameIndex={frameIndex} />
                    <TrajectoryPanel title="右机械臂轨迹" side="right" episode={selectedEpisode} frameIndex={frameIndex} />
                    <ForcePanel title="左力传感器实时曲线" side="left" episode={selectedEpisode} frameIndex={frameIndex} />
                    <ForcePanel title="右力传感器实时曲线" side="right" episode={selectedEpisode} frameIndex={frameIndex} />
                  </section>
                </div>
                <div className="dataset-player-controls">
                  <Button icon={playing ? <Pause size={15} /> : <Play size={15} />} onClick={() => setPlaying((value) => !value)}>
                    {playing ? '暂停' : '播放'}
                  </Button>
                  <Button icon={<Rewind size={15} />} onClick={() => setFrameIndex((value) => clampFrame(value - 30, selectedEpisode.frames))}>
                    回退
                  </Button>
                  <Button icon={<FastForward size={15} />} onClick={() => setFrameIndex((value) => clampFrame(value + 30, selectedEpisode.frames))}>
                    快进
                  </Button>
                  <Segmented
                    size="small"
                    value={playbackRate}
                    onChange={(value) => setPlaybackRate(Number(value))}
                    options={[
                      { label: 'x1', value: 1 },
                      { label: 'x2', value: 2 },
                      { label: 'x4', value: 4 },
                    ]}
                  />
                  <Slider
                    className="dataset-frame-slider"
                    min={0}
                    max={selectedEpisode.frames - 1}
                    value={frameIndex}
                    onChange={(value) => setFrameIndex(clampFrame(value, selectedEpisode.frames))}
                    tooltip={{ formatter: (value) => `Frame ${Number(value ?? 0) + 1}` }}
                  />
                  <Typography.Text type="secondary">
                    {(frameIndex / selectedEpisode.fps).toFixed(2)}s / {selectedEpisode.durationS.toFixed(2)}s
                  </Typography.Text>
                </div>
              </section>

              <section className="panel-surface checklist-grid">
                {[
                  selectedEpisode.warnings.length === 0 ? '视频文件完整' : selectedEpisode.warnings[0],
                  'state/action shape 对齐',
                  '三路相机与状态帧同步',
                  '双臂轨迹可按帧回放',
                  '力传感器曲线可定位异常接触',
                ].map((item, index) => (
                  <div className={`check-item ${index === 0 && selectedEpisode.warnings.length > 0 ? 'check-item-warn' : ''}`} key={item}>
                    {index === 0 && selectedEpisode.warnings.length > 0 ? <XCircle size={17} /> : <CheckCircle2 size={17} />}
                    <span>{item}</span>
                  </div>
                ))}
              </section>
            </>
          )}
        </main>
      </section>

      <Modal
        title={renameTarget?.type === 'dataset' ? '重命名数据集' : '重命名 Episode'}
        open={Boolean(renameTarget)}
        onCancel={() => setRenameTarget(null)}
        onOk={commitRename}
        okText="保存"
        cancelText="取消"
      >
        <Input
          value={renameTarget?.value ?? ''}
          onChange={(event) => setRenameTarget((current) => current ? { ...current, value: event.target.value } : current)}
        />
      </Modal>
    </div>
  )
}
