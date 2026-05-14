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
  MotionOriginConfig,
  ParameterSnapshot,
  ParameterSnapshotScope,
} from '../types'

export const apiBase = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:18082'
export const wsUrl = import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:18082/ws'
export const mockMode = import.meta.env.MODE === 'test'
export const autoShutdownOnClose = import.meta.env.VITE_AUTO_SHUTDOWN_ON_CLOSE === 'true'

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

export function installAutoShutdownOnClose() {
  if (!autoShutdownOnClose || mockMode || typeof window === 'undefined') return
  let sent = false
  const requestShutdown = () => {
    // pagehide 阶段不能依赖普通 fetch 完成，优先使用 sendBeacon 请求后端延迟停栈。
    if (sent) return
    sent = true
    const url = `${apiBase}/api/runtime/shutdown`
    const body = JSON.stringify({ reason: 'browser-close' })
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))
      return
    }
    void fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined)
  }
  window.addEventListener('pagehide', requestShutdown, { once: true })
}

export function installRuntimeReleaseOnClose() {
  if (mockMode || typeof window === 'undefined') return
  let sent = false
  const requestRelease = () => {
    if (sent) return
    sent = true
    const url = `${apiBase}/api/runtime/release_handles`
    const body = JSON.stringify({ reason: 'browser-close' })
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))
      return
    }
    void fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined)
  }
  window.addEventListener('pagehide', requestRelease, { once: true })
}

export async function fetchConfig(): Promise<AppConfig> {
  if (mockMode) return structuredClone(defaultConfig)
  const response = await fetch(`${apiBase}/api/settings`)
  if (!response.ok) throw new Error(`settings fetch failed: ${response.status}`)
  return response.json() as Promise<AppConfig>
}

export async function fetchHardwareStatus(): Promise<{
  camera?: { ok: boolean; message: string }
  force?: { ok: boolean; message: string }
  gripper?: { ok: boolean; message: string }
  pico?: { ok: boolean; message: string }
}> {
  if (mockMode) return {}
  const response = await fetch(`${apiBase}/api/hardware/status`)
  if (!response.ok) throw new Error(`hardware status fetch failed: ${response.status}`)
  return response.json() as Promise<{
    camera?: { ok: boolean; message: string }
    force?: { ok: boolean; message: string }
    gripper?: { ok: boolean; message: string }
    pico?: { ok: boolean; message: string }
  }>
}

export async function postCommand(path: string, body?: unknown) {
  if (mockMode) return { ok: true, path, body, ts: Date.now() }
  // 调用方可以传 `/api/foo` 或 `foo`，这里统一规范成后端路由。
  const apiPath = path.startsWith('/api/') ? path : `/api${path.startsWith('/') ? path : `/${path}`}`
  const response = await fetch(`${apiBase}${apiPath}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`command failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

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

export async function fetchParameterSnapshots(scope?: ParameterSnapshotScope): Promise<ParameterSnapshot[]> {
  if (mockMode) return []
  const suffix = scope ? `?scope=${encodeURIComponent(scope)}` : ''
  const response = await fetch(`${apiBase}/api/settings/snapshots${suffix}`)
  if (!response.ok) throw new Error(`snapshots fetch failed: ${response.status}`)
  return response.json() as Promise<ParameterSnapshot[]>
}

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

export async function applyParameterSnapshotApi(id: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/settings/snapshots/${encodeURIComponent(id)}/apply`, { method: 'POST' })
  if (!response.ok) throw new Error(`snapshot apply failed: ${response.status}`)
  return response.json() as Promise<{ ok: boolean; data?: { config?: AppConfig; snapshots?: ParameterSnapshot[] } }>
}

export async function deleteParameterSnapshotApi(id: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/settings/snapshots/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`snapshot delete failed: ${response.status}`)
  return response.json() as Promise<{ ok: boolean; data?: { snapshots?: ParameterSnapshot[] } }>
}

export async function sendSettingsLogCommand(channel: LogEntry['channel'], msg: string, level: LogEntry['level'] = 'INFO') {
  return postCommand('/settings/log_command', { channel, msg, level })
}

export const reconnectHal = () => postCommand('/hal/reconnect')

export const enumerateCamera = (camera: CameraTelemetry['key']) =>
  postCommand(`/cameras/${camera}/enumerate`)

export const reconnectCamera = (camera: CameraTelemetry['key']) =>
  postCommand(`/cameras/${camera}/reconnect`)

export const applyCameraTuning = (camera: CameraTelemetry['key'], config?: AppConfig) =>
  postCommand(`/cameras/${camera}/tuning/apply`, config)

export async function manualAxisMove(
  side: ManualControlSide,
  axis: ManualControlAxis,
  direction: -1 | 1,
  step: number,
  speedMode: ManualSpeedMode,
) {
  return postCommand('/motion/manual_axis_move', { side, axis, direction, step, speedMode })
}

export const enableMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/enable_all`)

export const disableMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/disable_all`)

export const stopMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/stop`)

export const homeMotionSide = (side: ManualControlSide) =>
  postCommand(`/motion/${side}/home`)

export interface MotionOriginResponse {
  ok: boolean
  data?: {
    origin?: MotionOriginConfig
    config?: AppConfig
  }
}

export async function fetchMotionOrigin(): Promise<MotionOriginResponse> {
  if (mockMode) return { ok: true, data: { origin: structuredClone(defaultConfig.motion.origin) } }
  const response = await fetch(`${apiBase}/api/motion/origin`)
  if (!response.ok) throw new Error(`motion origin fetch failed: ${response.status}`)
  return response.json() as Promise<MotionOriginResponse>
}

export const captureMotionOrigin = (side?: ManualControlSide) =>
  postCommand(side ? `/motion/${side}/origin/capture` : '/motion/origin/capture') as Promise<MotionOriginResponse>

export const clearMotionOrigin = (side?: ManualControlSide) =>
  postCommand(side ? `/motion/${side}/origin/clear` : '/motion/origin/clear') as Promise<MotionOriginResponse>

export async function gripperCommand(side: ManualControlSide, command: ManualGripperCommand, targetMm?: number, forceLimitN?: number) {
  return postCommand(`/gripper/${side}/command`, { side, command, targetMm, forceLimitN })
}

export const startGripperTeleop = () => postCommand('/teleop/gripper/start')
export const stopGripperTeleop = () => postCommand('/teleop/gripper/stop')
export const fetchGripperTeleopStatus = () => fetch(`${apiBase}/api/teleop/gripper/status`).then((r) => r.json())

// Session control
export const createSession = (datasetName: string, task: string) =>
  postCommand('/record/session/create', { dataset_name: datasetName, task })

export const saveEpisode = () => postCommand('/record/episode/save')

export const discardEpisode = () => postCommand('/record/episode/discard')

export const finishSession = () => postCommand('/record/session/finish')

export const skipReset = () => postCommand('/record/reset/skip')

/**
 * 读取本地数据集列表。
 *
 * 业务背景：复核页依赖该列表展示可见 episode、schema 摘要和本地数据集状态。
 *
 * @returns 数据集摘要数组；mock 模式下返回空数组。
 * @throws 当 HTTP 状态非 2xx 时抛出 Error。
 */
export async function fetchDatasets(): Promise<DatasetApi[]> {
  if (mockMode) return []
  const response = await fetch(`${apiBase}/api/datasets`)
  if (!response.ok) throw new Error(`datasets fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { datasets?: DatasetApi[] } }
  return payload.data?.datasets ?? []
}

/**
 * 读取指定 episode 的复核详情。
 *
 * 业务背景：复核页需要按需查看 feature shape、抽样轨迹和相机分辨率来源，
 * 该函数保留后端 ok/data/ts envelope，只向调用方暴露 data.episode。
 *
 * @param datasetId 本地数据集 ID，必须是后端可解析的安全目录名。
 * @param episodeId episode ID，例如 episode_000001。
 * @returns 后端返回的 episode 详情；mock 模式下返回 null。
 * @throws 当 HTTP 状态非 2xx 时抛出 Error。
 */
export async function fetchDatasetEpisodeApi(datasetId: string, episodeId: string): Promise<DatasetEpisodeApi | null> {
  if (mockMode) return null
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}`)
  if (!response.ok) throw new Error(`episode fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { episode?: DatasetEpisodeApi } }
  return payload.data?.episode ?? null
}

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

export async function saveDatasetReviewApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/review/save`, { method: 'POST' })
  if (!response.ok) throw new Error(`dataset review save failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

export async function exportDatasetApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/export`, { method: 'POST' })
  if (!response.ok) throw new Error(`dataset export failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

export async function fetchDatasetStatsApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/stats`)
  if (!response.ok) throw new Error(`dataset stats failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

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

export async function pushDatasetApi(datasetId: string, repoId: string, dryRun = true) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/push`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repoId, dryRun }),
  })
  if (!response.ok) throw new Error(`dataset push failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

export async function deleteDatasetApi(datasetId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`dataset delete failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

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

export async function deleteDatasetEpisodeApi(datasetId: string, episodeId: string) {
  if (mockMode) return { ok: true, data: {}, ts: Date.now() }
  const response = await fetch(`${apiBase}/api/datasets/${encodeURIComponent(datasetId)}/episodes/${encodeURIComponent(episodeId)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`episode delete failed: ${response.status}`)
  return response.json() as Promise<unknown>
}

// Sensors
export const tareForceSensors = () => postCommand('/sensors/tare')

export const tareForceSensor = (side: ManualControlSide) => postCommand(`/force/${side}/tare`)

// Teleop
export const toggleClutch = () => postCommand('/teleop/clutch_toggle')

export const setSpeedMode = (mode: 'coarse' | 'medium' | 'fine') =>
  postCommand('/teleop/speed', { mode })

export const connectTeleopHand = (side: ManualControlSide) =>
  postCommand(`/teleop/${side}/connect`)

export const disconnectTeleopHand = (side: ManualControlSide) =>
  postCommand(`/teleop/${side}/disconnect`)

export const setTeleopGravityCompensation = (side: ManualControlSide, enabled: boolean) =>
  postCommand(`/teleop/${side}/gravity_compensation`, { enabled })

export const zeroTeleopForceFeedback = (side: ManualControlSide) =>
  postCommand(`/teleop/${side}/zero_force_feedback`)

// Emergency
export const emergencyStop = () => postCommand('/motion/emergency_stop')

export const acknowledgeSafety = () => postCommand('/motion/safety/acknowledge')

export const homeAll = () => postCommand('/motion/home_all')

export async function fetchModels(): Promise<{ models: PolicyModelApi[]; activeModelId: string }> {
  if (mockMode) return { models: [], activeModelId: '' }
  const response = await fetch(`${apiBase}/api/models`)
  if (!response.ok) throw new Error(`models fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { models?: PolicyModelApi[]; activeModelId?: string } }
  return { models: payload.data?.models ?? [], activeModelId: payload.data?.activeModelId ?? '' }
}

export const importModelApi = (name: string, path = '') =>
  postCommand('/models/import', { name, path })

export const startModelApi = (modelId: string) =>
  postCommand(`/models/${encodeURIComponent(modelId)}/start`)

export const stopModelApi = (modelId: string) =>
  postCommand(`/models/${encodeURIComponent(modelId)}/stop`)

export const startAutoExecution = (modelId?: string) =>
  postCommand('/auto/start', { modelId })

export const stopAutoExecution = () =>
  postCommand('/auto/stop')

export const queueAutoAction = (payload: unknown) =>
  postCommand('/auto/action', payload)

export const dispatchNextAutoAction = () =>
  postCommand('/auto/dispatch_next')

export async function fetchFineTuneJobs(): Promise<FineTuneJobApi[]> {
  if (mockMode) return []
  const response = await fetch(`${apiBase}/api/fine_tune/jobs`)
  if (!response.ok) throw new Error(`fine-tune jobs fetch failed: ${response.status}`)
  const payload = await response.json() as { data?: { jobs?: FineTuneJobApi[] } }
  return payload.data?.jobs ?? []
}

export const startFineTuneJobApi = (datasetId: string, baseModel: string, outputDir?: string) =>
  postCommand('/fine_tune/jobs', { datasetId, baseModel, outputDir })

export const cancelFineTuneJobApi = (jobId: string) =>
  postCommand(`/fine_tune/jobs/${encodeURIComponent(jobId)}/cancel`)
