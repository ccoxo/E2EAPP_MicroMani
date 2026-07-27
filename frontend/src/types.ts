export type ConnectionState = 'ok' | 'warn' | 'error' | 'checking' | 'pending'

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'

export type LogChannel =
  | '[HAL]'
  | '[BACKEND]'
  | '[CAMERA]'
  | '[FORCE]'
  | '[SAFETY]'
  | '[ZMQ]'
  | '[POLICY]'
  | '[LEROBOT]'
  | '[GRIPPER]'

export interface LogEntry {
  id: number
  ts: number
  channel: LogChannel
  level: LogLevel
  msg: string
}

export interface ProcessStatus {
  name: 'hal' | 'backend' | 'policy' | 'recorder' | 'wsl'
  label: string
  status: 'running' | 'not_running' | 'degraded' | 'error'
  pid?: number
  cpuPct: number
  memMb: number
  vramGb?: number
  autoRestart?: boolean
}

export interface DiagnosticItem {
  key: string
  label: string
  method: string
  metric: string
  status: ConnectionState
  remediation: string
  suspect?: boolean
}

export interface CameraTelemetry {
  key: 'global' | 'wrist_left' | 'wrist_right'
  label: string
  fps: number
  timestampSkewMs: number
  frameAgeMs: number
  health: ConnectionState
  backend?: string | null
  workerActive?: boolean | null
}

export interface CameraTuningProfile {
  autoExposure: boolean
  exposure: number
  gain: number
  autoWhiteBalance: boolean
}

export interface Omega7Telemetry {
  side: 'left' | 'right'
  connected: boolean
  calibrated: boolean
  openId: number
  deviceId: number
  serial: string
  systemName: string
  leftHanded: boolean | null
  pose: number[]
  clutchPressed: boolean
  gripperPressed: boolean
  gripperGapMm: number | null
  lastReadOk: boolean
  message: string
}

export interface TelemetryFrame {
  timestamp: number
  elapsedSec: number
  jointPositions: number[]
  gripperPositions: number[]
  motionEnabled: { left: boolean | null; right: boolean | null }
  motionAxisEnabled: { left: Array<boolean | null>; right: Array<boolean | null> }
  forceLeft: number[]
  forceRight: number[]
  dangerIndex: number
  recording: boolean
  episodeCount: number
  frameCount: number
  halOk: boolean
  wsOk: boolean
  cameras: CameraTelemetry[]
  teleopHands: Omega7Telemetry[]
  queueDepth: { left: number; right: number }
  resource: { uiFps: number; wsHz: number; cpuPct: number; memMb: number }
  processStatus: ProcessStatus[]
}

export interface TelemetrySample {
  time: number
  joints: number[]
  forceLeft: number[]
  forceRight: number[]
  danger: number
  queueLeft: number
  queueRight: number
}

export interface SafetyState {
  dangerIndex: number
  estopActive: boolean
  manualOverride: number | null
  confirmedAt?: number
}

export interface MotionAxisProfile {
  startSpeed: number
  maxSpeed: number
  accTimeSec: number
  decTimeSec: number
}

export interface ArmMotionProfile {
  translation: MotionAxisProfile
  rotation: MotionAxisProfile
}

export interface MotionKinematicsConfig {
  source: string
  axisOrder: string[]
  leftAxisMap: number[]
  rightAxisMap: number[]
  leftPhysicalAxis: number[]
  rightPhysicalAxis: number[]
  axisUnitSpec: string[]
  leftPulsePerUnit: number[]
  rightPulsePerUnit: number[]
  leftDirectionSign: number[]
  rightDirectionSign: number[]
  leftSignedPulsePerUnit: number[]
  rightSignedPulsePerUnit: number[]
  syncActionPulseCoeff: boolean
  updatedAt: string
}

export interface AxisSoftLimitConfig {
  min: number
  max: number
}

export interface ArmSoftLimitConfig {
  x: AxisSoftLimitConfig
  y: AxisSoftLimitConfig
  z: AxisSoftLimitConfig
  roll: AxisSoftLimitConfig
  pitch: AxisSoftLimitConfig
  yaw: AxisSoftLimitConfig
}

export interface RotationWorkLimitSideConfig {
  roll: AxisSoftLimitConfig
  pitch: AxisSoftLimitConfig
  yaw: AxisSoftLimitConfig
}

export interface RotationWorkLimitConfig {
  enabled: boolean
  left: RotationWorkLimitSideConfig
  right: RotationWorkLimitSideConfig
}

export interface MotionOriginConfig {
  valid: boolean
  leftValid: boolean
  rightValid: boolean
  leftPulse: number[]
  rightPulse: number[]
  updatedAt: number
  previousValid: boolean
  previousLeftPulse: number[]
  previousRightPulse: number[]
  previousUpdatedAt: number
}

export interface MotionHomeReferenceConfig {
  valid: boolean
  leftValid: boolean
  rightValid: boolean
  leftPulse: number[]
  rightPulse: number[]
  updatedAt: number
}

export interface MotionWorkOriginOffsetConfig {
  valid: boolean
  leftValid: boolean
  rightValid: boolean
  leftPulseDelta: number[]
  rightPulseDelta: number[]
  updatedAt: number
}

export interface MotionRelativeSoftLimitsConfig {
  left: ArmSoftLimitConfig
  right: ArmSoftLimitConfig
}

export interface MotionStartupHomeConfig {
  enabled: boolean
  mode: 'work_origin'
}

export type ManualControlSide = 'left' | 'right'
export type ManualControlAxis = 'X' | 'Y' | 'Z' | 'Roll' | 'Pitch' | 'Yaw'
export type ManualSpeedMode = 'fine' | 'medium' | 'coarse'
export type ManualGripperCommand = 'enable' | 'disable' | 'open' | 'close' | 'home' | 'target' | 'stop'
export type PicoVisionCameraSource = CameraTelemetry['key']
export type PicoVisionRotation = 'none' | 'cw90' | 'ccw90' | '180'
export type Omega7StabilityMode = 'track' | 'hold' | 'off'
export type TeleopControlMode = 'velocity_admittance' | 'incremental_position'

export type RecorderPhase = 'idle' | 'starting' | 'recording' | 'reviewing' | 'resetting' | 'saving' | 'finishing'

export interface EpisodeRecord {
  index: number
  frameCount: number
  durationS: number
  status: 'ok' | 'emergency' | 'discarded'
  maxForceLeft: number
  maxForceRight: number
  lateFrames: number
  cameraDrops: { global: number; wristLeft: number; wristRight: number }
}

export interface RecordQualityReport extends EpisodeRecord {
  warnings: string[]
  passed: boolean
}

export interface RecordSessionState {
  datasetName: string
  task: string
  targetEpisodes: number
  episodeTimeS: number
  resetTimeS: number
  currentEpisode: number
  savedEpisodes: number
  episodeHistory: EpisodeRecord[]
  latestQualityReport: RecordQualityReport | null
  phase: RecorderPhase
  phaseStartedAt: number | null
  recorderFps: number
  recorderFrameCount: number
  recorderLateFrames: number
  recorderElapsedS: number
  recorderTotalS: number
  resetPending: boolean
  resetRequiredSides: ManualControlSide[]
  resetReturnedSides: ManualControlSide[]
  resetReady: boolean
  returnOriginInFlight: boolean
  forceTareActive: boolean
  speedMode: ManualSpeedMode
}

export type DatasetEpisodeStatusApi = 'valid' | 'review' | 'invalid'
export type DatasetCameraKeyApi = 'global' | 'wrist_left' | 'wrist_right'

export interface DatasetFeatureApi {
  dtype?: string
  shape?: number[]
  names?: string[]
}

export type DatasetFeatureSummaryApi = Record<string, DatasetFeatureApi>

export interface DatasetCameraResolutionApi {
  physical?: string
  capture?: string
  preview?: string
  saved?: string
}

export interface DatasetEpisodeSampleApi {
  frame: number
  leftJoints: number[]
  rightJoints: number[]
  leftPulses?: number[]
  rightPulses?: number[]
  forceLeft: number[]
  forceRight: number[]
  images?: Partial<Record<DatasetCameraKeyApi, string>>
}

export interface DatasetEpisodeMotionOriginApi {
  valid?: boolean
  leftValid?: boolean
  rightValid?: boolean
  leftPulse?: number[]
  rightPulse?: number[]
  updatedAt?: number
  positionSource?: string
  configHash?: string
}

export interface DatasetEpisodeMotionCalibrationApi {
  kinematics?: Partial<MotionKinematicsConfig>
  teleop?: {
    strategyVersion?: string
    mappingMode?: string
    leftImpulseCoeff?: number[]
    rightImpulseCoeff?: number[]
    leftDirectionSign?: number[]
    rightDirectionSign?: number[]
    syncImpulseCoeffFromKinematics?: boolean
  }
  stateUnitSpec?: string[]
  configHash?: string
}

export interface DatasetEpisodeApi {
  id: string
  name: string
  task: string
  status: DatasetEpisodeStatusApi
  quality: number
  frames: number
  fps: number
  durationS: number
  createdAt: number
  warnings: string[]
  samples: DatasetEpisodeSampleApi[]
  lateFrames?: number
  cameraDrops?: Partial<Record<DatasetCameraKeyApi, number>>
  maxForceLeft?: number
  maxForceRight?: number
  features?: DatasetFeatureSummaryApi
  featureSummary?: DatasetFeatureSummaryApi
  cameraResolutions?: Partial<Record<DatasetCameraKeyApi, DatasetCameraResolutionApi>>
  motionOrigin?: DatasetEpisodeMotionOriginApi
  motionCalibration?: DatasetEpisodeMotionCalibrationApi
}

export interface DatasetApi {
  id: string
  name: string
  status: 'local' | 'dry-run' | '待审核' | string
  fps: number
  format: string
  root?: string
  createdAt?: number
  updatedAt?: number
  featureSummary?: DatasetFeatureSummaryApi
  cameraResolutions?: Partial<Record<DatasetCameraKeyApi, DatasetCameraResolutionApi>>
  episodes: DatasetEpisodeApi[]
}

export type ManualControlAction =
  | {
      id: number
      type: 'arm-axis'
      ts: number
      side: ManualControlSide
      axis: ManualControlAxis
      delta: number
      unit: 'um' | '°'
      speedMode: ManualSpeedMode
      positionAfter: number
    }
  | {
      id: number
      type: 'gripper'
      ts: number
      side: ManualControlSide
      command: ManualGripperCommand
      targetMm: number
      forceLimitN: number
      enabled: boolean
    }

export interface ManualControlMemory {
  id: number
  name: string
  createdAt: number
  durationMs: number
  actions: ManualControlAction[]
}

export interface ManualControlState {
  selectedSide: ManualControlSide
  selectedAxis: ManualControlAxis
  axisStepUm: number
  axisStepDeg: number
  speedMode: ManualSpeedMode
  axisBusyUntil: Record<string, number>
  recording: boolean
  recordingStartedAt: number | null
  draftActions: ManualControlAction[]
  memories: ManualControlMemory[]
  replayingMemoryId: number | null
  axisOffsets: Record<string, number>
}

export interface AppConfig {
  hal: {
    baseUrl: string
    wsUrl: string
    axisCount: number
    apiConfirmed: boolean
  }
  cameras: {
    global: string
    globalIdentity?: string
    wristLeft: string
    wristLeftIdentity?: string
    wristRight: string
    wristRightIdentity?: string
    previewResolution: string
    globalResolution?: string
    wristLeftResolution?: string
    wristRightResolution?: string
    fps: number
    tuningDefaultsVersion?: string
    tuning: Record<CameraTelemetry['key'], CameraTuningProfile>
  }
  force: {
    leftIp: string
    rightIp: string
    port: number
    sampleHz: number
    recordWindowSamples: number
    tareSamples: number
    certificateConfirmed: boolean
    calibrationEnabled: boolean
    leftCalibrationPath: string
    rightCalibrationPath: string
    inputMode: string
    voltageMin: number
    voltageMax: number
    lowpassEnabled: boolean
    lowpassCutoffHz: number
    swapHands: boolean
  }
  motion: {
    leftCardNo: number
    rightCardNo: number
    motionThreadHz: number
    jogStepUm: number
    jogStepDeg: number
    yawSoftLimitDeg: number
    positionSource: 'dmc_get_position' | 'dmc_get_encoder'
    workOriginStrategyVersion: string
    homeReferenceVersion: string
    origin: MotionOriginConfig
    homeReference: MotionHomeReferenceConfig
    workOriginOffset: MotionWorkOriginOffsetConfig
    relativeSoftLimits: MotionRelativeSoftLimitsConfig
    homeOnStartup: MotionStartupHomeConfig
    leftProfile: ArmMotionProfile
    rightProfile: ArmMotionProfile
    leftSoftLimits: ArmSoftLimitConfig
    rightSoftLimits: ArmSoftLimitConfig
    rotationWorkLimits: RotationWorkLimitConfig
    kinematics: MotionKinematicsConfig
  }
  gripper: {
    leftPort: string
    rightPort: string
    baudrate: number
    leftSlaveId: number
    rightSlaveId: number
    strokeMm: number
    targetLeftMm: number
    targetRightMm: number
    leftEnabled: boolean
    rightEnabled: boolean
    commandForceLimitN: number
    commandSpeed: number
    commandTorque: number
    icfTargetProtectionEnabled: boolean
    icfTargetMinGapMm: number
    sampleHz: number
    sampleStaleMs: number
    sampleEnableOnNegative: boolean
    workerCommandTimeoutSec: number
    forceFeedbackAvailable: boolean
  }
  safety: {
    fxyWarnN: number
    fxyStopN: number
    fzWarnN: number
    fzStopN: number
    momentWarnNm: number
    momentStopNm: number
    yawSoftLimitDeg: number
    watchdogMs: number
  }
  zmq: {
    observationPush: string
    actionPull: string
    timeoutMs: number
  }
  storage: {
    datasetRoot: string
    recordFps: number
    videoCrf: number
    pushToHub: boolean
  }
  auto: {
    allowHardwareDispatch: boolean
    translationStepUm: number
    rotationStepDeg: number
    translationVelocityUmS: number
    rotationVelocityDegS: number
  }
  picoVision: {
    ip: string
    adbPort: number
    videoPort: number
    commandPort: number
    gateway: string
    ifIndex: number
    rotation: PicoVisionRotation
    cameraSource: PicoVisionCameraSource
  }
  teleop: {
    controlMode: TeleopControlMode
    nativeLoopHz: number
    nativeTranslationDeadzoneM: number
    nativeTranslationFullScaleM: number
    nativeRotationDeadzoneDeg: number
    nativeRotationFullScaleDeg: number
    nativeVelocitySmoothingMs: number
    kalmanFilterEnabled: boolean
    kalmanBeta: number
    kalmanMinVariance: number
    kalmanMaxVariance: number
    kalmanDtMinSec: number
    kalmanDtMaxSec: number
    kalmanTranslationPositionVariance: number
    kalmanTranslationVelocityVariance: number
    kalmanTranslationMeasurementVariance: number
    kalmanTranslationProcessPositionVariance: number
    kalmanTranslationProcessVelocityVariance: number
    kalmanRotationPositionVariance: number
    kalmanRotationVelocityVariance: number
    kalmanRotationMeasurementVariance: number
    kalmanRotationProcessPositionVariance: number
    kalmanRotationProcessVelocityVariance: number
    kalmanTranslationIntentVelocityThreshold: number
    kalmanRotationIntentVelocityThreshold: number
    coarse: number
    medium: number
    fine: number
    inputIntervalMs: number
    commandIntervalMs: number
    leftOpenId: number
    rightOpenId: number
    leftConnected: boolean
    rightConnected: boolean
    leftGravityCompensation: boolean
    rightGravityCompensation: boolean
    leftForceFeedback: boolean
    rightForceFeedback: boolean
    leftGravityScale: number
    rightGravityScale: number
    strategyVersion: string
    mappingMode: 'direct' | 'legacy'
    swapHands: boolean
    swapTeleopChannels: boolean
    leftTranslationScale: number
    rightTranslationScale: number
    leftRotationScale: number
    rightRotationScale: number
    homeBeforeStart: boolean
    leftAxisOutputScale: number[]
    rightAxisOutputScale: number[]
    translationStepUm: number
    rotationStepDeg: number
    translationStepLimitPulse: number
    rotationStepLimitPulse: number
    translationPulseDeadband: number
    rotationPulseDeadband: number
    translationDeadzone: number
    rotationDeadzone: number
    incrementalTranslationMinEffectiveDelta: number
    incrementalTranslationReverseDeadzone: number
    translationStartVelocityUmS: number
    translationMaxVelocityUmS: number
    rotationStartVelocityDegS: number
    rotationMaxVelocityDegS: number
    motionProfileAccSec: number
    motionProfileDecSec: number
    continuousIncrementMode: boolean
    translationInputEpsilon: number
    rotationInputEpsilon: number
    translationMinActivePulse: number
    rotationMinActivePulse: number
    continuousMicroConfirmTicks: number
    diagLog: boolean
    leftEnabledAxes: boolean[]
    rightEnabledAxes: boolean[]
    softLimitUnitSpec: string[]
    leftSoftLimitMin: number[]
    leftSoftLimitMax: number[]
    rightSoftLimitMin: number[]
    rightSoftLimitMax: number[]
    leftImpulseCoeff: number[]
    rightImpulseCoeff: number[]
    leftDirectionSign: number[]
    rightDirectionSign: number[]
    syncImpulseCoeffFromKinematics: boolean
    requireClutch: boolean
    stabilityMode: Omega7StabilityMode
    tcpFallbackPort: number
    gripperTeleop: {
      enabled: boolean
      loopHz: number
      leftGapMinMm: number
      leftGapMaxMm: number
      rightGapMinMm: number
      rightGapMaxMm: number
      leftGapInvert: boolean
      rightGapInvert: boolean
      openThreshold: number
      closeThreshold: number
      gripSpeed: number
      gripTorque: number
      positionDeadbandCounts: number
      minCommandIntervalMs: number
      autoGapCalibration: boolean
      autoGapMinSpanMm: number
      autoGapMarginMm: number
      releaseSpeed: number
      releaseTorque: number
      leftSourceHand: string
      rightSourceHand: string
      objectDetectMargin: number
      buttonFallback: boolean
      diagLog: boolean
    }
  }
  wsl: {
    distro: string
    condaEnv: string
    pythonPath: string
    pendingWindowsValidation: boolean
  }
}

export type ParameterSnapshotScope = 'all' | 'motion-left' | 'motion-right'

export interface MotionCardSnapshotConfig {
  cardNo: number
  motionThreadHz: number
  yawSoftLimitDeg: number
  positionSource: AppConfig['motion']['positionSource']
  profile: ArmMotionProfile
  softLimits: ArmSoftLimitConfig
  rotationWorkLimitEnabled: boolean
  rotationWorkLimits: RotationWorkLimitSideConfig
}

export type ParameterSnapshot =
  | {
      id: string
      name: string
      createdAt: number
      scope: 'all'
      config: AppConfig
    }
  | {
      id: string
      name: string
      createdAt: number
      scope: 'motion-left' | 'motion-right'
      config: MotionCardSnapshotConfig
    }

export interface QualityReport {
  episode: number
  durationSec: number
  frames: number
  task: string
  maxSkewMs: number
  jitterMs: number
  maxForceLeft: number
  maxForceRight: number
  warnings: string[]
}
