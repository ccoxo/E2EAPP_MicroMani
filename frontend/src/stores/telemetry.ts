import { create } from 'zustand'
import {
  acknowledgeSafety as acknowledgeSafetyApi,
  applyParameterSnapshotApi,
  createParameterSnapshot as createParameterSnapshotApi,
  createSession as createRecordSessionApi,
  deleteParameterSnapshotApi,
  discardEpisode as discardRecordEpisodeApi,
  emergencyStop as emergencyStopApi,
  fetchConfig,
  fetchHardwareStatus,
  fetchParameterSnapshots,
  finishSession as finishRecordSessionApi,
  gripperCommand as gripperCommandApi,
  homeAll as homeAllApi,
  manualAxisMove as manualAxisMoveApi,
  mockMode,
  saveEpisode as saveRecordEpisodeApi,
  sendSettingsLogCommand,
  setSpeedMode as setRecordSpeedModeApi,
  startAutoExecution,
  skipReset as skipRecordResetApi,
  stopAutoExecution,
  tareForceSensors as tareForceSensorsApi,
  toggleClutch as toggleClutchApi,
  putConfig,
  wsUrl,
} from '../api/index'
import { defaultConfig, defaultDiagnostics, logChannels } from '../data'
import type {
  AppConfig,
  DiagnosticItem,
  EpisodeRecord,
  LogEntry,
  LogLevel,
  ManualControlAction,
  ManualControlAxis,
  ManualControlState,
  ManualGripperCommand,
  ManualSpeedMode,
  MotionCardSnapshotConfig,
  ParameterSnapshot,
  ParameterSnapshotScope,
  ProcessStatus,
  QualityReport,
  RecordQualityReport,
  RecordSessionState,
  TelemetryFrame,
  TelemetrySample,
} from '../types'

const maxLogEntries = 5000
export const uiFrameIntervalMs = 66
export const chartHistoryIntervalMs = 100
export const mockTelemetryIntervalMs = 33
// History buffer drives the live charts. It is intentionally a chart display
// history, not a complete capture history; recording data stays backend-owned.
const maxHistorySamples = 120
const parameterSnapshotStorageKey = 'appstation.parameterSnapshots.v1'
const cameraLabels = {
  global: '全局相机',
  wrist_left: '左腕相机',
  wrist_right: '右腕相机',
} as const
const manualAxisOrder: ManualControlAxis[] = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']
const manualAxisKeys = ['x', 'y', 'z', 'roll', 'pitch', 'yaw'] as const

function manualAxisBusyKey(side: ManualControlState['selectedSide'], axis: ManualControlAxis) {
  return `${side}-${axis}`
}

function manualAxisLockMs(
  config: AppConfig,
  side: ManualControlState['selectedSide'],
  axis: ManualControlAxis,
  step: number,
  speedMode: ManualSpeedMode,
) {
  // 前端先按运动参数估算轴占用时间，避免用户连点时堆积多条 jog 命令。
  const axisIndex = manualAxisOrder.indexOf(axis)
  const profile = side === 'left' ? config.motion.leftProfile : config.motion.rightProfile
  const group = axisIndex < 3 ? profile.translation : profile.rotation
  const velocityCap = axisIndex < 3 ? 20000 : 30
  const speedScale = speedMode === 'coarse' ? 1 : speedMode === 'medium' ? 0.5 : 0.2
  const maxVelocity = Math.max(0.001, Math.min(group.maxSpeed, velocityCap) * speedScale)
  const accTimeMs = Math.min(Math.max(group.accTimeSec, 0.001), 5) * 500
  const decTimeMs = Math.min(Math.max(group.decTimeSec, 0.001), 5) * 500
  const travelMs = (Math.abs(step) / maxVelocity) * 1000
  return Math.min(Math.max(travelMs + accTimeMs + decTimeMs + 400, 500), 30000)
}

const initialManualState: ManualControlState = {
  selectedSide: 'left',
  selectedAxis: 'X',
  axisStepUm: 100,
  axisStepDeg: 1,
  speedMode: 'fine',
  axisBusyUntil: {},
  recording: false,
  recordingStartedAt: null,
  draftActions: [],
  memories: [],
  replayingMemoryId: null,
  axisOffsets: {},
}

const initialRecordSession: RecordSessionState = {
  datasetName: 'micro_assembly_v1',
  task: 'Assemble ICF target component',
  targetEpisodes: 50,
  episodeTimeS: 30,
  resetTimeS: 10,
  currentEpisode: 0,
  savedEpisodes: 0,
  episodeHistory: [],
  latestQualityReport: null,
  phase: 'idle',
  phaseStartedAt: null,
  recorderFps: 0,
  recorderFrameCount: 0,
  recorderLateFrames: 0,
  recorderElapsedS: 0,
  recorderTotalS: -1,
  forceTareActive: false,
  speedMode: 'fine',
}

function cloneConfig<T>(value: T): T {
  return structuredClone(value)
}

function isParameterSnapshotScope(value: unknown): value is ParameterSnapshotScope {
  return value === 'all' || value === 'motion-left' || value === 'motion-right'
}

function makeMotionCardSnapshotConfig(config: AppConfig, scope: Exclude<ParameterSnapshotScope, 'all'>): MotionCardSnapshotConfig {
  const side = scope === 'motion-left' ? 'left' : 'right'
  return {
    cardNo: side === 'left' ? config.motion.leftCardNo : config.motion.rightCardNo,
    motionThreadHz: config.motion.motionThreadHz,
    yawSoftLimitDeg: config.motion.yawSoftLimitDeg,
    positionSource: config.motion.positionSource,
    profile: cloneConfig(side === 'left' ? config.motion.leftProfile : config.motion.rightProfile),
    softLimits: cloneConfig(side === 'left' ? config.motion.leftSoftLimits : config.motion.rightSoftLimits),
  }
}

function applyMotionCardSnapshotConfig(config: AppConfig, scope: Exclude<ParameterSnapshotScope, 'all'>, snapshot: MotionCardSnapshotConfig): AppConfig {
  if (scope === 'motion-left') {
    return {
      ...config,
      motion: {
        ...config.motion,
        leftCardNo: snapshot.cardNo,
        motionThreadHz: snapshot.motionThreadHz,
        yawSoftLimitDeg: snapshot.yawSoftLimitDeg,
        positionSource: snapshot.positionSource,
        leftProfile: cloneConfig(snapshot.profile),
        leftSoftLimits: cloneConfig(snapshot.softLimits),
      },
    }
  }
  return {
    ...config,
    motion: {
      ...config.motion,
      rightCardNo: snapshot.cardNo,
      motionThreadHz: snapshot.motionThreadHz,
      yawSoftLimitDeg: snapshot.yawSoftLimitDeg,
      positionSource: snapshot.positionSource,
      rightProfile: cloneConfig(snapshot.profile),
      rightSoftLimits: cloneConfig(snapshot.softLimits),
    },
  }
}

function parameterSnapshotScopeLabel(scope: ParameterSnapshotScope) {
  if (scope === 'all') return '全局硬件'
  if (scope === 'motion-left') return '左臂运动控制卡'
  return '右臂运动控制卡'
}

function readParameterSnapshots(): ParameterSnapshot[] {
  try {
    if (typeof window === 'undefined') return []
    const raw = window.localStorage.getItem(parameterSnapshotStorageKey)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is ParameterSnapshot =>
      typeof item?.id === 'string'
      && typeof item.name === 'string'
      && typeof item.createdAt === 'number'
      && isParameterSnapshotScope(item.scope)
      && typeof item.config === 'object'
      && item.config !== null,
    )
  } catch {
    return []
  }
}

function persistParameterSnapshots(snapshots: ParameterSnapshot[]) {
  try {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(parameterSnapshotStorageKey, JSON.stringify(snapshots))
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
}

interface TelemetryStore {
  tick: number
  frame: TelemetryFrame
  history: TelemetrySample[]
  logs: LogEntry[]
  diagnostics: DiagnosticItem[]
  config: AppConfig
  recording: boolean
  autoRunning: boolean
  clutchActive: boolean
  episodeCount: number
  frameCount: number
  logPanelOpen: boolean
  selectedMode: 'Record' | 'Auto' | 'Manual'
  dangerOverride: number | null
  qualityReport: QualityReport | null
  recordSession: RecordSessionState
  manualControl: ManualControlState
  parameterSnapshots: ParameterSnapshot[]
  mockTimer: number | null
  backendWs: WebSocket | null
  backendReconnectTimer: number | null
  backendReconnectAttempts: number
  startMock: () => void
  stopMock: () => void
  startBackend: () => void
  stopBackend: () => void
  setLogPanelOpen: (open: boolean) => void
  setMode: (mode: 'Record' | 'Auto' | 'Manual') => void
  startRecording: () => void
  pauseRecording: () => void
  saveEpisode: () => void
  discardEpisode: () => void
  setRecordDatasetName: (name: string) => void
  setRecordTask: (task: string) => void
  setRecordTargetEpisodes: (n: number) => void
  setRecordEpisodeTimes: (episodeS: number, resetS: number) => void
  startRecordSession: (datasetName: string, task: string) => void
  saveRecordEpisode: () => void
  discardRecordEpisode: () => void
  acceptRecordQualityReport: () => void
  rejectRecordQualityReport: () => void
  finishRecordSession: () => void
  skipRecordReset: () => void
  tareRecordForceSensors: () => void
  toggleRecordClutch: () => void
  setRecordSpeedMode: (mode: ManualSpeedMode) => void
  homeRecordArms: () => void
  triggerEmergencyStop: () => void
  clearRecordSession: () => void
  setClutchActive: (active: boolean) => void
  setAutoRunning: (running: boolean) => void
  setDangerOverride: (danger: number | null) => void
  acknowledgeSafety: () => void
  injectLog: (level: LogLevel, msg: string, channel?: LogEntry['channel']) => void
  sendBackendCommandLog: (level: LogLevel, msg: string, channel?: LogEntry['channel']) => void
  runDiagnostics: () => Promise<void>
  updateConfig: (patch: Partial<AppConfig>) => void
  saveParameterSnapshot: (scope: ParameterSnapshotScope, name: string) => void
  applyParameterSnapshot: (id: string) => void
  deleteParameterSnapshot: (id: string) => void
  selectManualAxis: (side: ManualControlState['selectedSide'], axis: ManualControlAxis) => void
  setManualAxisStep: (unit: 'um' | '°', value: number) => void
  setManualSpeedMode: (mode: ManualSpeedMode) => void
  issueManualAxisMove: (side: ManualControlState['selectedSide'], axis: ManualControlAxis, direction: -1 | 1) => void
  issueManualGripperMove: (side: ManualControlState['selectedSide'], command: ManualGripperCommand, targetMm?: number) => void
  startManualRecording: () => void
  stopManualRecording: () => void
  saveManualMemory: (name?: string) => void
  replayManualMemory: (id: number) => void
  pauseManualReplay: () => void
  deleteManualMemory: (id: number) => void
  closeQualityReport: () => void
}

const emptyFrame: TelemetryFrame = {
  timestamp: Date.now(),
  elapsedSec: 0,
  jointPositions: Array.from({ length: 12 }, () => 0),
  gripperPositions: [-1, -1],
  motionEnabled: { left: null, right: null },
  motionAxisEnabled: { left: Array.from({ length: 6 }, () => null), right: Array.from({ length: 6 }, () => null) },
  forceLeft: [0, 0, 0, 0, 0, 0],
  forceRight: [0, 0, 0, 0, 0, 0],
  dangerIndex: 0,
  recording: false,
  episodeCount: 0,
  frameCount: 0,
  halOk: true,
  wsOk: true,
  cameras: [
    { key: 'global', label: cameraLabels.global, fps: 0, timestampSkewMs: 0, frameAgeMs: 9999, health: 'pending' },
    { key: 'wrist_left', label: cameraLabels.wrist_left, fps: 0, timestampSkewMs: 0, frameAgeMs: 9999, health: 'pending' },
    { key: 'wrist_right', label: cameraLabels.wrist_right, fps: 0, timestampSkewMs: 0, frameAgeMs: 9999, health: 'pending' },
  ],
  queueDepth: { left: 20, right: 18 },
  resource: { uiFps: 60, wsHz: 30, cpuPct: 18, memMb: 386 },
  processStatus: [],
  teleopHands: [
    {
      side: 'left',
      connected: false,
      calibrated: false,
      openId: 0,
      deviceId: -1,
      serial: '',
      systemName: '',
      leftHanded: null,
      pose: [0, 0, 0, 0, 0, 0],
      clutchPressed: false,
      gripperPressed: false,
      gripperGapMm: null,
      lastReadOk: false,
      message: '',
    },
    {
      side: 'right',
      connected: false,
      calibrated: false,
      openId: 1,
      deviceId: -1,
      serial: '',
      systemName: '',
      leftHanded: null,
      pose: [0, 0, 0, 0, 0, 0],
      clutchPressed: false,
      gripperPressed: false,
      gripperGapMm: null,
      lastReadOk: false,
      message: '',
    },
  ],
}

function nextProcessStatus(t: number, autoRunning: boolean): ProcessStatus[] {
  return [
    {
      name: 'hal',
      label: 'HalServer.exe',
      status: 'running',
      pid: 4128,
      cpuPct: 2.5 + Math.sin(t * 0.6) * 0.8,
      memMb: 48,
      autoRestart: true,
    },
    {
      name: 'backend',
      label: 'FastAPI Backend',
      status: 'running',
      pid: 5204,
      cpuPct: 12 + Math.sin(t * 0.8) * 4,
      memMb: 420 + Math.sin(t * 0.3) * 18,
      autoRestart: true,
    },
    {
      name: 'policy',
      label: 'PolicyServer',
      status: autoRunning ? 'running' : 'not_running',
      pid: autoRunning ? 6810 : undefined,
      cpuPct: autoRunning ? 44 + Math.sin(t) * 10 : 0,
      memMb: autoRunning ? 1650 : 0,
      vramGb: autoRunning ? 5.2 + Math.sin(t * 0.4) * 0.3 : undefined,
    },
    {
      name: 'recorder',
      label: 'DataRecorder',
      status: 'running',
      pid: 5222,
      cpuPct: 8 + Math.cos(t * 0.5) * 2,
      memMb: 310,
    },
    {
      name: 'wsl',
      label: 'WSL2 Bridge',
      status: autoRunning ? 'running' : 'degraded',
      pid: autoRunning ? 6932 : undefined,
      cpuPct: autoRunning ? 16 : 0,
      memMb: autoRunning ? 940 : 0,
    },
  ]
}

function buildFrame(state: TelemetryStore): TelemetryFrame {
  // mock 模式生成与后端 WebSocket 同形状的数据，组件无需区分数据来源。
  const tick = state.tick + 1
  const t = tick / 50
  const jointPositions = Array.from({ length: 12 }, (_, index) => {
    const linearScale = index % 6 < 3 ? 520 : 85
    const side = index < 6 ? 'left' : 'right'
    const axis = manualAxisOrder[index % 6]
    const offset = state.manualControl.axisOffsets[`${side}-${axis}`] ?? 0
    return Math.sin(t * (0.22 + index * 0.015) + index * 0.74) * linearScale + offset
  })
  const forceLeftRaw = [
    Math.sin(t * 1.8) * 0.8,
    Math.cos(t * 1.3) * 0.5,
    1.4 + Math.sin(t * 0.9) * 0.45,
    Math.sin(t * 1.2) * 0.012,
    Math.cos(t * 1.1) * 0.01,
    Math.sin(t * 0.7) * 0.009,
  ]
  const forceRightRaw = [
    Math.cos(t * 1.1) * 0.6,
    Math.sin(t * 1.6) * 0.7,
    0.95 + Math.cos(t * 0.8) * 0.35,
    Math.cos(t * 0.9) * 0.011,
    Math.sin(t * 1.4) * 0.013,
    Math.cos(t * 0.6) * 0.007,
  ]
  const forceLeft = state.recordSession.forceTareActive
    ? [
        Math.sin(t * 0.9) * 0.035,
        Math.cos(t * 0.8) * 0.028,
        Math.sin(t * 0.7) * 0.045,
        Math.sin(t * 0.6) * 0.003,
        Math.cos(t * 0.55) * 0.003,
        Math.sin(t * 0.5) * 0.002,
      ]
    : forceLeftRaw
  const forceRight = state.recordSession.forceTareActive
    ? [
        Math.cos(t * 0.75) * 0.033,
        Math.sin(t * 0.85) * 0.03,
        Math.cos(t * 0.65) * 0.042,
        Math.cos(t * 0.5) * 0.003,
        Math.sin(t * 0.58) * 0.003,
        Math.cos(t * 0.48) * 0.002,
      ]
    : forceRightRaw
  const pulse = Math.sin(t * 0.18) > 0.92 ? 0.38 : 0
  const computedDanger = Math.min(1.16, Math.max(0, Math.sin(t * 0.31) * 0.48 + pulse))
  const dangerIndex = state.dangerOverride ?? (state.recordSession.forceTareActive ? Math.min(0.18, computedDanger) : computedDanger)
  const recordActive = state.recordSession.phase === 'recording'
  const frameCount = recordActive ? state.recordSession.recorderFrameCount : state.recording ? state.frameCount + 1 : state.frameCount
  const queueLeft = Math.round(45 + Math.sin(t * 1.1) * 22 + (state.autoRunning ? 18 : -24))
  const queueRight = Math.round(42 + Math.cos(t * 1.05) * 20 + (state.autoRunning ? 16 : -22))
  const leftTeleopConnected = state.config.teleop.leftConnected
  const rightTeleopConnected = state.config.teleop.rightConnected

  return {
    timestamp: Date.now(),
    elapsedSec: t,
    jointPositions,
    gripperPositions: [
      clamp(state.config.gripper.targetLeftMm + Math.sin(t * 0.5) * 0.25, 0, state.config.gripper.strokeMm),
      clamp(state.config.gripper.targetRightMm + Math.cos(t * 0.55) * 0.25, 0, state.config.gripper.strokeMm),
    ],
    motionEnabled: { left: false, right: false },
    motionAxisEnabled: {
      left: Array.from({ length: 6 }, () => false),
      right: Array.from({ length: 6 }, () => false),
    },
    forceLeft,
    forceRight,
    dangerIndex,
    recording: state.recording || recordActive,
    episodeCount: state.recordSession.currentEpisode || state.episodeCount,
    frameCount,
    halOk: true,
    wsOk: true,
    cameras: [
      {
        key: 'global',
        label: cameraLabels.global,
        fps: 29.8 + Math.sin(t * 0.4) * 0.2,
        timestampSkewMs: 3 + Math.sin(t) * 2,
        frameAgeMs: 26 + Math.sin(t * 1.3) * 6,
        health: 'ok',
      },
      {
        key: 'wrist_left',
        label: cameraLabels.wrist_left,
        fps: 29.6 + Math.cos(t * 0.5) * 0.25,
        timestampSkewMs: 5 + Math.cos(t * 1.2) * 3,
        frameAgeMs: 31 + Math.cos(t * 1.1) * 7,
        health: 'ok',
      },
      {
        key: 'wrist_right',
        label: cameraLabels.wrist_right,
        fps: 29.7 + Math.sin(t * 0.7) * 0.2,
        timestampSkewMs: 4 + Math.sin(t * 0.9) * 3,
        frameAgeMs: 29 + Math.sin(t * 1.5) * 5,
        health: 'ok',
      },
    ],
    teleopHands: [
      {
        side: 'left',
        connected: leftTeleopConnected,
        calibrated: false,
        openId: state.config.teleop.leftOpenId,
        deviceId: 0,
        serial: 'test-left',
        systemName: 'test Omega.7',
        leftHanded: true,
        pose: leftTeleopConnected
          ? [
              Math.sin(t * 0.6) * 0.025,
              Math.cos(t * 0.5) * 0.018,
              Math.sin(t * 0.45 + 0.8) * 0.022,
              0,
              0,
              Math.sin(t * 0.4) * 4.5,
            ]
          : [0, 0, 0, 0, 0, 0],
        clutchPressed: leftTeleopConnected && Math.sin(t * 0.8) > 0.4,
        gripperPressed: leftTeleopConnected && Math.cos(t * 0.7) > 0.45,
        gripperGapMm: null,
        lastReadOk: leftTeleopConnected,
        message: leftTeleopConnected ? 'test fixture' : 'logical teleop hand disconnected',
      },
      {
        side: 'right',
        connected: rightTeleopConnected,
        calibrated: false,
        openId: state.config.teleop.rightOpenId,
        deviceId: 1,
        serial: 'test-right',
        systemName: 'test Omega.7',
        leftHanded: false,
        pose: rightTeleopConnected
          ? [
              Math.sin(t * 0.6 + 1) * 0.025,
              Math.cos(t * 0.5 + 1) * 0.018,
              Math.sin(t * 0.45 + 1.8) * 0.022,
              0,
              0,
              Math.sin(t * 0.4 + 1) * 4.5,
            ]
          : [0, 0, 0, 0, 0, 0],
        clutchPressed: rightTeleopConnected && Math.sin(t * 0.8 + 1) > 0.4,
        gripperPressed: rightTeleopConnected && Math.cos(t * 0.7 + 1) > 0.45,
        gripperGapMm: null,
        lastReadOk: rightTeleopConnected,
        message: rightTeleopConnected ? 'test fixture' : 'logical teleop hand disconnected',
      },
    ],
    queueDepth: {
      left: Math.max(0, Math.min(100, queueLeft)),
      right: Math.max(0, Math.min(100, queueRight)),
    },
    resource: {
      uiFps: 59.4 + Math.sin(t) * 0.4,
      wsHz: 30,
      cpuPct: 22 + Math.sin(t * 0.6) * 8,
      memMb: 520 + Math.cos(t * 0.25) * 30,
    },
    processStatus: nextProcessStatus(t, state.autoRunning),
  }
}

let _logIdSeq = 0
let _manualActionIdSeq = 0
let _manualMemoryIdSeq = 0

function makeLog(level: LogLevel, msg: string, channel?: LogEntry['channel']): LogEntry {
  const id = ++_logIdSeq
  return {
    id,
    ts: Date.now(),
    channel: channel ?? logChannels[id % logChannels.length],
    level,
    msg,
  }
}

function appendLog(logs: LogEntry[], entry: LogEntry) {
  return [...logs, entry].slice(-maxLogEntries)
}

type BackendWsMessage =
  | { type: 'telemetry'; data: TelemetryFrame }
  | { type: 'log'; data: LogEntry }
  | { type: 'config'; data: AppConfig }

function telemetrySampleFromFrame(frame: TelemetryFrame): TelemetrySample {
  // 图表只保留绘制需要的字段，避免历史缓冲持有整帧对象造成渲染压力。
  return {
    time: frame.elapsedSec,
    joints: frame.jointPositions,
    forceLeft: frame.forceLeft,
    forceRight: frame.forceRight,
    danger: frame.dangerIndex,
    queueLeft: frame.queueDepth.left,
    queueRight: frame.queueDepth.right,
  }
}

function appendTelemetryHistory(history: TelemetrySample[], sample: TelemetrySample) {
  const nextHistory = history.length >= maxHistorySamples
    ? history.slice(history.length - maxHistorySamples + 1)
    : history.slice()
  nextHistory.push(sample)
  return nextHistory
}

type TelemetryStorePatch = Partial<TelemetryStore>
type TelemetryStoreSet = (partial: TelemetryStorePatch | ((state: TelemetryStore) => TelemetryStorePatch), replace?: false) => void
type TelemetryStoreGet = () => TelemetryStore

let pendingBackendFrame: TelemetryFrame | null = null
let backendFrameDelayTimer: number | null = null
let backendFrameRaf: number | null = null
let lastBackendFrameCommitAt = 0
let lastBackendHistoryCommitAt = 0

function backendFrameCommitIsUrgent(previous: TelemetryFrame, next: TelemetryFrame) {
  return previous.wsOk !== next.wsOk
    || previous.halOk !== next.halOk
    || previous.recording !== next.recording
    || previous.episodeCount !== next.episodeCount
    || previous.motionEnabled.left !== next.motionEnabled.left
    || previous.motionEnabled.right !== next.motionEnabled.right
    || previous.motionAxisEnabled.left.some((value, index) => value !== next.motionAxisEnabled.left[index])
    || previous.motionAxisEnabled.right.some((value, index) => value !== next.motionAxisEnabled.right[index])
    || (previous.dangerIndex < 1 && next.dangerIndex >= 1)
}

function nextRecordSessionFromBackend(state: TelemetryStore, frame: TelemetryFrame): RecordSessionState {
  if (state.recordSession.phase !== 'recording') return state.recordSession
  return {
    ...state.recordSession,
    recorderFrameCount: Math.max(state.recordSession.recorderFrameCount, frame.frameCount),
    recorderFps: frame.recording ? Math.max(0, frame.resource.wsHz) : state.recordSession.recorderFps,
    recorderElapsedS: state.recordSession.phaseStartedAt
      ? Math.max(0, (Date.now() - state.recordSession.phaseStartedAt) / 1000)
      : state.recordSession.recorderElapsedS,
  }
}

function commitBackendFrame(set: TelemetryStoreSet, frame: TelemetryFrame, forceHistory: boolean) {
  const now = Date.now()
  const sample = telemetrySampleFromFrame(frame)
  let historyCommitted = false
  set((state) => {
    const shouldCommitHistory =
      forceHistory
      || state.history.length === 0
      || lastBackendHistoryCommitAt === 0
      || now - lastBackendHistoryCommitAt >= chartHistoryIntervalMs
    const nextFrame = state.dangerOverride === null ? frame : { ...frame, dangerIndex: state.dangerOverride }
    historyCommitted = shouldCommitHistory
    return {
      tick: state.tick + 1,
      frame: nextFrame,
      recording: nextFrame.recording,
      episodeCount: nextFrame.episodeCount,
      frameCount: nextFrame.frameCount,
      recordSession: nextRecordSessionFromBackend(state, nextFrame),
      ...(shouldCommitHistory ? { history: appendTelemetryHistory(state.history, sample) } : {}),
    }
  })
  lastBackendFrameCommitAt = now
  if (historyCommitted) lastBackendHistoryCommitAt = now
}

function flushPendingBackendFrame(set: TelemetryStoreSet, get: TelemetryStoreGet, force = false) {
  const frame = pendingBackendFrame
  if (!frame) return
  const now = Date.now()
  if (!force && lastBackendFrameCommitAt > 0 && now - lastBackendFrameCommitAt < uiFrameIntervalMs) {
    schedulePendingBackendFrame(set, get)
    return
  }
  pendingBackendFrame = null
  commitBackendFrame(set, frame, force)
}

function schedulePendingBackendFrame(set: TelemetryStoreSet, get: TelemetryStoreGet) {
  if (backendFrameDelayTimer !== null || backendFrameRaf !== null) return
  const elapsed = lastBackendFrameCommitAt > 0 ? Date.now() - lastBackendFrameCommitAt : uiFrameIntervalMs
  const delayMs = Math.max(0, uiFrameIntervalMs - elapsed)
  backendFrameDelayTimer = window.setTimeout(() => {
    backendFrameDelayTimer = null
    const flush = () => {
      backendFrameRaf = null
      flushPendingBackendFrame(set, get)
    }
    if (typeof window.requestAnimationFrame === 'function') {
      backendFrameRaf = window.requestAnimationFrame(flush)
      return
    }
    flush()
  }, delayMs)
}

function enqueueBackendFrame(set: TelemetryStoreSet, get: TelemetryStoreGet, frame: TelemetryFrame) {
  const urgent = backendFrameCommitIsUrgent(get().frame, frame)
  pendingBackendFrame = frame
  if (urgent) {
    clearPendingBackendFrameFlush()
    pendingBackendFrame = frame
    flushPendingBackendFrame(set, get, true)
    return
  }
  schedulePendingBackendFrame(set, get)
}

function clearPendingBackendFrameFlush() {
  if (backendFrameDelayTimer !== null) {
    window.clearTimeout(backendFrameDelayTimer)
    backendFrameDelayTimer = null
  }
  if (backendFrameRaf !== null && typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(backendFrameRaf)
  }
  backendFrameRaf = null
  pendingBackendFrame = null
  lastBackendFrameCommitAt = 0
  lastBackendHistoryCommitAt = 0
}

function mergeConfig(current: AppConfig, patch: Partial<AppConfig>): AppConfig {
  // 设置页按分组提交 patch；深合并可以保留未编辑分组里的现有参数。
  return {
    ...current,
    ...patch,
    hal: { ...current.hal, ...patch.hal },
    cameras: { ...current.cameras, ...patch.cameras },
    force: { ...current.force, ...patch.force },
    motion: { ...current.motion, ...patch.motion },
    gripper: { ...current.gripper, ...patch.gripper },
    safety: { ...current.safety, ...patch.safety },
    zmq: { ...current.zmq, ...patch.zmq },
    storage: { ...current.storage, ...patch.storage },
    picoVision: { ...current.picoVision, ...patch.picoVision },
    teleop: { ...current.teleop, ...patch.teleop },
    wsl: { ...current.wsl, ...patch.wsl },
  }
}

function diagnosticsFromHardwareStatus(
  diagnostics: DiagnosticItem[],
  status: Awaited<ReturnType<typeof fetchHardwareStatus>>,
): DiagnosticItem[] {
  return diagnostics.map((item) => {
    if (item.key.startsWith('cam-') && status.camera) {
      return { ...item, status: status.camera.ok ? 'ok' : 'error', remediation: status.camera.message }
    }
    if (item.key.startsWith('ati-') && status.force) {
      return { ...item, status: status.force.ok ? 'ok' : 'error', remediation: status.force.message }
    }
    if (item.key === 'gripper' && status.gripper) {
      return { ...item, status: status.gripper.ok ? 'ok' : 'error', remediation: status.gripper.message }
    }
    if (item.key === 'omega7') {
      return { ...item, status: 'pending', remediation: 'Omega.7 状态由 C++ HalServer /health.omega7_ok 提供' }
    }
    return item
  })
}

function appendManualAction(manual: ManualControlState, action: ManualControlAction): ManualControlState {
  if (!manual.recording) return manual
  return {
    ...manual,
    draftActions: [...manual.draftActions, action],
  }
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function speedModeText(mode: ManualSpeedMode) {
  if (mode === 'coarse') return '粗调'
  if (mode === 'medium') return '中速'
  return '精调'
}

function makeQualityReport(episode: number, frames: number): QualityReport {
  return {
    episode,
    durationSec: Math.max(1, frames / 30),
    frames,
    task: 'Assemble ICF target component',
    maxSkewMs: 8.3,
    jitterMs: 1.2,
    maxForceLeft: 3.2,
    maxForceRight: 5.8,
    warnings: ['左腕相机模拟掉 1 帧，需要在真实采集时检查 USB 带宽', '右臂 Fx 接近警告阈值，建议复核接触力控制'],
  }
}

function maxAbs(values: number[]) {
  return values.reduce((max, value) => Math.max(max, Math.abs(value)), 0)
}

function cameraHealthDrops(frame: TelemetryFrame) {
  return {
    global: frame.cameras.find((camera) => camera.key === 'global')?.health === 'ok' ? 0 : 1,
    wristLeft: frame.cameras.find((camera) => camera.key === 'wrist_left')?.health === 'ok' ? 0 : 1,
    wristRight: frame.cameras.find((camera) => camera.key === 'wrist_right')?.health === 'ok' ? 0 : 1,
  }
}

function makeRecordQualityReport(state: TelemetryStore): RecordQualityReport {
  const session = state.recordSession
  const frame = state.frame
  const frameCount = Math.max(1, session.recorderFrameCount)
  const durationS = Math.max(1, session.recorderElapsedS || frameCount / 30)
  const cameraDrops = cameraHealthDrops(frame)
  const maxForceLeft = maxAbs(frame.forceLeft)
  const maxForceRight = maxAbs(frame.forceRight)
  const warnings: string[] = []
  if (session.recorderLateFrames > 0) warnings.push(`检测到 ${session.recorderLateFrames} 个迟帧，请复核采集链路`)
  if (Object.values(cameraDrops).some((drops) => drops > 0)) warnings.push('存在相机健康状态异常，请检查预览链路')
  if (maxForceLeft > 4 || maxForceRight > 4) warnings.push('力觉峰值接近安全阈值，请复核操作过程')

  return {
    index: session.currentEpisode,
    frameCount,
    durationS,
    status: 'ok',
    maxForceLeft,
    maxForceRight,
    lateFrames: session.recorderLateFrames,
    cameraDrops,
    warnings,
    passed: warnings.length === 0,
  }
}

function makeDiscardedEpisodeRecord(session: RecordSessionState): EpisodeRecord {
  return {
    index: session.currentEpisode,
    frameCount: Math.max(0, session.recorderFrameCount),
    durationS: Math.max(0, session.recorderElapsedS),
    status: 'discarded',
    maxForceLeft: 0,
    maxForceRight: 0,
    lateFrames: session.recorderLateFrames,
    cameraDrops: { global: 0, wristLeft: 0, wristRight: 0 },
  }
}

function advanceRecordSession(session: RecordSessionState): RecordSessionState {
  if (session.phase === 'idle') return session

  const now = Date.now()
  const phaseStartedAt = session.phaseStartedAt ?? now
  const elapsedS = Math.max(0, (now - phaseStartedAt) / 1000)
  const totalS =
    session.phase === 'recording'
      ? session.episodeTimeS
      : session.phase === 'resetting'
        ? session.resetTimeS
        : -1
  const recorderFrameCount =
    session.phase === 'recording'
      ? Math.max(session.recorderFrameCount, Math.floor(elapsedS * 30))
      : session.recorderFrameCount
  const recorderFps =
    session.phase === 'recording'
      ? 29.7 + Math.sin(now / 850) * 0.25
      : session.phase === 'saving' || session.phase === 'finishing'
        ? session.recorderFps
        : 0
  const recorderLateFrames =
    session.phase === 'recording' && elapsedS > 12 && Math.floor(elapsedS) % 17 === 0
      ? Math.max(session.recorderLateFrames, 1)
      : session.recorderLateFrames

  return {
    ...session,
    phaseStartedAt,
    recorderElapsedS: elapsedS,
    recorderTotalS: totalS,
    recorderFrameCount,
    recorderFps,
    recorderLateFrames,
  }
}

export const useTelemetryStore = create<TelemetryStore>((set, get) => ({
  tick: 0,
  frame: emptyFrame,
  history: [],
  logs: [makeLog('INFO', mockMode ? 'M0 test telemetry fixture started at 30Hz' : 'Backend telemetry initializing', '[BACKEND]')],
  diagnostics: defaultDiagnostics,
  config: defaultConfig,
  recording: false,
  autoRunning: false,
  clutchActive: false,
  episodeCount: 42,
  frameCount: 0,
  logPanelOpen: false,
  selectedMode: 'Record',
  dangerOverride: null,
  qualityReport: null,
  recordSession: initialRecordSession,
  manualControl: initialManualState,
  parameterSnapshots: readParameterSnapshots(),
  mockTimer: null,
  backendWs: null,
  backendReconnectTimer: null,
  backendReconnectAttempts: 0,

  startMock: () => {
    if (get().mockTimer) return
    const timer = window.setInterval(() => {
      set((state) => {
        const recordSession = advanceRecordSession(state.recordSession)
        const frame = buildFrame({ ...state, recordSession })
        const tick = state.tick + 1
        const sample: TelemetrySample = {
          time: frame.elapsedSec,
          joints: frame.jointPositions,
          forceLeft: frame.forceLeft,
          forceRight: frame.forceRight,
          danger: frame.dangerIndex,
          queueLeft: frame.queueDepth.left,
          queueRight: frame.queueDepth.right,
        }
        let logs = state.logs
        if (tick % 25 === 0) {
          logs = appendLog(
            logs,
            makeLog('INFO', `Frame ${frame.frameCount} OK, ws=${frame.resource.wsHz}Hz`, '[BACKEND]'),
          )
        }
        if (frame.dangerIndex > 0.82 && tick % 20 === 0) {
          logs = appendLog(logs, makeLog('WARNING', `danger_index=${frame.dangerIndex.toFixed(2)}`, '[SAFETY]'))
        }
        return {
          tick,
          frame,
          recordSession,
          frameCount: frame.frameCount,
          history: state.history.length === 0 || tick % Math.max(1, Math.round(chartHistoryIntervalMs / mockTelemetryIntervalMs)) === 0
            ? appendTelemetryHistory(state.history, sample)
            : state.history,
          logs,
        }
      })
    }, mockTelemetryIntervalMs)
    set({ mockTimer: timer })
  },

  stopMock: () => {
    const timer = get().mockTimer
    if (timer) window.clearInterval(timer)
    set({ mockTimer: null })
  },

  startBackend: () => {
    if (get().backendWs) return
    clearPendingBackendFrameFlush()
    const reconnectTimer = get().backendReconnectTimer
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer)
      set({ backendReconnectTimer: null })
    }
    // WebSocket 建立前先拉一次静态配置和硬件概况，首屏不会等到下一帧遥测才有数据。
    void fetchConfig()
      .then((config) => set({ config }))
      .catch((error) => {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('ERROR', `settings fetch failed: ${String(error)}`, '[BACKEND]')),
        }))
      })
    void fetchParameterSnapshots()
      .then((snapshots) => set({ parameterSnapshots: snapshots }))
      .catch((error) => {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('WARNING', `snapshots fetch failed: ${String(error)}`, '[BACKEND]')),
        }))
      })
    void fetchHardwareStatus()
      .then((status) => {
        set((state) => ({
          diagnostics: diagnosticsFromHardwareStatus(state.diagnostics, status),
          logs: appendLog(state.logs, makeLog('INFO', 'Real hardware status probe completed', '[BACKEND]')),
        }))
      })
      .catch((error) => {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('ERROR', `hardware status fetch failed: ${String(error)}`, '[BACKEND]')),
        }))
      })
    const ws = new WebSocket(wsUrl)
    ws.onopen = () => {
      set((state) => ({
        backendReconnectAttempts: 0,
        logs: appendLog(state.logs, makeLog('INFO', `Backend WebSocket connected: ${wsUrl}`, '[BACKEND]')),
      }))
    }
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data)) as BackendWsMessage
        if (message.type === 'telemetry') {
          const frame = {
            ...message.data,
            motionEnabled: message.data.motionEnabled ?? { left: null, right: null },
            motionAxisEnabled: message.data.motionAxisEnabled ?? {
              left: Array.from({ length: 6 }, () => null),
              right: Array.from({ length: 6 }, () => null),
            },
          }
          enqueueBackendFrame(set, get, frame)
          // Coalesce raw WS frames so React and ECharts update on the UI cadence.
          return
        }
        if (message.type === 'log') {
          set((state) => ({ logs: appendLog(state.logs, message.data) }))
          return
        }
        if (message.type === 'config') {
          set({ config: message.data })
        }
      } catch (error) {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('WARNING', `backend ws message ignored: ${String(error)}`, '[BACKEND]')),
        }))
      }
    }
    ws.onerror = () => {
      set((state) => ({
        frame: { ...state.frame, wsOk: false },
        logs: appendLog(state.logs, makeLog('ERROR', 'Backend WebSocket error', '[BACKEND]')),
      }))
    }
    ws.onclose = (event) => {
      if (get().backendWs !== ws) {
        return
      }
      const attempts = get().backendReconnectAttempts + 1
      // 指数退避重连，避免后端重启时前端持续打满连接请求。
      const delayMs = Math.min(1000 * 2 ** Math.min(attempts - 1, 4), 15000)
      const reconnectTimer = window.setTimeout(() => {
        set({ backendReconnectTimer: null })
        get().startBackend()
      }, delayMs)
      set((state) => ({
        backendWs: null,
        backendReconnectTimer: reconnectTimer,
        backendReconnectAttempts: attempts,
        frame: { ...state.frame, wsOk: false },
        logs: appendLog(
          state.logs,
          makeLog(
            'WARNING',
            `Backend WebSocket closed; reconnecting in ${(delayMs / 1000).toFixed(0)}s (code=${event.code})`,
            '[BACKEND]',
          ),
        ),
      }))
    }
    set({ backendWs: ws })
  },

  stopBackend: () => {
    const ws = get().backendWs
    const timer = get().backendReconnectTimer
    if (timer) window.clearTimeout(timer)
    clearPendingBackendFrameFlush()
    set({ backendWs: null, backendReconnectTimer: null, backendReconnectAttempts: 0 })
    if (ws) ws.close()
  },

  setLogPanelOpen: (open) => set({ logPanelOpen: open }),
  setMode: (mode) => set({ selectedMode: mode }),

  startRecording: () =>
    set((state) => ({
      recording: true,
      logs: appendLog(state.logs, makeLog('INFO', 'Record session started', '[LEROBOT]')),
    })),

  pauseRecording: () =>
    set((state) => ({
      recording: false,
      logs: appendLog(state.logs, makeLog('INFO', 'Record session paused', '[LEROBOT]')),
    })),

  saveEpisode: () =>
    set((state) => {
      const nextEpisode = state.episodeCount + 1
      const report = makeQualityReport(state.episodeCount, Math.max(state.frameCount, 1))
      return {
        recording: false,
        episodeCount: nextEpisode,
        frameCount: 0,
        qualityReport: report,
        logs: appendLog(state.logs, makeLog('INFO', `Episode #${state.episodeCount} saved`, '[LEROBOT]')),
      }
    }),

  discardEpisode: () =>
    set((state) => ({
      recording: false,
      frameCount: 0,
      logs: appendLog(state.logs, makeLog('WARNING', 'Current episode discarded', '[LEROBOT]')),
    })),

  setRecordDatasetName: (name) =>
    set((state) => ({
      recordSession: {
        ...state.recordSession,
        datasetName: name,
      },
    })),

  setRecordTask: (task) =>
    set((state) => ({
      recordSession: {
        ...state.recordSession,
        task,
      },
    })),

  setRecordTargetEpisodes: (n) =>
    set((state) => ({
      recordSession: {
        ...state.recordSession,
        targetEpisodes: Math.max(1, Math.round(n)),
      },
    })),

  setRecordEpisodeTimes: (episodeS, resetS) =>
    set((state) => ({
      recordSession: {
        ...state.recordSession,
        episodeTimeS: Math.max(1, episodeS),
        resetTimeS: Math.max(0, resetS),
      },
    })),

  startRecordSession: (datasetName, task) => {
    void createRecordSessionApi(datasetName.trim() || initialRecordSession.datasetName, task)
      .catch((error) => {
        set((state) => ({
          recording: false,
          recordSession: {
            ...state.recordSession,
            phase: 'idle',
            phaseStartedAt: null,
            recorderFps: 0,
            recorderFrameCount: 0,
            recorderLateFrames: 0,
            recorderElapsedS: 0,
            recorderTotalS: -1,
          },
          logs: appendLog(state.logs, makeLog('ERROR', `record session start failed: ${String(error)}`, '[LEROBOT]')),
        }))
      })
    set((state) => {
      const now = Date.now()
      const nextSession = {
        ...state.recordSession,
        datasetName: datasetName.trim() || initialRecordSession.datasetName,
        task,
        latestQualityReport: null,
        phase: 'recording' as const,
        phaseStartedAt: now,
        recorderFps: 30,
        recorderFrameCount: 0,
        recorderLateFrames: 0,
        recorderElapsedS: 0,
        recorderTotalS: state.recordSession.episodeTimeS,
      }
      return {
        recording: true,
        frameCount: 0,
        recordSession: nextSession,
        logs: appendLog(state.logs, makeLog('INFO', `录制采集会话已开始：${nextSession.datasetName}`, '[LEROBOT]')),
      }
    })
  },

  saveRecordEpisode: () => {
    void saveRecordEpisodeApi().catch((error) => {
      set((state) => ({
        logs: appendLog(state.logs, makeLog('ERROR', `record episode save failed: ${String(error)}`, '[LEROBOT]')),
      }))
    })
    set((state) => {
      if (state.recordSession.phase !== 'recording') return state
      const report = makeRecordQualityReport(state)
      const nextSavedEpisodes = state.recordSession.savedEpisodes + 1
      const nextCurrentEpisode = report.index + 1
      return {
        recording: false,
        recordSession: {
          ...state.recordSession,
          phase: 'saving',
          phaseStartedAt: Date.now(),
          recorderElapsedS: 0,
          recorderTotalS: -1,
          savedEpisodes: nextSavedEpisodes,
          currentEpisode: nextCurrentEpisode,
          latestQualityReport: report,
          episodeHistory: [report, ...state.recordSession.episodeHistory].slice(0, 20),
        },
        logs: appendLog(state.logs, makeLog('INFO', `Episode #${report.index} 已保存，等待质量确认`, '[LEROBOT]')),
      }
    })
  },

  discardRecordEpisode: () => {
    void discardRecordEpisodeApi().catch((error) => {
      set((state) => ({
        logs: appendLog(state.logs, makeLog('ERROR', `record episode discard failed: ${String(error)}`, '[LEROBOT]')),
      }))
    })
    set((state) => {
      const record = makeDiscardedEpisodeRecord(state.recordSession)
      return {
        recording: true,
        recordSession: {
          ...state.recordSession,
          phase: 'recording',
          phaseStartedAt: Date.now(),
          recorderFps: 30,
          recorderFrameCount: 0,
          recorderLateFrames: 0,
          recorderElapsedS: 0,
          recorderTotalS: state.recordSession.episodeTimeS,
          latestQualityReport: null,
          episodeHistory: [record, ...state.recordSession.episodeHistory].slice(0, 20),
        },
        logs: appendLog(state.logs, makeLog('WARNING', `Episode #${record.index} 已丢弃，重新录制`, '[LEROBOT]')),
      }
    })
  },

  acceptRecordQualityReport: () =>
    set((state) => ({
      recordSession: {
        ...state.recordSession,
        latestQualityReport: null,
        phase: 'resetting',
        phaseStartedAt: Date.now(),
        recorderFps: 0,
        recorderElapsedS: 0,
        recorderTotalS: state.recordSession.resetTimeS,
      },
      logs: appendLog(state.logs, makeLog('INFO', '质量报告已接受，进入复位等待', '[LEROBOT]')),
    })),

  rejectRecordQualityReport: () => {
    void discardRecordEpisodeApi().catch((error) => {
      set((state) => ({
        logs: appendLog(state.logs, makeLog('ERROR', `record episode rerecord failed: ${String(error)}`, '[LEROBOT]')),
      }))
    })
    set((state) => {
      const report = state.recordSession.latestQualityReport
      if (!report) return state
      const savedEpisodes = Math.max(0, state.recordSession.savedEpisodes - 1)
      return {
        recording: true,
        recordSession: {
          ...state.recordSession,
          currentEpisode: report.index,
          savedEpisodes,
          latestQualityReport: null,
          phase: 'recording',
          phaseStartedAt: Date.now(),
          recorderFps: 30,
          recorderFrameCount: 0,
          recorderLateFrames: 0,
          recorderElapsedS: 0,
          recorderTotalS: state.recordSession.episodeTimeS,
          episodeHistory: state.recordSession.episodeHistory.filter((item) => item.index !== report.index),
        },
        logs: appendLog(state.logs, makeLog('WARNING', `Episode #${report.index} 已退回重录`, '[LEROBOT]')),
      }
    })
  },

  finishRecordSession: () => {
    void finishRecordSessionApi().finally(() => {
      set((state) => ({
        recording: false,
        recordSession: {
          ...state.recordSession,
          phase: 'idle',
          phaseStartedAt: null,
          recorderFps: 0,
          recorderFrameCount: 0,
          recorderLateFrames: 0,
          recorderElapsedS: 0,
          recorderTotalS: -1,
          latestQualityReport: null,
        },
        logs: appendLog(state.logs, makeLog('INFO', '录制采集会话已结束并 finalize()', '[LEROBOT]')),
      }))
    })
    set((state) => ({
      recording: false,
      recordSession: {
        ...state.recordSession,
        phase: 'finishing',
        phaseStartedAt: Date.now(),
        recorderElapsedS: 0,
        recorderTotalS: -1,
      },
    }))
  },

  skipRecordReset: () => {
    void skipRecordResetApi().catch((error) => {
      set((state) => ({
        logs: appendLog(state.logs, makeLog('ERROR', `record reset skip failed: ${String(error)}`, '[LEROBOT]')),
      }))
    })
    set((state) => ({
      recording: true,
      recordSession: {
        ...state.recordSession,
        phase: 'recording',
        phaseStartedAt: Date.now(),
        recorderFps: 30,
        recorderFrameCount: 0,
        recorderLateFrames: 0,
        recorderElapsedS: 0,
        recorderTotalS: state.recordSession.episodeTimeS,
      },
      logs: appendLog(state.logs, makeLog('INFO', '已跳过复位，开始下一条录制', '[LEROBOT]')),
    }))
  },

  tareRecordForceSensors: () => {
    void tareForceSensorsApi()
    set((state) => ({
      dangerOverride: 0,
      frame: {
        ...state.frame,
        dangerIndex: 0,
        forceLeft: [0, 0, 0, 0, 0, 0],
        forceRight: [0, 0, 0, 0, 0, 0],
      },
      recordSession: {
        ...state.recordSession,
        forceTareActive: true,
      },
      logs: appendLog(state.logs, makeLog('INFO', '力觉 Tare 已执行', '[FORCE]')),
    }))
  },

  toggleRecordClutch: () => {
    void toggleClutchApi()
    set((state) => ({
      clutchActive: !state.clutchActive,
      logs: appendLog(state.logs, makeLog('INFO', `离合器${state.clutchActive ? '释放' : '切换'}`, '[HAL]')),
    }))
  },

  setRecordSpeedMode: (mode) => {
    void setRecordSpeedModeApi(mode)
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        speedMode: mode,
      },
      recordSession: {
        ...state.recordSession,
        speedMode: mode,
      },
      logs: appendLog(state.logs, makeLog('INFO', `遥操作速度切换为 ${speedModeText(mode)}`, '[HAL]')),
    }))
  },

  homeRecordArms: () => {
    void homeAllApi()
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        axisOffsets: {},
      },
      logs: appendLog(state.logs, makeLog('INFO', '双臂已回工作原点', '[HAL]')),
    }))
  },

  triggerEmergencyStop: () => {
    void emergencyStopApi()
    set((state) => ({
      recording: false,
      autoRunning: false,
      dangerOverride: 1.1,
      logPanelOpen: true,
      frame: {
        ...state.frame,
        dangerIndex: 1.1,
        recording: false,
      },
      recordSession: {
        ...state.recordSession,
        phase: 'idle',
        phaseStartedAt: null,
        recorderFps: 0,
        recorderElapsedS: 0,
        recorderTotalS: -1,
      },
      logs: appendLog(state.logs, makeLog('ERROR', '操作员触发硬件急停', '[SAFETY]')),
    }))
  },

  clearRecordSession: () =>
    set((state) => ({
      recordSession: {
        ...initialRecordSession,
        datasetName: state.recordSession.datasetName,
        task: state.recordSession.task,
        targetEpisodes: state.recordSession.targetEpisodes,
        episodeTimeS: state.recordSession.episodeTimeS,
        resetTimeS: state.recordSession.resetTimeS,
      },
      logs: appendLog(state.logs, makeLog('INFO', '录制采集会话状态已清空', '[LEROBOT]')),
    })),

  setClutchActive: (active) =>
    set((state) => ({
      clutchActive: active,
      logs: appendLog(state.logs, makeLog(active ? 'INFO' : 'DEBUG', `Clutch ${active ? 'enabled' : 'released'}`, '[HAL]')),
    })),

  setAutoRunning: (running) => {
    if (!mockMode) {
      void (running ? startAutoExecution() : stopAutoExecution()).catch((error) => {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('ERROR', `auto command failed: ${String(error)}`, '[POLICY]')),
        }))
      })
    }
    set((state) => ({
      autoRunning: running,
      logs: appendLog(state.logs, makeLog(running ? 'INFO' : 'WARNING', running ? 'Auto policy loop started' : 'Auto policy loop stopped', '[POLICY]')),
    }))
  },

  setDangerOverride: (danger) =>
    set((state) => ({
      dangerOverride: danger,
      frame: {
        ...state.frame,
        dangerIndex: danger ?? state.frame.dangerIndex,
      },
    })),

  acknowledgeSafety: () => {
    void acknowledgeSafetyApi().catch((error) => {
      set((state) => ({
        logs: appendLog(state.logs, makeLog('ERROR', `safety acknowledge failed: ${String(error)}`, '[SAFETY]')),
      }))
    })
    set((state) => ({
      dangerOverride: null,
      frame: {
        ...state.frame,
        dangerIndex: 0,
      },
      logs: appendLog(state.logs, makeLog('INFO', '操作员确认安全复位', '[SAFETY]')),
    }))
  },

  injectLog: (level, msg, channel) =>
    set((state) => ({
      logs: appendLog(state.logs, makeLog(level, msg, channel)),
    })),

  sendBackendCommandLog: (level, msg, channel) => {
    if (!mockMode) {
      void sendSettingsLogCommand(channel ?? '[BACKEND]', msg, level).catch((error) => {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('ERROR', `settings log command failed: ${String(error)}`, '[BACKEND]')),
        }))
      })
    }
    set((state) => ({
      logs: appendLog(state.logs, makeLog(level, `${msg}${mockMode ? ' · test fixture' : ' · backend command'}`, channel)),
    }))
  },

  runDiagnostics: async () => {
    set((state) => ({
      diagnostics: state.diagnostics.map((item) => ({ ...item, status: item.status === 'pending' ? 'checking' : item.status })),
    }))
    await new Promise((resolve) => window.setTimeout(resolve, 600))
    set((state) => ({
      diagnostics: state.diagnostics.map((item) => {
        if (!item.suspect) return { ...item, status: 'ok' }
        if (item.key.includes('ati')) return { ...item, status: 'warn' }
        return { ...item, status: 'pending' }
      }),
      logs: appendLog(state.logs, makeLog('INFO', 'Diagnostics completed with backend hardware markers', '[BACKEND]')),
    }))
  },

  updateConfig: (patch) => {
    const nextConfig = mergeConfig(get().config, patch)
    set({ config: nextConfig })
    if (!mockMode) {
      void putConfig(nextConfig).catch((error) => {
        set((state) => ({
          logs: appendLog(state.logs, makeLog('ERROR', `settings save failed: ${String(error)}`, '[BACKEND]')),
        }))
      })
    }
  },

  saveParameterSnapshot: (scope, name) => {
    if (!mockMode) {
      const config = scope === 'all' ? cloneConfig(get().config) : makeMotionCardSnapshotConfig(get().config, scope)
      void createParameterSnapshotApi(scope, name, config)
        .then((response) => {
          const data = response.data as { snapshots?: ParameterSnapshot[] } | undefined
          if (data?.snapshots) set({ parameterSnapshots: data.snapshots })
        })
        .catch((error) => {
          set((state) => ({
            logs: appendLog(state.logs, makeLog('ERROR', `snapshot save failed: ${String(error)}`, '[BACKEND]')),
          }))
        })
    }
    set((state) => {
      const snapshot: ParameterSnapshot = scope === 'all'
        ? {
            id: `${scope}-${Date.now()}`,
            name,
            createdAt: Date.now(),
            scope,
            config: cloneConfig(state.config),
          }
        : {
            id: `${scope}-${Date.now()}`,
            name,
            createdAt: Date.now(),
            scope,
            config: makeMotionCardSnapshotConfig(state.config, scope),
          }
      const snapshots = [snapshot, ...state.parameterSnapshots].slice(0, 40)
      persistParameterSnapshots(snapshots)
      return {
        parameterSnapshots: snapshots,
        logs: appendLog(state.logs, makeLog('INFO', `${parameterSnapshotScopeLabel(scope)}快照已保存：${name}`, '[BACKEND]')),
      }
    })
  },

  applyParameterSnapshot: (id) => {
    if (!mockMode) {
      void applyParameterSnapshotApi(id)
        .then((response) => {
          const data = response.data as { config?: AppConfig; snapshots?: ParameterSnapshot[] } | undefined
          const patch: Partial<TelemetryStore> = {}
          if (data?.config) patch.config = data.config
          if (data?.snapshots) patch.parameterSnapshots = data.snapshots
          if (Object.keys(patch).length > 0) set(patch)
        })
        .catch((error) => {
          set((state) => ({
            logs: appendLog(state.logs, makeLog('ERROR', `snapshot apply failed: ${String(error)}`, '[BACKEND]')),
          }))
        })
    }
    set((state) => {
      const snapshot = state.parameterSnapshots.find((item) => item.id === id)
      if (!snapshot) return state
      const config = snapshot.scope === 'all'
        ? cloneConfig(snapshot.config)
        : applyMotionCardSnapshotConfig(state.config, snapshot.scope, snapshot.config)
      return {
        config,
        logs: appendLog(state.logs, makeLog('INFO', `${parameterSnapshotScopeLabel(snapshot.scope)}快照已应用：${snapshot.name}`, '[BACKEND]')),
      }
    })
  },

  deleteParameterSnapshot: (id) => {
    if (!mockMode) {
      void deleteParameterSnapshotApi(id)
        .then((response) => {
          const data = response.data as { snapshots?: ParameterSnapshot[] } | undefined
          if (data?.snapshots) set({ parameterSnapshots: data.snapshots })
        })
        .catch((error) => {
          set((state) => ({
            logs: appendLog(state.logs, makeLog('ERROR', `snapshot delete failed: ${String(error)}`, '[BACKEND]')),
          }))
        })
    }
    set((state) => {
      const snapshots = state.parameterSnapshots.filter((item) => item.id !== id)
      persistParameterSnapshots(snapshots)
      return {
        parameterSnapshots: snapshots,
        logs: appendLog(state.logs, makeLog('INFO', '参数快照已删除', '[BACKEND]')),
      }
    })
  },

  selectManualAxis: (side, axis) =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        selectedSide: side,
        selectedAxis: axis,
      },
    })),

  setManualAxisStep: (unit, value) =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        [unit === 'um' ? 'axisStepUm' : 'axisStepDeg']: Math.max(0, value),
      },
    })),

  setManualSpeedMode: (mode) =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        speedMode: mode,
      },
    })),

  issueManualAxisMove: (side, axis, direction) => {
    if (!mockMode) {
      const state = get()
      const axisIndex = manualAxisOrder.indexOf(axis)
      if (state.frame.motionAxisEnabled?.[side]?.[axisIndex] === false) {
        set((current) => ({
          logs: appendLog(current.logs, makeLog('WARNING', `${side} ${axis} jog skipped: motion axis is disabled`, '[HAL]')),
        }))
        return
      }
      const step = axisIndex < 3 ? state.manualControl.axisStepUm : state.manualControl.axisStepDeg
      const speedMode = state.manualControl.speedMode
      const busyKey = manualAxisBusyKey(side, axis)
      const now = Date.now()
      const busyUntil = state.manualControl.axisBusyUntil[busyKey] ?? 0
      if (busyUntil > now) {
        // 前端本地节流只影响重复点击，真实安全边界仍由后端和 HAL 执行。
        const remainingS = ((busyUntil - now) / 1000).toFixed(1)
        set((current) => ({
          logs: appendLog(
            current.logs,
            makeLog('WARNING', `${side} ${axis} jog skipped: axis is still moving (${remainingS}s)`, '[HAL]'),
          ),
        }))
        return
      }
      const lockUntil = now + manualAxisLockMs(state.config, side, axis, step, speedMode)
      // 先标记轴忙，避免网络往返期间再次发出同轴命令。
      set((current) => ({
        manualControl: {
          ...current.manualControl,
          selectedSide: side,
          selectedAxis: axis,
          axisBusyUntil: {
            ...current.manualControl.axisBusyUntil,
            [busyKey]: lockUntil,
          },
        },
      }))
      void manualAxisMoveApi(side, axis, direction, step, speedMode)
        .then(() => {
          set((current) => ({
            logs: appendLog(current.logs, makeLog('INFO', `${side} ${axis} jog command accepted by HAL`, '[HAL]')),
          }))
        })
        .catch((error) => {
          set((current) => ({
            manualControl: {
              ...current.manualControl,
              axisBusyUntil: {
                ...current.manualControl.axisBusyUntil,
                [busyKey]: Date.now() + 1000,
              },
            },
            logs: appendLog(current.logs, makeLog('ERROR', `manual axis command failed: ${String(error)}`, '[HAL]')),
          }))
        })
      return
    }
    set((state) => {
      const axisIndex = manualAxisOrder.indexOf(axis)
      if (axisIndex < 0) return state
      const axisKey = manualAxisKeys[axisIndex]
      const unit = axisIndex < 3 ? 'um' : '°'
      const stateIndex = (side === 'left' ? 0 : 6) + axisIndex
      const current = state.frame.jointPositions[stateIndex] ?? 0
      const rawDelta = unit === 'um' ? state.manualControl.axisStepUm : state.manualControl.axisStepDeg
      const limits = side === 'left' ? state.config.motion.leftSoftLimits[axisKey] : state.config.motion.rightSoftLimits[axisKey]
      // mock 模式沿用软限位，保证手动页演示行为接近真机路径。
      const nextPosition = clamp(current + rawDelta * direction, limits.min, limits.max)
      const appliedDelta = nextPosition - current
      const offsetKey = `${side}-${axis}`
      const nextManual = {
        ...state.manualControl,
        selectedSide: side,
        selectedAxis: axis,
        axisOffsets: {
          ...state.manualControl.axisOffsets,
          [offsetKey]: (state.manualControl.axisOffsets[offsetKey] ?? 0) + appliedDelta,
        },
      }
      const action: ManualControlAction = {
        id: ++_manualActionIdSeq,
        type: 'arm-axis',
        ts: Date.now(),
        side,
        axis,
        delta: appliedDelta,
        unit,
        speedMode: state.manualControl.speedMode,
        positionAfter: nextPosition,
      }
      const jointPositions = state.frame.jointPositions.map((value, index) => (index === stateIndex ? nextPosition : value))
      return {
        manualControl: appendManualAction(nextManual, action),
        frame: { ...state.frame, jointPositions },
        logs: appendLog(
          state.logs,
          makeLog(
            'INFO',
            `${side === 'left' ? '左臂' : '右臂'} ${axis} ${appliedDelta >= 0 ? '+' : ''}${appliedDelta.toFixed(unit === 'um' ? 1 : 3)}${unit} · ${speedModeText(state.manualControl.speedMode)} · test fixture`,
            '[HAL]',
          ),
        ),
      }
    })
  },

  issueManualGripperMove: (side, command, targetMm) => {
    if (!mockMode) {
      const enabledKey = side === 'left' ? 'leftEnabled' : 'rightEnabled'
      if (!['enable', 'disable', 'stop'].includes(command) && !get().config.gripper[enabledKey]) {
        set((current) => ({
          logs: appendLog(current.logs, makeLog('WARNING', `${side} gripper ${command} skipped: gripper is disabled`, '[GRIPPER]')),
        }))
        return
      }
      const previousEnabled = Boolean(get().config.gripper[enabledKey])
      // 夹爪启停先乐观更新 UI，再由后端持久化和回读校准最终状态。
      // Optimistically reflect enable/disable in the UI so the user can keep
      // clicking instead of waiting for a config refetch round-trip. The
      // backend persists the same change to disk; we then refetch to make sure
      // any other field (eg targetMm) is in sync.
      if (command === 'enable' || command === 'disable') {
        set((current) => ({
          config: {
            ...current.config,
            gripper: {
              ...current.config.gripper,
              [enabledKey]: command === 'enable',
            },
          },
        }))
      }
      void gripperCommandApi(side, command, targetMm, get().config.gripper.commandForceLimitN)
        .then(() => {
          set((current) => ({
            logs: appendLog(current.logs, makeLog('INFO', `${side} gripper ${command} accepted by backend`, '[GRIPPER]')),
          }))
          // For state-changing commands, pull the canonical config back from
          // backend so the UI never drifts (eg if /api/settings has been
          // edited externally).
          if (command === 'enable' || command === 'disable') {
            void fetchConfig()
              .then((config) => set({ config }))
              .catch(() => undefined)
          }
        })
        .catch((error) => {
          set((current) => ({
            config: command === 'enable' || command === 'disable'
              ? {
                  ...current.config,
                  gripper: {
                    ...current.config.gripper,
                    [enabledKey]: previousEnabled,
                  },
                }
              : current.config,
            logs: appendLog(current.logs, makeLog('ERROR', `gripper command failed: ${String(error)}`, '[GRIPPER]')),
          }))
        })
      return
    }
    set((state) => {
      const enabledKey = side === 'left' ? 'leftEnabled' : 'rightEnabled'
      const targetKey = side === 'left' ? 'targetLeftMm' : 'targetRightMm'
      const currentTarget = state.config.gripper[targetKey]
      const commandTarget =
        command === 'open'
          ? state.config.gripper.strokeMm
          : command === 'close' || command === 'home'
            ? 0
            : command === 'target'
              ? clamp(targetMm ?? currentTarget, 0, state.config.gripper.strokeMm)
              : currentTarget
      const nextEnabled = command === 'enable' ? true : command === 'disable' ? false : state.config.gripper[enabledKey]
      const nextConfig = {
        ...state.config,
        gripper: {
          ...state.config.gripper,
          [enabledKey]: nextEnabled,
          [targetKey]: commandTarget,
        },
      }
      const action: ManualControlAction = {
        id: ++_manualActionIdSeq,
        type: 'gripper',
        ts: Date.now(),
        side,
        command,
        targetMm: commandTarget,
        forceLimitN: state.config.gripper.commandForceLimitN,
        enabled: nextEnabled,
      }
      const nextManual = appendManualAction(state.manualControl, action)
      const label = side === 'left' ? '左夹爪' : '右夹爪'
      const commandText: Record<ManualGripperCommand, string> = {
        enable: '使能',
        disable: '断使能',
        open: '打开',
        close: '闭合',
        home: '回零',
        target: `目标 ${commandTarget.toFixed(1)}mm`,
        stop: '停止',
      }
      return {
        config: nextConfig,
        manualControl: nextManual,
        logs: appendLog(state.logs, makeLog('INFO', `${label} ${commandText[command]} · gripper test fixture`, '[GRIPPER]')),
      }
    })
  },

  startManualRecording: () =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        recording: true,
        recordingStartedAt: Date.now(),
        draftActions: [],
      },
      logs: appendLog(state.logs, makeLog('INFO', 'Manual action recording started', '[HAL]')),
    })),

  stopManualRecording: () =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        recording: false,
      },
      logs: appendLog(state.logs, makeLog('INFO', 'Manual action recording stopped', '[HAL]')),
    })),

  saveManualMemory: (name) =>
    set((state) => {
      const actions = state.manualControl.draftActions
      if (actions.length === 0) {
        return {
          logs: appendLog(state.logs, makeLog('WARNING', 'Manual memory save skipped: no recorded actions', '[HAL]')),
        }
      }
      const createdAt = Date.now()
      const firstTs = actions[0]?.ts ?? createdAt
      const lastTs = actions.at(-1)?.ts ?? createdAt
      const memory = {
        id: ++_manualMemoryIdSeq,
        name: name?.trim() || `动作记忆 ${state.manualControl.memories.length + 1}`,
        createdAt,
        durationMs: Math.max(0, lastTs - firstTs),
        actions,
      }
      return {
        manualControl: {
          ...state.manualControl,
          recording: false,
          recordingStartedAt: null,
          draftActions: [],
          memories: [memory, ...state.manualControl.memories].slice(0, 12),
        },
        logs: appendLog(state.logs, makeLog('INFO', `Manual memory saved: ${memory.name}, ${actions.length} actions`, '[HAL]')),
      }
    }),

  replayManualMemory: (id) =>
    set((state) => {
      const memory = state.manualControl.memories.find((item) => item.id === id)
      if (!memory) return state
      return {
        manualControl: {
          ...state.manualControl,
          replayingMemoryId: id,
        },
        logs: appendLog(state.logs, makeLog('INFO', `Manual memory replay queued: ${memory.name} (${memory.actions.length} actions)`, '[HAL]')),
      }
    }),

  pauseManualReplay: () =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        replayingMemoryId: null,
      },
      logs: appendLog(state.logs, makeLog('WARNING', 'Manual memory replay paused', '[HAL]')),
    })),

  deleteManualMemory: (id) =>
    set((state) => ({
      manualControl: {
        ...state.manualControl,
        memories: state.manualControl.memories.filter((item) => item.id !== id),
        replayingMemoryId: state.manualControl.replayingMemoryId === id ? null : state.manualControl.replayingMemoryId,
      },
      logs: appendLog(state.logs, makeLog('INFO', 'Manual memory deleted', '[HAL]')),
    })),

  closeQualityReport: () => set({ qualityReport: null }),
}))
