import { defaultConfig } from '../data'
import type {
  AppConfig,
  CameraTelemetry,
  DatasetApi,
  DatasetEpisodeApi,
  DatasetEpisodeStatusApi,
  LogEntry,
  ManualControlAxis,
  ManualControlSide,
  ManualGripperCommand,
  ManualSpeedMode,
  MotionHomeReferenceConfig,
  MotionOriginConfig,
  MotionWorkOriginOffsetConfig,
  ParameterSnapshot,
  ParameterSnapshotScope,
} from '../types'

export const apiBase = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:18082'
export const wsUrl = import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:18082/ws'
export const mockMode = import.meta.env.MODE === 'test'

const runtimeReleasePath = '/api/runtime/release_handles'
const runtimeShutdownPath = '/api/runtime/shutdown'
let runtimeReleaseListenerInstalled = false
let runtimeShutdownListenerInstalled = false

function sendRuntimeLifecycleCommand(path: string, reason: string) {
  if (mockMode || typeof window === 'undefined') return
  const url = `${apiBase}${path}`
  const body = JSON.stringify({ reason })
  try {
    if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
      const blob = new Blob([body], { type: 'application/json' })
      if (navigator.sendBeacon(url, blob)) return
    }
  } catch {
    // Keep page teardown best-effort; fetch keepalive below is the fallback.
  }
  if (typeof fetch !== 'function') return
  void fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => undefined)
}

function installRuntimeLifecycleOnClose(
  path: string,
  reason: string,
  installed: () => boolean,
  markInstalled: () => void,
) {
  if (mockMode || typeof window === 'undefined' || installed()) return
  markInstalled()
  let sent = false
  const release = () => {
    if (sent) return
    sent = true
    sendRuntimeLifecycleCommand(path, reason)
  }
  window.addEventListener('pagehide', release, { capture: true })
  window.addEventListener('beforeunload', release, { capture: true })
}

export function installRuntimeReleaseOnClose(reason = 'browser-close') {
  installRuntimeLifecycleOnClose(
    runtimeReleasePath,
    reason,
    () => runtimeReleaseListenerInstalled,
    () => {
      runtimeReleaseListenerInstalled = true
    },
  )
}

export function installAutoShutdownOnClose(reason = 'browser-close') {
  installRuntimeLifecycleOnClose(
    runtimeShutdownPath,
    reason,
    () => runtimeShutdownListenerInstalled,
    () => {
      runtimeShutdownListenerInstalled = true
    },
  )
}

type ApiErrorPayload = {
  detail?: {
    code?: unknown
    message?: unknown
    drift?: unknown
  }
}

export interface MotionOriginDriftAxis {
  axis: string
  deltaPulse: number
  deltaUi: number
  absDeltaUi: number
  unit: 'um' | 'deg'
  threshold: number
}

export interface MotionOriginDriftSide {
  side: ManualControlSide
  baseline: 'current' | 'previous'
  axes: MotionOriginDriftAxis[]
}

export interface MotionOriginCaptureDrift {
  requiresConfirmation: boolean
  thresholds: {
    translationUm: number
    rotationDeg: number
  }
  sides: MotionOriginDriftSide[]
}

export interface ApiCommandError extends Error {
  status?: number
  code?: string
  payload?: unknown
  drift?: MotionOriginCaptureDrift
}

export type {
  DatasetApi,
  DatasetCameraKeyApi,
  DatasetCameraResolutionApi,
  DatasetEpisodeApi,
  DatasetEpisodeSampleApi,
  DatasetEpisodeStatusApi,
  DatasetFeatureApi,
  DatasetFeatureSummaryApi,
} from '../types'

export interface PolicyModelApi {
  id: string
  name: string
  status: string
  note: string
  latencyMs: number
  updatedAt: number
}

export interface FineTuneJobApi {
  id: string
  datasetId: string
  baseModel: string
  outputDir: string
  status: string
  createdAt: number
  updatedAt?: number
  message: string
}

const localizedApiErrorMessages: Record<string, string> = {
  RECORDING_BUSY: '录制会话已在运行，请先结束当前会话后再开始新的录制。',
  WORK_ORIGIN_MISSING: '目标硬件臂工作原点未设置，请先在设置页记录工作原点后再连接遥操作。',
}
/** 格式化对应数值用于界面展示。 */
export function formatApiErrorMessage(status: number, payload?: unknown, fallback = 'command failed') {
  const detail = (payload as ApiErrorPayload | undefined)?.detail
  const code = typeof detail?.code === 'string' ? detail.code : ''
  const message = typeof detail?.message === 'string' ? detail.message : ''
  const localizedMessage = code ? localizedApiErrorMessages[code] : ''
  if (localizedMessage) return `${localizedMessage}（${status} ${code}）`
  const suffix = [code, message].filter(Boolean).join(': ')
  return suffix ? `${fallback}: ${status} ${suffix}` : `${fallback}: ${status}`
}
/** Build a typed command error while preserving backend detail payloads. */
async function commandErrorFromResponse(response: Response, fallback = 'command failed') {
  let payload: unknown
  try {
    payload = await response.clone().json()
  } catch {
    payload = undefined
  }
  const error = new Error(formatApiErrorMessage(response.status, payload, fallback)) as ApiCommandError
  const detail = (payload as ApiErrorPayload | undefined)?.detail
  error.status = response.status
  error.payload = payload
  if (typeof detail?.code === 'string') error.code = detail.code
  if (isMotionOriginCaptureDrift(detail?.drift)) error.drift = detail.drift
  return error
}
/** Narrow backend drift metadata before attaching it to command errors. */
function isMotionOriginCaptureDrift(value: unknown): value is MotionOriginCaptureDrift {
  if (!value || typeof value !== 'object') return false
  const drift = value as MotionOriginCaptureDrift
  return (
    typeof drift.requiresConfirmation === 'boolean' &&
    Boolean(drift.thresholds) &&
    typeof drift.thresholds.translationUm === 'number' &&
    typeof drift.thresholds.rotationDeg === 'number' &&
    Array.isArray(drift.sides)
  )
}
/** Load persisted runtime settings; mock mode returns an isolated copy. */
export async function fetchConfig(): Promise<AppConfig> {
  if (mockMode) return structuredClone(defaultConfig)
  const response = await fetch(`${apiBase}/api/settings`)
  if (!response.ok) throw new Error(`settings fetch failed: ${response.status}`)
  return response.json() as Promise<AppConfig>
}

export interface HardwareProbeStatus {
  camera?: { ok: boolean; message: string }
  force?: { ok: boolean; message: string }
  gripper?: {
    ok: boolean | null
    message: string
    ports?: Array<{ side: string; port: string; slaveId: number; baudrate: number }>
  }
  omega7?: {
    ok: boolean
    message: string
    hands?: Array<{
      side: string
      requestedId: number
      connected: boolean
      lastReadOk: boolean
      deviceId: number | string
      serial: string
    }>
  }
  pico?: { ok: boolean; message: string }
  runtime?: {
    backendDeployment?: {
      restartRequired?: boolean
      message?: string
      latestPath?: string | null
    }
    halDeployment?: {
      restartRequired?: boolean
      message?: string
      components?: Record<string, { pendingNext?: boolean }>
    }
  }
}
/** Probe hardware status without changing device state. */
export async function fetchHardwareStatus(): Promise<HardwareProbeStatus> {
  if (mockMode) return {}
  const response = await fetch(`${apiBase}/api/hardware/status`)
  if (!response.ok) throw new Error(`hardware status fetch failed: ${response.status}`)
  return response.json() as Promise<HardwareProbeStatus>
}

/** 发送或封装对应的后端命令。 */
export async function postCommand(path: string, body?: unknown) {
  if (mockMode) return { ok: true, path, body, ts: Date.now() }
  // 调用方可以传完整接口路径或短路径，这里统一规范成后端路由。
  const apiPath = path.startsWith('/api/') ? path : `/api${path.startsWith('/') ? path : `/${path}`}`
  const response = await fetch(`${apiBase}${apiPath}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await commandErrorFromResponse(response)
  return response.json() as Promise<unknown>
}

/** Persist the full settings tree through the backend validator. */
export async function putConfig(config: AppConfig): Promise<AppConfig> {
  if (mockMode) return structuredClone(config)
  const response = await fetch(`${apiBase}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!response.ok) throw new Error(`settings save failed: ${response.status}`)
  return response.json() as Promise<AppConfig>
}
/** 应用对应配置或状态。 */
export async function applyConfig(config?: AppConfig) {
  if (mockMode) return { ok: true, data: { config }, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/settings/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: config ? JSON.stringify(config) : undefined,
  })
  if (!response.ok) throw new Error(`settings apply failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 从后端读取对应数据。 */
export async function fetchParameterSnapshots(scope?: ParameterSnapshotScope): Promise<ParameterSnapshot[]> {
  if (mockMode) return []
  const suffix = scope ? `?scope=${encodeURIComponent(scope)}` : ''
  const response = await fetch(`${apiBase}/api/settings/snapshots${suffix}`)
  if (!response.ok) throw new Error(`snapshots fetch failed: ${response.status}`)
  return response.json() as Promise<ParameterSnapshot[]>
}
/** 描述当前方法的功能边界。 */
export async function createParameterSnapshot(scope: ParameterSnapshotScope, name: string, config?: unknown) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/settings/snapshots`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, name, config }),
  })
  if (!response.ok) throw new Error(`snapshot create failed: ${response.status}`)
  return response.json() as Promise<{ ok: boolean; data?: { snapshot?: ParameterSnapshot; snapshots?: ParameterSnapshot[] } }>
}
/** 应用对应配置或状态。 */
export async function applyParameterSnapshotApi(id: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/settings/snapshots/${encodeURIComponent(id)}/apply`, { method: 'POST' })
  if (!response.ok) throw new Error(`snapshot apply failed: ${response.status}`)
  return response.json() as Promise<{ ok: boolean; data?: { config?: AppConfig; snapshots?: ParameterSnapshot[] } }>
}
/** 删除对应数据并同步界面状态。 */
export async function deleteParameterSnapshotApi(id: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/settings/snapshots/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`snapshot delete failed: ${response.status}`)
  return response.json() as Promise<{ ok: boolean; data?: { snapshots?: ParameterSnapshot[] } }>
}
/** 发送或封装对应的后端命令。 */
export async function sendSettingsLogCommand(channel: LogEntry['channel'], msg: string, level: LogEntry['level'] = 'INFO') {
  return postCommand('/settings/log_command', { channel, msg, level })
}
/** 发送或封装对应的后端命令。 */
export const reconnectHal = () => postCommand('/hal/reconnect')
/** 发送或封装对应的后端命令。 */
export const enumerateCamera = (camera: CameraTelemetry['key']) =>
  postCommand(`/cameras/${camera}/enumerate`)
/** 发送或封装对应的后端命令。 */
export const reconnectCamera = (camera: CameraTelemetry['key']) =>
  postCommand(`/cameras/${camera}/reconnect`)
/** 应用对应配置或状态。 */
export const applyCameraTuning = (camera: CameraTelemetry['key'], config?: AppConfig) =>
  postCommand(`/cameras/${camera}/tuning/apply`, config)
export interface PicoCommandResponse {
  ok: boolean
  data?: {
    ok?: boolean
    message?: string
    stdout?: string
    stderr?: string
  }
  ts?: number
}
/** 发送或封装对应的后端命令。 */
export const connectPicoAdb = () => postCommand('/pico/adb/connect') as Promise<PicoCommandResponse>
/** 发送或封装对应的后端命令。 */
export const startPicoVision = () => postCommand('/pico/vision/start') as Promise<PicoCommandResponse>
/** 发送或封装对应的后端命令。 */
export const stopPicoVision = () => postCommand('/pico/vision/stop') as Promise<PicoCommandResponse>
/** 发送或封装对应的后端命令。 */
export const checkPicoStatus = () => postCommand('/pico/status/check') as Promise<PicoCommandResponse>
/** 计算或执行手动控制的对应逻辑。 */
export async function manualAxisMove(
  side: ManualControlSide,
  axis: ManualControlAxis,
  direction: -1 | 1,
  step: number,
  speedMode: ManualSpeedMode,
) {
  return postCommand('/motion/manual_axis_move', { side, axis, direction, step, speedMode })
}
/** 发送或封装对应的后端命令。 */
export const enableMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/enable_all`)
/** 发送或封装对应的后端命令。 */
export const disableMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/disable_all`)
/** 停止对应流程。 */
export const stopMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/stop`)
/** 发送或封装对应的后端命令。 */
export const homeMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/home`)
/** 发送或封装对应的后端命令。 */
export const returnMotionOriginSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/return_origin`)

export interface MotionPreviousRestoreStatus {
  available: boolean
  restorable: boolean
  message: string
}

export interface MotionOriginResponse {
  ok: boolean
  data?: {
    origin?: MotionOriginConfig
    homeReference?: MotionHomeReferenceConfig
    workOriginOffset?: MotionWorkOriginOffsetConfig
    config?: AppConfig
    originCaptureDrift?: MotionOriginCaptureDrift
    previousRestore?: MotionPreviousRestoreStatus
  }
}

/** 从后端读取对应数据。 */
export async function fetchMotionOrigin(): Promise<MotionOriginResponse> {
  if (mockMode) {
    return {
      ok: true,
      data: {
        origin: structuredClone(defaultConfig.motion.origin),
        homeReference: structuredClone(defaultConfig.motion.homeReference),
        workOriginOffset: structuredClone(defaultConfig.motion.workOriginOffset),
        previousRestore: {
          available: false,
          restorable: false,
          message: 'previous motion work origin is not available',
        },
      },
    }
  }
  const response = await fetch(`${apiBase}/api/motion/origin`)
  if (!response.ok) throw new Error(`motion origin fetch failed: ${response.status}`)
  return response.json() as Promise<MotionOriginResponse>
}
/** 发送或封装对应的后端命令。 */
export const captureMotionOrigin = (side?: ManualControlSide, options?: { confirmLargeDrift?: boolean }) =>
  postCommand(
    side ? `/motion/${side}/origin/capture` : '/motion/origin/capture',
    options?.confirmLargeDrift ? { confirmLargeDrift: true } : undefined,
  ) as Promise<MotionOriginResponse>

/** 删除对应数据并同步界面状态。 */
export const clearMotionOrigin = (side?: ManualControlSide) =>
  postCommand(side ? `/motion/${side}/origin/clear` : '/motion/origin/clear') as Promise<MotionOriginResponse>
/** 应用对应配置或状态。 */
export const restorePreviousMotionOrigin = () =>
  postCommand('/motion/origin/restore_previous') as Promise<MotionOriginResponse>
/** 发送或封装对应的后端命令。 */
export async function gripperCommand(side: ManualControlSide, command: ManualGripperCommand, targetMm?: number, forceLimitN?: number) {
  return postCommand(`/gripper/${side}/command`, { side, command, targetMm, forceLimitN })
}

/** 启动对应流程。 */
export const startGripperTeleop = () => postCommand('/teleop/gripper/start')
/** 停止对应流程。 */
export const stopGripperTeleop = () => postCommand('/teleop/gripper/stop')
/** 从后端读取对应数据。 */
export const fetchGripperTeleopStatus = () => fetch(`${apiBase}/api/teleop/gripper/status`).then((r) => r.json())

// 说明当前代码块的功能用途。
/** 描述当前方法的功能边界。 */
export const createSession = (datasetName: string, task: string) =>
  postCommand('/record/session/create', { dataset_name: datasetName, task }) as Promise<RecordSessionCommandResponse>

export interface RecordStatusApi {
  active?: boolean
  recording?: boolean
  datasetName?: string
  task?: string
  episodeIndex?: number
  frameCount?: number
  elapsedS?: number
  fps?: number
  resetPending?: boolean
  resetRequiredSides?: Array<'left' | 'right'>
  resetReturnedSides?: Array<'left' | 'right'>
  resetReady?: boolean
}

export interface RecordSessionCommandResponse {
  ok?: boolean
  data?: RecordStatusApi
  ts?: number
}
/** 从后端读取对应数据。 */
export async function fetchRecordStatus(): Promise<RecordStatusApi> {
  if (mockMode) return { active: false, recording: false }
  const response = await fetch(`${apiBase}/api/record/status`)
  if (!response.ok) throw await commandErrorFromResponse(response, 'record status fetch failed')
  const payload = await response.json() as { data?: RecordStatusApi }
  return payload.data ?? {}
}

export interface RecordEpisodeSaveApi {
  id?: string
  episodeIndex?: number
  frames?: number
  durationS?: number
  lateFrames?: number
  maxForceLeft?: number
  maxForceRight?: number
  cameraDrops?: Partial<Record<'global' | 'wrist_left' | 'wrist_right', number>>
  warnings?: string[]
}

export interface SaveEpisodeResponse {
  ok?: boolean
  data?: {
    episode?: RecordEpisodeSaveApi
    status?: unknown
  }
  ts?: number
}
/** 描述当前方法的功能边界。 */
export const saveEpisode = () =>
  postCommand('/record/episode/save') as Promise<SaveEpisodeResponse>
/** 删除对应数据并同步界面状态。 */
export const discardEpisode = () => postCommand('/record/episode/discard')

/** 描述当前方法的功能边界。 */
export const finishSession = () => postCommand('/record/session/finish')
/** 描述当前方法的功能边界。 */
export const skipReset = () => postCommand('/record/reset/skip')

/**
 * 读取本地数据集列表。
 *
 * 业务背景：复核页依赖该列表展示可见片段、结构摘要和本地数据集状态。
 *
 * 返回数据集摘要数组；模拟模式下返回空数组。
 * 当响应状态非成功状态时抛出错误。
 */
export async function fetchDatasets(): Promise<DatasetApi[]> {
  if (mockMode) return []
  const response = await fetch(`${apiBase}/api/datasets`)
  if (!response.ok) throw new Error(`datasets fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { datasets?: DatasetApi[] } }
  return payload.data?.datasets ?? []
}

/**
 * 读取指定片段的复核详情。
 *
 * 业务背景：复核页需要按需查看特征形状、抽样轨迹和相机分辨率来源，
 * 该函数保留后端响应外层结构，只向调用方暴露数据里的片段。
 *
 * 第一个参数是本地数据集编号，必须是后端可解析的安全目录名。
 * 第二个参数是片段编号，例如“片段_000001”。
 * 返回后端片段详情；模拟模式下返回空值。
 * 当响应状态非成功状态时抛出错误。
 */
export async function fetchDatasetEpisodeApi(datasetId: string, episodeId: string): Promise<DatasetEpisodeApi | null> {
  if (mockMode) return null
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}`)
  if (!response.ok) throw new Error(`episode fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { episode?: DatasetEpisodeApi } }
  return payload.data?.episode ?? null
}
/** 描述当前方法的功能边界。 */
export async function createDatasetApi(name: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!response.ok) throw new Error(`dataset create failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export async function renameDatasetApi(datasetId: string, name: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!response.ok) throw new Error(`dataset rename failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export async function saveDatasetReviewApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/review/save`, { method: 'POST' })
  if (!response.ok) throw new Error(`dataset review save failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export async function exportDatasetApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/export`, { method: 'POST' })
  if (!response.ok) throw new Error(`dataset export failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

export async function updateDatasetHubApi(pushToHub: boolean) {
  if (mockMode) return { ok: true, data: { pushToHub }, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/hub`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pushToHub }),
  })
  if (!response.ok) throw new Error(`dataset hub update failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 从后端读取对应数据。 */
export async function fetchDatasetStatsApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/stats`)
  if (!response.ok) throw new Error(`dataset stats failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export async function splitDatasetApi(datasetId: string, ratios: { train: number; val: number; test: number }) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/split`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ratios }),
  })
  if (!response.ok) throw new Error(`dataset split failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export async function cleanDatasetApi(datasetId: string, apply = false) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/clean`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ apply, minFrames: 2 }),
  })
  if (!response.ok) throw new Error(`dataset clean failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export interface DatasetHubUploadRequest {
  repoId: string
  localPath?: string
  token?: string
  private?: boolean
  dryRun?: boolean
}

export async function pushDatasetApi(datasetId: string, request: DatasetHubUploadRequest) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/push`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  if (!response.ok) throw new Error(`dataset push failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 删除对应数据并同步界面状态。 */
export async function deleteDatasetApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`dataset delete failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 描述当前方法的功能边界。 */
export async function updateDatasetEpisodeApi(datasetId: string, episodeId: string, patch: { name?: string; status?: DatasetEpisodeStatusApi }) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!response.ok) throw new Error(`episode update failed: ${response.status}`)
  return response.json() as Promise<unknown>
}
/** 删除对应数据并同步界面状态。 */
export async function deleteDatasetEpisodeApi(datasetId: string, episodeId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`episode delete failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

// 说明当前代码块的功能用途。
/** 发送或封装对应的后端命令。 */
export const tareForceSensors = () => postCommand('/sensors/tare')
/** 发送或封装对应的后端命令。 */
export const tareForceSensor = (side: ManualControlSide) => postCommand(`/force/${side}/tare`)

// 说明当前代码块的功能用途。
/** 发送或封装对应的后端命令。 */
export const toggleClutch = () => postCommand('/teleop/clutch_toggle')
/** 设置当前流程的对应状态。 */
export const setSpeedMode = (mode: 'coarse' | 'medium' | 'fine') =>
  postCommand('/teleop/speed', { mode })
/** 发送或封装对应的后端命令。 */
export const connectTeleopHand = (side: ManualControlSide) =>
  postCommand(`/teleop/${side}/connect`)
/** 发送或封装对应的后端命令。 */
export const disconnectTeleopHand = (side: ManualControlSide) =>
  postCommand(`/teleop/${side}/disconnect`)
/** 从后端读取对应数据。 */
export const fetchTeleopMappingStatus = () => fetch(`${apiBase}/api/teleop/mapping/status`).then((r) => r.json())
/** 设置当前流程的对应状态。 */
export const setTeleopGravityCompensation = (
  side: ManualControlSide,
  value: boolean | { enabled: boolean; scale?: number },
) => postCommand(`/teleop/${side}/gravity_compensation`, typeof value === 'boolean' ? { enabled: value } : value)

/** 发送或封装对应的后端命令。 */
export const zeroTeleopForceFeedback = (side: ManualControlSide) =>
  postCommand(`/teleop/${side}/zero_force_feedback`)

// 说明当前代码块的功能用途。
/** 发送或封装对应的后端命令。 */
export const emergencyStop = () => postCommand('/motion/emergency_stop')
/** 应用对应配置或状态。 */
export const acknowledgeSafety = () => postCommand('/motion/safety/acknowledge')
/** 发送或封装对应的后端命令。 */
export const homeAll = () => postCommand('/motion/home_all')
/** 从后端读取对应数据。 */
export async function fetchModels(): Promise<{ models: PolicyModelApi[]; activeModelId: string }> {
  if (mockMode) return { models: [], activeModelId: '' }
  const response = await fetch(`${apiBase}/api/models`)
  if (!response.ok) throw new Error(`models fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { models?: PolicyModelApi[]; activeModelId?: string } }
  return { models: payload.data?.models ?? [], activeModelId: payload.data?.activeModelId ?? '' }
}
/** 发送或封装对应的后端命令。 */
export const importModelApi = (name: string, path = '') =>
  postCommand('/models/import', { name, path })
/** 启动对应流程。 */
export const startModelApi = (modelId: string) =>
  postCommand(`/models/${encodeURIComponent(modelId)}/start`)
/** 停止对应流程。 */
export const stopModelApi = (modelId: string) =>
  postCommand(`/models/${encodeURIComponent(modelId)}/stop`)
/** 启动对应流程。 */
export const startAutoExecution = (modelId?: string) =>
  postCommand('/auto/start', { modelId })
/** 停止对应流程。 */
export const stopAutoExecution = () =>
  postCommand('/auto/stop')
/** 发送或封装对应的后端命令。 */
export const queueAutoAction = (payload: unknown) =>
  postCommand('/auto/action', payload)
/** 发送或封装对应的后端命令。 */
export const dispatchNextAutoAction = () =>
  postCommand('/auto/dispatch_next')
/** 从后端读取对应数据。 */
export async function fetchFineTuneJobs(): Promise<FineTuneJobApi[]> {
  if (mockMode) return []
  const response = await fetch(`${apiBase}/api/fine_tune/jobs`)
  if (!response.ok) throw new Error(`fine-tune jobs fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { jobs?: FineTuneJobApi[] } }
  return payload.data?.jobs ?? []
}
/** 启动对应流程。 */
export const startFineTuneJobApi = (datasetId: string, baseModel: string, outputDir?: string) =>
  postCommand('/fine_tune/jobs', { datasetId, baseModel, outputDir })
/** 停止对应流程。 */
export const cancelFineTuneJobApi = (jobId: string) =>
  postCommand(`/fine_tune/jobs/${encodeURIComponent(jobId)}/cancel`)
