/*
 * 阅读导航 01｜入口与界面
 * 职责：管理数据集、episode 审阅和图像回放；列表与详情分别请求，避免一次加载全部样本。
 * 先看：CameraKey → EpisodeStatus → EpisodeSample → ReviewCamera。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { UiButton, UiCard, UiProgress, UiSegmented, UiSpace, UiTag, UiText, UiTitle } from '../components/ui'
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
import { useEffect, useMemo, useRef, useState } from 'react'
import { CameraPreview } from '../components/CameraPreview'
import { DatasetReplayPanel } from '../components/DatasetReplayPanel'
import {
  apiBase,
  createDatasetApi,
  deleteDatasetApi,
  deleteDatasetEpisodeApi,
  fetchDatasetEpisodeApi,
  fetchDatasets,
  mockMode,
  pushDatasetApi,
  renameDatasetApi,
  saveDatasetReviewApi,
  updateDatasetHubApi,
  updateDatasetEpisodeApi,
  type DatasetApi,
  type DatasetCameraResolutionApi,
  type DatasetEpisodeApi,
  type DatasetEpisodeStatusApi,
  type DatasetFeatureSummaryApi,
} from '../api'
import { camerasEqual, useFrameField } from '../stores/frameSelectors'
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
  { key: 'global', label: '全局相机', model: 'IMX335', resolution: '1920x1080', aspectRatio: '16 / 9' },
  { key: 'wrist_left', label: '左腕相机', model: 'IMX335', resolution: '1920x1080', aspectRatio: '16 / 9' },
  { key: 'wrist_right', label: '右腕相机', model: 'IMX335', resolution: '1920x1080', aspectRatio: '16 / 9' },
]

const axisLabels = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const forceLabels = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz']

/** 构建当前流程需要的数据结构。 */
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

/** 构建当前流程需要的数据结构。 */
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

/** 将后端或录制数据转换为复核页可用的数据模型。 */
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

/** 格式化对应数值用于界面展示。 */
function formatCreatedAt(value: number) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

/** 将后端或录制数据转换为复核页可用的数据模型。 */
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

/** 将后端或录制数据转换为复核页可用的数据模型。 */
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

/** 选择当前复核流程需要的数据。 */
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

/** 格式化对应数值用于界面展示。 */
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

/** 格式化对应数值用于界面展示。 */
function statusTag(status: EpisodeStatus) {
  if (status === 'valid') return <UiTag tone="success">有效</UiTag>
  if (status === 'invalid') return <UiTag tone="error">无效</UiTag>
  return <UiTag tone="warning">待复核</UiTag>
}

/** 计算对应的业务值或展示值。 */
function clampFrame(value: number, frames: number) {
  return Math.max(0, Math.min(frames - 1, Math.round(value)))
}

/** 构建当前流程需要的数据结构。 */
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

/** 构建当前流程需要的数据结构。 */
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

/** 选择当前复核流程需要的数据。 */
function currentSample(episode: ReviewEpisode, frameIndex: number) {
  if (episode.samples.length === 0) {
    return { frame: 0, leftJoints: [0, 0, 0, 0, 0, 0], rightJoints: [0, 0, 0, 0, 0, 0], forceLeft: [0, 0, 0, 0, 0, 0], forceRight: [0, 0, 0, 0, 0, 0] }
  }
  let selected = episode.samples[0]
  for (const sample of episode.samples) {
    if (sample.frame > frameIndex) break
    selected = sample
  }
  return selected
}

/** 渲染当前界面单元，并连接所需数据。 */
function DatasetVideoPane({
  camera,
  episode,
  frameIndex,
  imageUrls,
  ready,
  onImageState,
}: {
  imageUrls: string[]
  ready: boolean
  onImageState: (url: string, state: 'loaded' | 'error') => void
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
        <UiTag>{camera.resolution}</UiTag>
      </div>
      <div className="dataset-video-frame" style={{ aspectRatio: camera.aspectRatio }}>
        {imageUrls.map((url) => (
          <img key={url} className="dataset-video-image" src={url}
            style={{ visibility: ready && url === imageUrl ? 'visible' : 'hidden' }}
            aria-hidden={!ready || url !== imageUrl}
            onLoad={async (event) => {
              const image = event.currentTarget
              try {
                if (image.decode) await image.decode()
                if (image.isConnected) onImageState(url, 'loaded')
              } catch {
                if (image.isConnected) onImageState(url, 'error')
              }
            }}
            onError={() => onImageState(url, 'error')}
            alt={url === imageUrl ? `${camera.label} frame ${sample.frame + 1}` : ''} />
        ))}
        {!imageUrl && <>
          <div className="dataset-video-grid-overlay" />
          <div className="dataset-video-target" style={{ left: `${50 + markerX}%`, top: `${50 + markerY}%` }} />
          <div className="dataset-video-center">{camera.model}</div>
        </>}
        {imageUrl && !ready && <div className="dataset-video-center" role="status">正在加载画面…</div>}
        <div className="dataset-video-caption">
          Frame {frameIndex + 1}/{episode.frames}
        </div>
      </div>
    </article>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
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
    <UiCard title={title}>
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
    </UiCard>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
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
    <UiCard title={title}>
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
    </UiCard>
  )
}

/** 构建当前流程需要的数据结构。 */
function episodeKey(datasetId: string, episodeId: string) {
  return JSON.stringify([datasetId, episodeId])
}

function applyEpisodeOverrides(datasetId: string, episode: ReviewEpisode, nameOverrides: Record<string, string>, statusOverrides: Record<string, EpisodeStatus>) {
  const key = episodeKey(datasetId, episode.id)
  return {
    ...episode,
    name: nameOverrides[key] ?? episode.name,
    status: statusOverrides[key] ?? episode.status,
  }
}

/**
 * 渲染数据集复核工作台。
 *
 * 业务背景：复核人员需要在同一页面检查片段轨迹、力觉曲线、
 * 三路相机样本和数据特征形状，并对样本做有效性标记。
 *
 * 返回数据集复核页面组件。
 */
export function DatasetView() {
  const recordSession = useTelemetryStore((state) => state.recordSession)
  const config = useTelemetryStore((state) => state.config)
  // 复核页只读相机健康，不订整帧，避免 15Hz 关节刷新拖垮长页面。
  const liveCameras = useFrameField((frame) => frame.cameras, camerasEqual)
  const [selectedDatasetId, setSelectedDatasetId] = useState('micro_assembly_v1')
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(null)
  const [frameIndex, setFrameIndex] = useState(0)
  const [requestedFrame, setRequestedFrame] = useState(0)
  const [imageStates, setImageStates] = useState<Record<string, 'loaded' | 'error'>>({})
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
  const [renameTarget, setRenameTarget] = useState<{ type: 'dataset' | 'episode'; id: string; datasetId?: string; value: string } | null>(null)
  const [mutationPending, setMutationPending] = useState(false)
  const mutationPendingRef = useRef(false)
  const mountedRef = useRef(false)
  const [hubUploadOpen, setHubUploadOpen] = useState(false)
  const [hubPushToHub, setHubPushToHub] = useState(Boolean(config.storage.pushToHub))
  const [hubRepoId, setHubRepoId] = useState('')
  const [hubLocalPath, setHubLocalPath] = useState('')
  const [hubToken, setHubToken] = useState('')
  const [hubPrivate, setHubPrivate] = useState(false)
  const [hubDryRun, setHubDryRun] = useState(true)
  const [hubUploading, setHubUploading] = useState(false)
  const [hubMessage, setHubMessage] = useState('')
  const episodeDetailRequestedKeysRef = useRef(new Set<string>())

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

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
          .filter((episode) => !deletedEpisodeIds.includes(episodeKey(dataset.id, episode.id)))
          .map((episode) => applyEpisodeOverrides(dataset.id, episode, episodeNameOverrides, episodeStatusOverrides)),
      }))
  }, [datasetNameOverrides, deletedDatasetIds, deletedEpisodeIds, episodeNameOverrides, episodeStatusOverrides, recordSession.datasetName, recordSession.episodeHistory, serverDatasets])

  const selectedDataset = datasets.find((dataset) => dataset.id === selectedDatasetId)
    ?? datasets.find((dataset) => dataset.episodes.length > 0)
    ?? datasets[0]
  const selectedEpisode = selectedDataset?.episodes.find((episode) => episode.id === selectedEpisodeId) ?? selectedDataset?.episodes[0]
  const selectedCameras = selectedDataset && selectedEpisode ? camerasForReview(selectedDataset, selectedEpisode) : cameras
  const livePreviewCameras = cameras
    .map(({ key }) => liveCameras.find((camera) => camera.key === key))
    .filter((camera): camera is NonNullable<typeof camera> => Boolean(camera))
  const validEpisodes = selectedDataset?.episodes.filter((episode) => episode.status === 'valid').length ?? 0
  const avgQuality = selectedDataset && selectedDataset.episodes.length > 0
    ? Math.round(selectedDataset.episodes.reduce((sum, episode) => sum + episode.quality, 0) / selectedDataset.episodes.length)
    : 0

  const detailDatasetId = selectedDataset?.id
  const detailEpisodeId = selectedEpisode?.id
  const needsEpisodeDetail = selectedEpisode?.samples.length === 0
  useEffect(() => {
    if (mockMode || !detailDatasetId || !detailEpisodeId || !needsEpisodeDetail) return
    const detailKey = episodeKey(detailDatasetId, detailEpisodeId)
    if (episodeDetailRequestedKeysRef.current.has(detailKey)) return
    let cancelled = false
    void fetchDatasetEpisodeApi(detailDatasetId, detailEpisodeId)
      .then((episode) => {
        if (cancelled) return
        if (!episode) throw new Error('未找到 episode 详情')
        episodeDetailRequestedKeysRef.current.add(detailKey)
        setServerDatasets((items) =>
          items.map((dataset) =>
            dataset.id === detailDatasetId
              ? {
                  ...dataset,
                  episodes: dataset.episodes.map((item) =>
                    item.id === episode.id ? episodeFromApi(episode) : item,
                  ),
                }
              : dataset,
          ),
        )
        setBackendLoadError('')
      })
      .catch((error) => {
        if (!cancelled) setBackendLoadError(String(error))
      })
    return () => { cancelled = true }
  }, [detailDatasetId, detailEpisodeId, needsEpisodeDetail, refreshToken])

  const previewScope = `${detailDatasetId}:${detailEpisodeId}`
  const previewScopeRef = useRef(previewScope)
  previewScopeRef.current = previewScope
  useEffect(() => {
    setFrameIndex(0)
    setRequestedFrame(0)
    setImageStates({})
    setPlaying(false)
  }, [previewScope])

  const imageUrlsAt = (frame: number) => selectedEpisode ? selectedCameras.map((camera) => {
    const path = currentSample(selectedEpisode, frame).images?.[camera.key]
    return path?.startsWith('/api/') ? `${apiBase}${path}` : path
  }) : []
  const previewUrls = imageUrlsAt(frameIndex)
  const requestedUrls = imageUrlsAt(requestedFrame)
  // 只保留当前、请求目标及下一组样本；预加载节点切换为可见时不重新创建图片。
  const nextSampleFrame = selectedEpisode?.samples.find((sample) => sample.frame > requestedFrame)?.frame ?? 0
  const nextUrls = imageUrlsAt(nextSampleFrame)
  const bufferedUrls = selectedCameras.map((_, index) =>
    [...new Set([previewUrls[index], requestedUrls[index], nextUrls[index]].filter((url): url is string => Boolean(url)))])
  const activeUrls = new Set(bufferedUrls.flat())
  const activeUrlsRef = useRef(activeUrls)
  activeUrlsRef.current = activeUrls
  const onImageState = (url: string, state: 'loaded' | 'error') => {
    if (previewScopeRef.current !== previewScope || !activeUrlsRef.current.has(url)) return
    setImageStates((current) => ({
      ...Object.fromEntries(Object.entries(current).filter(([key]) => activeUrlsRef.current.has(key))), [url]: state,
    }))
  }
  const previewReady = previewUrls.every((url) => !url || imageStates[url] === 'loaded')
  const requestedReady = requestedUrls.every((url) => !url || imageStates[url] === 'loaded')
  const previewError = requestedUrls.some((url) => url && imageStates[url] === 'error')
  useEffect(() => {
    if (requestedReady) setFrameIndex(requestedFrame)
  }, [requestedReady, requestedFrame])
  useEffect(() => {
    if (!playing || !selectedEpisode || !previewReady || frameIndex !== requestedFrame) return
    const timer = window.setTimeout(() => {
      setRequestedFrame(frameIndex >= selectedEpisode.frames - 1 ? 0 : frameIndex + 1)
    }, Math.max(40, 1000 / (selectedEpisode.fps * playbackRate)))
    return () => window.clearTimeout(timer)
  }, [playing, playbackRate, selectedEpisode, previewReady, frameIndex, requestedFrame])

    /** 选择当前复核流程需要的数据。 */
const chooseEpisode = (episodeId: string) => {
    setSelectedEpisodeId(episodeId)
    setFrameIndex(0)
    setRequestedFrame(0)
    setImageStates({})
    setPlaying(false)
  }

    /** 选择当前复核流程需要的数据。 */
const chooseDataset = (datasetId: string) => {
    setSelectedDatasetId(datasetId)
    setSelectedEpisodeId(null)
    setFrameIndex(0)
    setRequestedFrame(0)
    setImageStates({})
    setPlaying(false)
  }

  const runMutation = async (request: () => Promise<unknown>, apply?: () => void) => {
    if (mutationPendingRef.current) return
    if (mockMode) {
      apply?.()
      return
    }
    mutationPendingRef.current = true
    setMutationPending(true)
    setBackendLoadError('')
    try {
      await request()
      if (!mountedRef.current) return
      apply?.()
      setRefreshToken((value) => value + 1)
    } catch (error) {
      if (mountedRef.current) setBackendLoadError(String(error))
    } finally {
      mutationPendingRef.current = false
      if (mountedRef.current) setMutationPending(false)
    }
  }

  /** 仅在后端确认后写入本地覆盖，失败时保留原值和编辑内容。 */
  const commitRename = () => {
    if (!renameTarget) return
    const target = renameTarget
    const name = target.value.trim() || target.id
    if (renameTarget.type === 'dataset') {
      void runMutation(() => renameDatasetApi(target.id, name), () => {
        setDatasetNameOverrides((current) => ({ ...current, [target.id]: name }))
        setRenameTarget(null)
      })
    } else if (target.datasetId) {
      const datasetId = target.datasetId
      void runMutation(() => updateDatasetEpisodeApi(datasetId, target.id, { name }), () => {
        setEpisodeNameOverrides((current) => ({ ...current, [episodeKey(datasetId, target.id)]: name }))
        setRenameTarget(null)
      })
    }
  }

  const deleteDataset = (datasetId: string) => {
    void runMutation(() => deleteDatasetApi(datasetId), () => {
      setDeletedDatasetIds((current) => [...current, datasetId])
    })
  }

  const deleteEpisode = (episodeId: string) => {
    if (!selectedDataset) return
    const datasetId = selectedDataset.id
    void runMutation(() => deleteDatasetEpisodeApi(datasetId, episodeId), () => {
      setDeletedEpisodeIds((current) => [...current, episodeKey(datasetId, episodeId)])
    })
  }

  const setEpisodeStatus = (episodeId: string, status: EpisodeStatus) => {
    if (!selectedDataset) return
    const datasetId = selectedDataset.id
    void runMutation(() => updateDatasetEpisodeApi(datasetId, episodeId, { status }), () => {
      setEpisodeStatusOverrides((current) => ({ ...current, [episodeKey(datasetId, episodeId)]: status }))
    })
  }

  const createDataset = () => {
    const name = `dataset_${Date.now()}`
    void runMutation(() => createDatasetApi(name))
  }

  const saveReview = () => {
    if (!selectedDataset || mockMode) return
    void runMutation(() => saveDatasetReviewApi(selectedDataset.id))
  }

    /** 调用数据集后端接口并同步界面状态。 */
const openHubUpload = () => {
  if (!selectedDataset) return
  setHubMessage('')
  setHubRepoId((current) => current || selectedDataset.id)
  setHubLocalPath(selectedDataset.root ?? '')
  setHubUploadOpen(true)
}

  const updateHubSwitch = (enabled: boolean) => {
    const previous = hubPushToHub
    setHubPushToHub(enabled)
    setHubMessage('')
    void updateDatasetHubApi(enabled)
      .then((response) => {
        const data = (response as { data?: { pushToHub?: boolean } }).data
        if (typeof data?.pushToHub === 'boolean') setHubPushToHub(data.pushToHub)
      })
      .catch((error) => {
        setHubPushToHub(previous)
        setBackendLoadError(String(error))
      })
  }

  const uploadToHub = () => {
    if (!selectedDataset) return
    const transientToken = hubToken.trim()
    setHubToken('')
    setHubUploading(true)
    setHubMessage('')
    void pushDatasetApi(selectedDataset.id, {
      repoId: hubRepoId.trim(),
      localPath: hubLocalPath.trim() || undefined,
      token: transientToken,
      private: hubPrivate,
      dryRun: hubDryRun,
    })
      .then((response) => {
        const data = (response as { data?: { queued?: boolean } }).data
        setHubUploadOpen(false)
        setHubMessage(hubDryRun ? 'Hub dry-run complete' : data?.queued ? 'Hub upload queued' : 'Hub upload complete')
        setRefreshToken((value) => value + 1)
      })
      .catch((error) => setBackendLoadError(String(error)))
      .finally(() => setHubUploading(false))
  }

  return (
    <div className="view-stack dataset-review-page">
      <section className="page-header">
        <div>
          <UiTitle level={2}>数据集质检 Dataset</UiTitle>
          <UiText secondary>选择数据集和 episode 后同步检查三路视频、双臂轨迹与双力传感器曲线。</UiText>
        </div>
        <UiSpace wrap>
          {!mockMode && <UiTag tone={backendLoadError ? 'error' : 'processing'}>{backendLoadError ? '后端数据异常' : '后端数据'}</UiTag>}
          <UiButton variant="primary" icon={<Database size={16} />} onClick={createDataset} disabled={mutationPending}>新建数据集</UiButton>
          {hubMessage && <UiTag tone="success">{hubMessage}</UiTag>}
          <UiTag tone={hubPushToHub ? 'success' : 'muted'}>{hubPushToHub ? 'Hub enabled' : 'Hub disabled'}</UiTag>
          <UiButton icon={<Upload size={16} />} onClick={openHubUpload} disabled={!selectedDataset}>Hub 上传</UiButton>
          <UiButton icon={<Save size={16} />} onClick={saveReview} disabled={!selectedDataset || mutationPending}>保存审核结果</UiButton>
        </UiSpace>
      </section>

      {backendLoadError && (
        <div role="alert" className="ui-alert ui-alert-error">
          {backendLoadError}
          <UiButton disabled={mutationPending} onClick={() => {
            episodeDetailRequestedKeysRef.current.clear()
            setRefreshToken((value) => value + 1)
          }}>重试读取</UiButton>
        </div>
      )}

      <section className="dataset-review-layout">
        <aside className="dataset-browser panel-surface">
          <div className="section-title">
            <span>数据集</span>
            <UiTag>{datasets.length}</UiTag>
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
                  <UiTag tone={dataset.status === '待审核' ? 'warning' : 'processing'}>{dataset.status}</UiTag>
                  <UiProgress percent={quality} status={quality >= 90 ? 'success' : quality >= 80 ? 'active' : 'exception'} />
                </button>
              )
            })}
          </div>

          {selectedDataset && (
            <div className="dataset-edit-actions">
              <UiButton disabled={mutationPending} icon={<Edit3 size={14} />} onClick={() => setRenameTarget({ type: 'dataset', id: selectedDataset.id, value: selectedDataset.name })}>
                重命名
              </UiButton>
              <UiButton
                danger
                disabled={mutationPending}
                icon={<Trash2 size={14} />}
                onClick={() => {
                  if (window.confirm('删除该数据集？')) deleteDataset(selectedDataset.id)
                }}
              >
                删除
              </UiButton>
            </div>
          )}

          <div className="section-title dataset-episode-title">
            <span>Episode</span>
            <UiTag>{selectedDataset?.episodes.length ?? 0}</UiTag>
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
              <div className="empty">请选择数据集和 episode</div>
            </section>
          ) : (
            <>
              <section className="panel-surface dataset-summary-strip">
                <div>
                  <UiTitle level={3}>{selectedDataset.name}</UiTitle>
                  <UiText secondary>{selectedEpisode.name} · {selectedEpisode.createdAt}</UiText>
                </div>
                <div className="dataset-summary-metrics">
                  <span><small>平均质量</small><b>{avgQuality}%</b></span>
                  <span><small>有效条数</small><b>{validEpisodes}/{selectedDataset.episodes.length}</b></span>
                  <span><small>当前帧</small><b>{frameIndex + 1}/{selectedEpisode.frames}</b></span>
                </div>
                <UiSpace wrap>
                  <UiButton disabled={mutationPending} icon={<Edit3 size={14} />} onClick={() => setRenameTarget({ type: 'episode', datasetId: selectedDataset.id, id: selectedEpisode.id, value: selectedEpisode.name })}>
                    重命名本条
                  </UiButton>
                  <UiButton disabled={mutationPending} icon={<CheckCircle2 size={14} />} onClick={() => setEpisodeStatus(selectedEpisode.id, 'valid')}>
                    标记有效
                  </UiButton>
                  <UiButton disabled={mutationPending} icon={<XCircle size={14} />} onClick={() => setEpisodeStatus(selectedEpisode.id, 'invalid')}>
                    标记无效
                  </UiButton>
                  <UiButton
                    danger
                    disabled={mutationPending}
                    icon={<Trash2 size={14} />}
                    onClick={() => {
                      if (window.confirm('删除该条数据？')) deleteEpisode(selectedEpisode.id)
                    }}
                  >
                    删除本条
                  </UiButton>
                </UiSpace>
              </section>

              <section className="panel-surface dataset-live-preview-strip">
                <div className="section-title">
                  <span>实时相机预览</span>
                  <UiTag tone="processing">Live</UiTag>
                </div>
                <div className="dataset-live-preview-grid">
                  {livePreviewCameras.map((camera) => (
                    <CameraPreview camera={camera} compact key={camera.key} />
                  ))}
                </div>
              </section>

              <section className="panel-surface dataset-quality-workbench">
                <div className="section-title">
                  <span>同步视频检查</span>
                  <UiSpace size={6} wrap>
                    {statusTag(selectedEpisode.status)}
                    <UiTag>{featureShapeText(selectedEpisode.featureSummary ?? selectedDataset.featureSummary)}</UiTag>
                    <UiTag>Fmax L/R {(selectedEpisode.maxForceLeft ?? 0).toFixed(2)} / {(selectedEpisode.maxForceRight ?? 0).toFixed(2)}</UiTag>
                    <UiTag tone={selectedEpisode.quality >= 85 ? 'success' : 'warning'}>质量 {selectedEpisode.quality}%</UiTag>
                  </UiSpace>
                </div>
                <div className="dataset-cockpit-grid">
                  <div className="dataset-video-grid">
                    {selectedCameras.map((camera) => (
                      <DatasetVideoPane camera={camera} episode={selectedEpisode} frameIndex={frameIndex} key={`${selectedDataset.id}:${selectedEpisode.id}:${camera.key}`}
                        imageUrls={bufferedUrls[selectedCameras.indexOf(camera)]} ready={previewReady}
                        onImageState={onImageState} />
                    ))}
                  </div>
                  <section className="dataset-quality-grid">
                    <TrajectoryPanel title="左机械臂轨迹" side="left" episode={selectedEpisode} frameIndex={frameIndex} />
                    <TrajectoryPanel title="右机械臂轨迹" side="right" episode={selectedEpisode} frameIndex={frameIndex} />
                    <ForcePanel title="左力传感器实时曲线" side="left" episode={selectedEpisode} frameIndex={frameIndex} />
                    <ForcePanel title="右力传感器实时曲线" side="right" episode={selectedEpisode} frameIndex={frameIndex} />
                  </section>
                </div>
                {previewError && <div className="ui-alert ui-alert-warning" role="alert">
                  图片加载失败，已保留当前画面；请重新选择片段或跳转位置重试。
                </div>}
                <div className="dataset-player-controls">
                  <UiButton icon={playing ? <Pause size={15} /> : <Play size={15} />} onClick={() => { if (playing) setRequestedFrame(frameIndex); setPlaying((value) => !value) }}>
                    {playing ? '暂停' : '播放'}
                  </UiButton>
                  <UiButton icon={<Rewind size={15} />} onClick={() => setRequestedFrame(clampFrame(frameIndex - 30, selectedEpisode.frames))}>
                    回退
                  </UiButton>
                  <UiButton icon={<FastForward size={15} />} onClick={() => setRequestedFrame(clampFrame(frameIndex + 30, selectedEpisode.frames))}>
                    快进
                  </UiButton>
                  <UiSegmented
                    value={String(playbackRate)}
                    onChange={(value) => setPlaybackRate(Number(value))}
                    options={[
                      { label: 'x1', value: '1' },
                      { label: 'x2', value: '2' },
                      { label: 'x4', value: '4' },
                    ]}
                  />
                  <input
                    type="range"
                    className="dataset-frame-slider"
                    min={0}
                    max={selectedEpisode.frames - 1}
                    value={frameIndex}
                    onChange={(event) => setRequestedFrame(clampFrame(Number(event.target.value), selectedEpisode.frames))}
                  />
                  <UiText secondary>
                    {(frameIndex / selectedEpisode.fps).toFixed(2)}s / {selectedEpisode.durationS.toFixed(2)}s
                  </UiText>
                </div>
              </section>

              <DatasetReplayPanel key={`${selectedDataset.id}/${selectedEpisode.id}`} datasetId={selectedDataset.id} episodeId={selectedEpisode.id} />
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

      {hubUploadOpen && (
        <div className="ui-modal-mask" role="presentation" onClick={() => { setHubToken(''); setHubUploadOpen(false) }}>
          <div className="ui-modal" role="dialog" aria-label="Hugging Face Hub 上传" onClick={(event) => event.stopPropagation()}>
            <header className="ui-modal-head"><strong>Hugging Face Hub 上传</strong></header>
            <div className="ui-modal-body">
              <label style={{ display: 'block', marginBottom: 10 }}>
                <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: '#7a8b9c', marginBottom: 4 }}>上传开关</span>
                <label className="ui-switch">
                  <input
                    aria-label="Hub 上传开关"
                    type="checkbox"
                    checked={hubPushToHub}
                    onChange={(event) => updateHubSwitch(event.target.checked)}
                  />
                  <span />
                </label>
              </label>
              <label style={{ display: 'block', marginBottom: 10 }}>
                <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: '#7a8b9c', marginBottom: 4 }}>Repo ID</span>
                <input
                  className="ui-input"
                  style={{ width: '100%' }}
                  value={hubRepoId}
                  placeholder="org/dataset-name"
                  onChange={(event) => setHubRepoId(event.target.value)}
                />
              </label>
              <label style={{ display: 'block', marginBottom: 10 }}>
                <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: '#7a8b9c', marginBottom: 4 }}>Local path</span>
                <input
                  className="ui-input"
                  style={{ width: '100%' }}
                  aria-label="Local path"
                  value={hubLocalPath}
                  placeholder="E:\\data group\\text50"
                  onChange={(event) => setHubLocalPath(event.target.value)}
                />
              </label>
              <label style={{ display: 'block', marginBottom: 10 }}>
                <span style={{ display: 'block', fontSize: 11, fontWeight: 700, color: '#7a8b9c', marginBottom: 4 }}>HF Token</span>
                <input
                  type="password"
                  className="ui-input"
                  style={{ width: '100%' }}
                  value={hubToken}
                  placeholder="hf_xxx"
                  autoComplete="off"
                  onChange={(event) => setHubToken(event.target.value)}
                />
              </label>
              <UiSpace wrap>
                <label className="ui-checkbox"><input type="checkbox" checked={hubPrivate} onChange={(event) => setHubPrivate(event.target.checked)} />Private</label>
                <label className="ui-checkbox"><input type="checkbox" checked={hubDryRun} onChange={(event) => setHubDryRun(event.target.checked)} />Dry-run</label>
              </UiSpace>
              {!hubDryRun && !hubPushToHub && (
                <UiTag tone="warning">关闭 Dry-run 前需要先打开 Hub 上传开关。</UiTag>
              )}
            </div>
            <div className="ui-modal-actions">
              <UiButton onClick={() => { setHubToken(''); setHubUploadOpen(false) }}>取消</UiButton>
              <UiButton
                variant="primary"
                loading={hubUploading}
                disabled={!hubRepoId.trim() || (!hubDryRun && !hubPushToHub)}
                onClick={uploadToHub}
              >
                开始上传
              </UiButton>
            </div>
          </div>
        </div>
      )}

      {renameTarget && (
        <div className="ui-modal-mask" role="presentation" onClick={() => { if (!mutationPending) setRenameTarget(null) }}>
          <div className="ui-modal" role="dialog" aria-label="重命名" onClick={(event) => event.stopPropagation()}>
            <header className="ui-modal-head">
              <strong>{renameTarget.type === 'dataset' ? '重命名数据集' : '重命名 Episode'}</strong>
            </header>
            <div className="ui-modal-body">
              <input
                className="ui-input"
                style={{ width: '100%' }}
                disabled={mutationPending}
                value={renameTarget.value}
                onChange={(event) => setRenameTarget((current) => current ? { ...current, value: event.target.value } : current)}
              />
            </div>
            <div className="ui-modal-actions">
              <UiButton disabled={mutationPending} onClick={() => setRenameTarget(null)}>取消</UiButton>
              <UiButton variant="primary" loading={mutationPending} onClick={commitRename}>保存</UiButton>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
