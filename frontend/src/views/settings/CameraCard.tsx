/*
 * 相机设置卡；从 SettingsView 按域拆出。
 * 先看：CameraCard。
 */
import { Camera, RefreshCw, Save } from 'lucide-react'
import { useState } from 'react'
import { applyCameraTuning, reconnectCamera } from '../../api'
import { CameraPreview } from '../../components/CameraPreview'
import {
  UiButton,
  UiField,
  UiInput,
  UiNumber,
  UiSelect,
  UiSlider,
  UiSpace,
  UiSwitch,
  UiTag,
  UiText,
} from '../../components/ui'
import { refreshCameraStream } from '../../hooks/useLiveCameraSnapshot'
import { cameraHardwareSpecs } from '../../data'
import type {
  AppConfig,
  CameraTuningProfile,
  CameraTelemetry,
  ConnectionState,
  LogEntry,
} from '../../types'
import {
  HardwareConfigCard,
  MetricBox,
  commandLog,
  stateTone,
  stateText,
  type InlineStatusTone,
  type PendingComparison,
} from './shared'
import type { ActionCompareItem } from '../../components/ActionCompareModal'

function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function inlineToneFromState(state: ConnectionState): InlineStatusTone {
  if (state === 'ok') return 'ok'
  if (state === 'warn') return 'warn'
  if (state === 'error') return 'error'
  return 'pending'
}

export type CameraKey = keyof typeof cameraHardwareSpecs

const defaultCameraTuning: Record<CameraKey, CameraTuningProfile> = {
  global: {
    autoExposure: true,
    exposure: -5.5,
    gain: 0,
    autoWhiteBalance: true,
  },
  wrist_left: {
    autoExposure: true,
    exposure: -6,
    gain: 0,
    autoWhiteBalance: true,
  },
  wrist_right: {
    autoExposure: true,
    exposure: -6,
    gain: 0,
    autoWhiteBalance: true,
  },
}
const cameraExposureMin = -13
const cameraExposureMax = 0
const cameraGainMin = 0
const cameraGainMax = 64
const previewResolutionOptions = [
  { value: '640x480', label: '640x480（推荐）' },
  { value: '320x240', label: '320x240（低负载）' },
]

/** 渲染当前界面单元，并连接所需数据。 */
export function CameraCard({
  cameraKey,
  camera,
  config,
  updateConfig,
  focusHash,
  injectLog,
  requestComparison,
}: {
  cameraKey: CameraKey
  camera?: CameraTelemetry
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
  requestComparison: (comparison: PendingComparison) => void
}) {
  const spec = cameraHardwareSpecs[cameraKey]
  const id = cameraKey === 'global' ? 'camera-global' : cameraKey === 'wrist_left' ? 'camera-left' : 'camera-right'
  const configField = cameraKey === 'global' ? 'global' : cameraKey === 'wrist_left' ? 'wristLeft' : 'wristRight'
  const resolutionField =
    cameraKey === 'global' ? 'globalResolution' : cameraKey === 'wrist_left' ? 'wristLeftResolution' : 'wristRightResolution'
  const previewResolution = config.cameras[resolutionField] ?? config.cameras.previewResolution
  const telemetryState = camera?.health ?? 'pending'
  const [previewHealth, setPreviewHealth] = useState<ConnectionState>('checking')
  const state: ConnectionState = !camera
    ? 'pending'
    : telemetryState === 'error' || previewHealth === 'error'
      ? 'error'
      : telemetryState === 'pending'
        ? 'pending'
        : previewHealth === 'checking' || previewHealth === 'pending'
          ? 'checking'
          : telemetryState === 'warn'
            ? 'warn'
            : 'ok'
  const tuning = config.cameras.tuning?.[cameraKey] ?? defaultCameraTuning[cameraKey]
  const [pendingCameraAction, setPendingCameraAction] = useState<'apply' | 'reconnect' | null>(null)
 /** 描述当前方法的功能边界。 */
 const sanitizeTuning = (next: CameraTuningProfile): CameraTuningProfile => {
    const exposure = Math.min(cameraExposureMax, Math.max(cameraExposureMin, Number(next.exposure)))
    const gain = Math.min(cameraGainMax, Math.max(cameraGainMin, Number(next.gain)))
    return {
      autoExposure: Boolean(next.autoExposure),
      exposure: Number.isFinite(exposure) ? exposure : defaultCameraTuning[cameraKey].exposure,
      gain: Number.isFinite(gain) ? gain : defaultCameraTuning[cameraKey].gain,
      autoWhiteBalance: Boolean(next.autoWhiteBalance),
    }
  }
 /** 描述当前方法的功能边界。 */
 const updateTuning = (patch: Partial<CameraTuningProfile>) => {
    const nextTuning = sanitizeTuning({ ...tuning, ...patch })
    updateConfig({
      cameras: {
        ...config.cameras,
        tuning: {
          ...(config.cameras.tuning ?? defaultCameraTuning),
          [cameraKey]: nextTuning,
        },
      },
    })
  }
 /** 处理对应的用户交互。 */
 const handleApplyTuning = async () => {
    setPendingCameraAction('apply')
    try {
      await applyCameraTuning(cameraKey, {
        ...config,
        cameras: {
          ...config.cameras,
          tuning: {
            ...(config.cameras.tuning ?? defaultCameraTuning),
            [cameraKey]: sanitizeTuning(tuning),
          },
        },
      })
      refreshCameraStream(cameraKey)
      commandLog(injectLog, '[CAMERA]', `${spec.label} camera tuning applied`)
    } catch (error) {
      injectLog('ERROR', `${spec.label} camera tuning failed: ${commandErrorMessage(error)}`, '[CAMERA]')
    } finally {
      setPendingCameraAction(null)
    }
  }
 /** 处理对应的用户交互。 */
 const handleReconnect = async () => {
    setPendingCameraAction('reconnect')
    try {
      await reconnectCamera(cameraKey)
      refreshCameraStream(cameraKey)
      commandLog(injectLog, '[CAMERA]', `${spec.label} camera reconnect requested`)
    } catch (error) {
      injectLog('ERROR', `${spec.label} camera reconnect failed: ${commandErrorMessage(error)}`, '[CAMERA]')
    } finally {
      setPendingCameraAction(null)
    }
  }
 /** 计算对应的业务值或展示值。 */
 const cameraTuningItems = (profile: CameraTuningProfile): ActionCompareItem[] => [
    { label: '分辨率', value: previewResolution },
    { label: 'FPS', value: `${config.cameras.fps}` },
    { label: 'Exposure', value: `${profile.exposure}` },
    { label: 'Gain', value: `${profile.gain}` },
    { label: 'Auto exposure', value: profile.autoExposure ? '开' : '关' },
    { label: 'Auto WB', value: profile.autoWhiteBalance ? '开' : '关' },
  ]
 /** 处理对应的用户交互。 */
 const requestApplyTuning = () => {
    const nextTuning = sanitizeTuning(tuning)
    requestComparison({
      title: `应用${spec.label}参数`,
      tone: 'warning',
      impact: `将写入${spec.label}预览参数，并刷新当前预览流。`,
      expected: '确认后会调用现有相机参数接口，失败时继续写入日志面板。',
      current: cameraTuningItems(tuning),
      proposed: cameraTuningItems(nextTuning),
      confirmText: '确认应用',
      onConfirm: handleApplyTuning,
    })
  }
  const cameraInlineTone = inlineToneFromState(state)
  const frameAgeTone: InlineStatusTone = !camera ? 'pending' : camera.frameAgeMs > 250 ? 'warn' : 'ok'
  const deviceText = config.cameras[configField] || spec.device

  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Camera size={20} />}
      title={`${spec.label} · ${spec.model}`}
      subtitle={`${previewResolution} @ ${config.cameras.fps}Hz · ${spec.lerobotKey}`}
      state={state}
      badges={<UiTag tone={stateTone(state)}>{previewResolution}</UiTag>}
    >
      {camera && <CameraPreview camera={camera} compact resolution={previewResolution} onPreviewHealthChange={setPreviewHealth} />}
      <div className="camera-status-strip">
        <div className={`camera-status-${cameraInlineTone}`}>
          <b>采集链路</b>
          <span>{stateText(state)}</span>
          <small>{camera ? `${camera.fps.toFixed(1)} FPS / ${previewResolution}` : '等待 telemetry'}</small>
        </div>
        <div className="camera-status-ok">
          <b>设备</b>
          <span>{deviceText}</span>
          <small>{spec.lerobotKey}</small>
        </div>
        <div className={`camera-status-${frameAgeTone}`}>
          <b>帧延迟</b>
          <span>{camera ? `${camera.frameAgeMs.toFixed(0)} ms` : '-'}</span>
          <small>{camera ? `clock ${camera.timestampSkewMs.toFixed(1)} ms` : '未收到帧'}</small>
        </div>
      </div>
      <div className="hardware-metric-grid camera-metric-grid">
        <MetricBox label="FPS" value={`${(camera?.fps ?? 0).toFixed(1)} / ${spec.fps}`} />
        <MetricBox label="Frame age" value={`${(camera?.frameAgeMs ?? 0).toFixed(0)} ms`} />
        <MetricBox label="Clock skew" value={`${(camera?.timestampSkewMs ?? 0).toFixed(1)} ms`} tone={Math.abs(camera?.timestampSkewMs ?? 0) > 16 ? 'warn' : 'ok'} />
      </div>
      <div className="camera-tuning-panel">
        <div className="camera-tuning-head">
          <UiText strong>相机参数</UiText>
          <UiSpace className="camera-tuning-actions" wrap>
            <UiButton icon={<Save size={15} />} loading={pendingCameraAction === 'apply'} onClick={requestApplyTuning}>
              应用参数
            </UiButton>
            <UiButton icon={<RefreshCw size={15} />} loading={pendingCameraAction === 'reconnect'} onClick={() => void handleReconnect()}>
              重连预览
            </UiButton>
          </UiSpace>
        </div>
      <div className="hardware-form-grid hardware-form-grid-compact camera-tuning-grid ui-form">
        <UiField label="设备">
          <UiInput value={config.cameras[configField]} onChange={(event) => updateConfig({ cameras: { ...config.cameras, [configField]: event.target.value } })} />
        </UiField>
        <UiField label="预览分辨率" tooltip="仅影响前端预览和相机流负载；默认 640x480 足够观察。">
          <UiSelect             value={previewResolution}
            onChange={(value) => updateConfig({ cameras: { ...config.cameras, [resolutionField]: value } })}
            options={previewResolutionOptions}
          />
        </UiField>
        <UiField label="相机采集目标 FPS" tooltip="后端相机预览流的目标帧率；数据保存频率在数据存储里的录制 FPS 单独设置。"><UiNumber min={1} max={60} value={config.cameras.fps} onChange={(value) => updateConfig({ cameras: { ...config.cameras, fps: Number(value ?? 30) } })} /></UiField>
        <UiField label="曝光 / 增益" className="camera-tuning-span">
          <div className="camera-tuning-control-stack">
            <div className="camera-tuning-toggle-row">
              <UiSwitch
                checked={Boolean(tuning.autoExposure)}
                checkedChildren="Auto"
                unCheckedChildren="Manual"
                onChange={(checked) => updateTuning({ autoExposure: checked })}
              />
              <UiSwitch
                checked={Boolean(tuning.autoWhiteBalance)}
                checkedChildren="Auto WB"
                unCheckedChildren="Manual WB"
                onChange={(checked) => updateTuning({ autoWhiteBalance: checked })}
              />
            </div>
            {cameraKey !== 'global' && tuning.autoExposure && (
              <UiText secondary>应用自动曝光时会尝试关闭低光降帧，实际 FPS 以预览测量为准。</UiText>
            )}
            <div className="camera-tuning-control-row">
              <UiText secondary className="camera-tuning-control-label">Exposure</UiText>
              <UiSlider
                min={cameraExposureMin}
                max={cameraExposureMax}
                step={0.5}
                value={tuning.exposure}
                onChange={(value) => updateTuning({ exposure: Number(value) })}
              />
              <UiNumber
                min={cameraExposureMin}
                max={cameraExposureMax}
                step={0.5}
                value={tuning.exposure}
                onChange={(value) => updateTuning({ exposure: Number(value ?? defaultCameraTuning[cameraKey].exposure) })}
              />
            </div>
            <div className="camera-tuning-control-row">
              <UiText secondary className="camera-tuning-control-label">Gain</UiText>
              <UiSlider
                min={cameraGainMin}
                max={cameraGainMax}
                step={1}
                value={tuning.gain}
                onChange={(value) => updateTuning({ gain: Number(value) })}
              />
              <UiNumber
                min={cameraGainMin}
                max={cameraGainMax}
                step={1}
                value={tuning.gain}
                onChange={(value) => updateTuning({ gain: Number(value ?? 0) })}
              />
            </div>
          </div>
        </UiField>
      </div>
      </div>
    </HardwareConfigCard>
  )
}
