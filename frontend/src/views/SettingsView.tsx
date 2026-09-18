/*
 * 阅读导航 01｜入口与界面
 * 职责：设置页壳层；硬件卡按域放在 settings/ 子模块。
 * 先看：SettingsView → settings/*Cards。
 */
import { UiButton, UiField, UiInput, UiSpace, UiTabs, UiTag, UiText, UiTitle } from '../components/ui'
import {
  AlertTriangle,
  RefreshCw,
  Save,
  ShieldAlert,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { ActionCompareModal } from '../components/ActionCompareModal'
import {
  applyConfig,
  fetchMotionOrigin,
  mockMode,
  type MotionPreviousRestoreStatus,
} from '../api'
import {
  hardwareSideForOperatorSide,
  operatorSideForHardwareSide,
  operatorSideLabel,
  type RobotSide,
} from '../data'
import { numberArrayEqual, teleopHandFrameEqual, teleopHandFrameSlice, useFrameField } from '../stores/frameSelectors'
import { useTelemetryStore } from '../stores/telemetry'
import { canAcknowledgeControlSafety } from '../utils/controlSafety'
import { GripperCard } from './settings/GripperCards'
import { MotionCard } from './settings/MotionCards'
import { TeleopHandCard } from './settings/TeleopHandCard'
import { CameraCard } from './settings/CameraCard'
import { WristCameraIdentification } from '../components/WristCameraIdentification'
import { ForceSensorCard } from './settings/ForceSensorCard'
import { ManualControlPanel } from './settings/ManualControlPanel'
import { HalCard } from './settings/HalCard'
import { SafetyCard } from './settings/SafetyCard'
import { PicoVisionCard } from './settings/PicoVisionCard'
import { StorageCard } from './settings/StorageCard'
import { ParameterSnapshotMenu } from './settings/ParameterSnapshotMenu'
import {
  type PendingComparison,
} from './settings/shared'
import type {
  ParameterSnapshotScope,
  TelemetryFrame,
} from '../types'

const sideOrder: RobotSide[] = ['left', 'right']
const cameraOrder = ['global', 'wrist_left', 'wrist_right'] as const
const emptyValues: number[] = []
const emptyCameras: TelemetryFrame['cameras'] = []

const hashLabels: Record<string, string> = {
  hal: 'HAL 通信',
  storage: '数据存储',
  safety: '安全链路',
  teleop: 'PICO-4 视觉推流',
  'motion-left': '左运动控制卡',
  'motion-right': '右运动控制卡',
  'camera-global': '全局相机',
  'camera-left': '左腕相机',
  'camera-right': '右腕相机',
  'force-left': '左臂六维力',
  'force-right': '右臂六维力',
  'gripper-left': '左夹爪',
  'gripper-right': '右夹爪',
  'teleop-left': '左 Omega.7',
  'teleop-right': '右 Omega.7',
  manual: '手动控制',
}

function tabForHardwareHash(focusHash: string) {
  if (focusHash === 'manual' || focusHash.startsWith('motion-')) return 'motion'
  if (focusHash.startsWith('gripper-') || focusHash.startsWith('teleop-')) return 'teleop'
  if (focusHash === 'teleop' || focusHash.startsWith('camera-') || focusHash === 'pico' || focusHash.startsWith('pico-')) return 'vision'
  if (focusHash.startsWith('force-') || focusHash === 'safety') return 'force'
  return 'system'
}

function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function defaultSnapshotName(scope: ParameterSnapshotScope) {
  const prefix = scope === 'all' ? '全局硬件' : `${operatorSideLabel(operatorSideForHardwareSide(scope === 'motion-left' ? 'left' : 'right'))}运动控制卡`
  return `${prefix}快照 ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`
}

function snapshotModalTitle(scope: ParameterSnapshotScope) {
  if (scope === 'all') return '保存全局硬件参数快照'
  return `保存${operatorSideLabel(operatorSideForHardwareSide(scope === 'motion-left' ? 'left' : 'right'))}运动控制卡参数`
}

/** 壳层：系统连接 / 安全与力觉 / 运动控制 / 遥操作 / 视觉 */
export function SettingsView() {
  const canAcknowledge = useTelemetryStore(canAcknowledgeControlSafety)
  const location = useLocation()
  const focusHash = location.hash.replace('#', '')
  const [activeTab, setActiveTab] = useState(() => tabForHardwareHash(focusHash))
  const config = useTelemetryStore((state) => state.config)
  const dangerIndex = useTelemetryStore((state) => activeTab === 'force' ? state.frame.dangerIndex : 0)
  const forceLeft = useFrameField((frame) => activeTab === 'force' ? frame.forceLeft : emptyValues, numberArrayEqual)
  const forceRight = useFrameField((frame) => activeTab === 'force' ? frame.forceRight : emptyValues, numberArrayEqual)
  const jointPositions = useFrameField((frame) => activeTab === 'motion' ? frame.jointPositions : emptyValues, numberArrayEqual)
  const gripperPositions = useFrameField((frame) => activeTab === 'motion' || activeTab === 'teleop' ? frame.gripperPositions : emptyValues, numberArrayEqual)
  const cameras = useTelemetryStore((state) => activeTab === 'vision' ? state.frame.cameras : emptyCameras)
  const forceStatus = useTelemetryStore((state) => activeTab === 'force' ? state.frame.forceStatus : undefined)
  const teleopFrame = useFrameField(
    (frame) => activeTab === 'teleop' ? teleopHandFrameSlice(frame) : null,
    (a, b) => a === b || (a !== null && b !== null && teleopHandFrameEqual(a, b)),
  )
  const updateConfig = useTelemetryStore((state) => state.updateConfig)
  const injectLog = useTelemetryStore((state) => state.sendBackendCommandLog)
  const setDangerOverride = useTelemetryStore((state) => state.setDangerOverride)
  const acknowledgeSafety = useTelemetryStore((state) => state.acknowledgeSafety)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const issueManualGripperMove = useTelemetryStore((state) => state.issueManualGripperMove)
  const manualControl = useTelemetryStore((state) => state.manualControl)
  const selectManualAxis = useTelemetryStore((state) => state.selectManualAxis)
  const setManualAxisStep = useTelemetryStore((state) => state.setManualAxisStep)
  const setManualSpeedMode = useTelemetryStore((state) => state.setManualSpeedMode)
  const issueManualAxisMove = useTelemetryStore((state) => state.issueManualAxisMove)
  const startManualRecording = useTelemetryStore((state) => state.startManualRecording)
  const stopManualRecording = useTelemetryStore((state) => state.stopManualRecording)
  const saveManualMemory = useTelemetryStore((state) => state.saveManualMemory)
  const replayManualMemory = useTelemetryStore((state) => state.replayManualMemory)
  const pauseManualReplay = useTelemetryStore((state) => state.pauseManualReplay)
  const deleteManualMemory = useTelemetryStore((state) => state.deleteManualMemory)
  const parameterSnapshots = useTelemetryStore((state) => state.parameterSnapshots)
  const saveParameterSnapshot = useTelemetryStore((state) => state.saveParameterSnapshot)
  const applyParameterSnapshot = useTelemetryStore((state) => state.applyParameterSnapshot)
  const deleteParameterSnapshot = useTelemetryStore((state) => state.deleteParameterSnapshot)

  const [pendingComparison, setPendingComparison] = useState<PendingComparison | null>(null)
  const [previousRestoreStatus, setPreviousRestoreStatus] = useState<MotionPreviousRestoreStatus | null>(null)
  const [snapshotDraft, setSnapshotDraft] = useState<{ scope: ParameterSnapshotScope; name: string } | null>(null)
  const [pendingReturnOriginSide, setPendingReturnOriginSide] = useState<RobotSide | null>(null)
  const returnOriginLock = useRef<RobotSide | null>(null)
  const applyingConfigRef = useRef(false)
  const [applyingConfig, setApplyingConfig] = useState(false)
  const [configApplyStatus, setConfigApplyStatus] = useState('')

  useEffect(() => {
    setActiveTab(tabForHardwareHash(focusHash))
  }, [focusHash])

  useEffect(() => {
    if (!applyingConfigRef.current) setConfigApplyStatus('')
  }, [config])

  // 锁保留在壳层，避免切换 Tab 后重新挂载的主手卡再次发送回原点请求。
  const updatePendingReturnOriginSide = useCallback((side: RobotSide | null) => {
    if (side !== null && returnOriginLock.current !== null) return false
    returnOriginLock.current = side
    setPendingReturnOriginSide(side)
    return true
  }, [])

  const applyRuntimeConfig = async () => {
    if (applyingConfigRef.current) return
    applyingConfigRef.current = true
    setApplyingConfig(true)
    setConfigApplyStatus('正在应用配置')
    try {
      await applyConfig(config)
      setConfigApplyStatus(useTelemetryStore.getState().config === config ? '配置已保存并应用' : '本次配置已应用；后续修改尚未应用')
      injectLog('INFO', '配置已保存并应用', '[HAL]')
    } catch (error) {
      const message = `配置应用失败：${commandErrorMessage(error)}`
      setConfigApplyStatus(message)
      injectLog('ERROR', message, '[HAL]')
    } finally {
      applyingConfigRef.current = false
      setApplyingConfig(false)
    }
  }

  const refreshMotionOriginStatus = useCallback(async () => {
    try {
      const response = await fetchMotionOrigin()
      setPreviousRestoreStatus(response.data?.previousRestore ?? null)
    } catch (error) {
      injectLog('WARNING', `motion origin status fetch failed: ${commandErrorMessage(error)}`, '[HAL]')
    }
  }, [injectLog])

  useEffect(() => {
    void refreshMotionOriginStatus()
  }, [refreshMotionOriginStatus])

  const openSnapshotModal = (scope: ParameterSnapshotScope) => setSnapshotDraft({ scope, name: defaultSnapshotName(scope) })
  const snapshotMenu = (scope: ParameterSnapshotScope) => ({
    items: parameterSnapshots
      .filter((item) => item.scope === scope)
      .map((item) => ({ key: item.id, label: item.name })),
    onClick: ({ key }: { key: string }) => applyParameterSnapshot(key),
    onDelete: deleteParameterSnapshot,
  })

  return (
    <div className="view-stack hardware-settings-view">
      <section className="page-header">
        <div>
          <UiTitle level={2}>硬件设置</UiTitle>
          <UiText secondary>参数保存到后端 config.json；手动控制命令经 Backend/HAL 下发到已连接硬件。</UiText>
        </div>
        <UiSpace wrap>
          {focusHash && <UiTag tone="processing">当前聚焦：{hashLabels[focusHash] ?? focusHash}</UiTag>}
          <ParameterSnapshotMenu title="选择硬件快照" menu={snapshotMenu('all')} />
          <UiButton icon={<Save size={16} />} variant="primary" onClick={() => openSnapshotModal('all')}>
            保存硬件快照
          </UiButton>
          <UiButton
            icon={<RefreshCw size={16} />}
            loading={applyingConfig}
            onClick={() => void applyRuntimeConfig()}
          >
            应用配置
          </UiButton>
          {configApplyStatus && <span role="status" aria-label="配置应用状态">{configApplyStatus}</span>}
          {mockMode && <UiButton danger icon={<AlertTriangle size={16} />} onClick={() => setDangerOverride(0.9)}>
            模拟危险
          </UiButton>}
          <UiButton icon={<ShieldAlert size={16} />} disabled={!canAcknowledge} onClick={acknowledgeSafety}>
            确认安全态
          </UiButton>
        </UiSpace>
      </section>

      <div className="ui-tabs">
        <UiTabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            { key: 'system', label: '系统连接' },
            { key: 'force', label: '安全与力觉' },
            { key: 'motion', label: '运动控制' },
            { key: 'teleop', label: '遥操作' },
            { key: 'vision', label: '视觉' },
          ]}
        />
      </div>

      {activeTab === 'system' ? (
        <section className="hardware-settings-page">
          <div className="module-group">
            <div className="module-group-head">
              HAL 通信
              <small>Backend ↔ HalServer</small>
            </div>
            <HalCard
              config={config}
              updateConfig={updateConfig}
              focusHash={focusHash}
              injectLog={injectLog}
            />
          </div>
          <div className="module-group">
            <div className="module-group-head">数据存储<small>目录 · 录制帧率</small></div>
            <StorageCard config={config} updateConfig={updateConfig} focusHash={focusHash} />
          </div>
        </section>
      ) : null}

      {activeTab === 'force' ? (
        <section className="hardware-settings-page">
          <div className="module-group">
            <div className="module-group-head">
              安全链路 / 急停 / 软限位
              <small>danger_index {dangerIndex.toFixed(2)}</small>
            </div>
            <SafetyCard
              config={config}
              updateConfig={updateConfig}
              focusHash={focusHash}
              triggerEmergencyStop={triggerEmergencyStop}
              acknowledgeSafety={acknowledgeSafety}
            />
          </div>
          <div className="module-group">
            <div className="module-group-head">
              六维力
              <small>Nano-17 / HKVL</small>
            </div>
            <div className="hardware-settings-grid">
              {sideOrder.map((side) => (
                <ForceSensorCard
                  key={side}
                  side={side}
                  config={config}
                  updateConfig={updateConfig}
                  focusHash={focusHash}
                  values={hardwareSideForOperatorSide(side) === 'left' ? forceLeft : forceRight}
                  forceStatus={forceStatus}
                  injectLog={injectLog}
                />
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {activeTab === 'motion' ? (
        <section className="hardware-settings-page">
          <div className="module-group">
            <div className="module-group-head">
              手动控制
              <small>点动 · 夹爪开合 · 动作回放</small>
            </div>
            <ManualControlPanel
              positions={jointPositions}
              grippers={gripperPositions}
              config={config}
              updateConfig={updateConfig}
              manualControl={manualControl}
              selectManualAxis={selectManualAxis}
              setManualAxisStep={setManualAxisStep}
              setManualSpeedMode={setManualSpeedMode}
              issueManualAxisMove={issueManualAxisMove}
              issueManualGripperMove={issueManualGripperMove}
              triggerEmergencyStop={triggerEmergencyStop}
              startManualRecording={startManualRecording}
              stopManualRecording={stopManualRecording}
              saveManualMemory={saveManualMemory}
              replayManualMemory={replayManualMemory}
              pauseManualReplay={pauseManualReplay}
              deleteManualMemory={deleteManualMemory}
              injectLog={injectLog}
              requestComparison={setPendingComparison}
            />
          </div>
          <div className="module-group">
            <div className="module-group-head">
              运动控制卡
              <small>轴映射 · 软限位 · 工作原点</small>
            </div>
            <div className="hardware-settings-grid">
              {sideOrder.map((side) => (
                <MotionCard
                  key={side}
                  side={side}
                  config={config}
                  updateConfig={updateConfig}
                  focusHash={focusHash}
                  positions={jointPositions}
                  injectLog={injectLog}
                  triggerEmergencyStop={triggerEmergencyStop}
                  snapshotMenu={snapshotMenu}
                  openSnapshotModal={openSnapshotModal}
                  requestComparison={setPendingComparison}
                  previousRestoreStatus={previousRestoreStatus}
                  refreshMotionOriginStatus={refreshMotionOriginStatus}
                />
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {activeTab === 'teleop' && teleopFrame ? (
        <section className="hardware-settings-page">
          <div className="module-group">
            <div className="module-group-head">
              Omega.7 主手
              <small>SDK 连接 · 映射与力反馈</small>
            </div>
            <div className="hardware-settings-grid">
              {sideOrder.map((side) => (
                <TeleopHandCard
                  key={side}
                  side={side}
                  config={config}
                  updateConfig={updateConfig}
                  focusHash={focusHash}
                  frame={teleopFrame}
                  injectLog={injectLog}
                  pendingReturnOriginSide={pendingReturnOriginSide}
                  setPendingReturnOriginSide={updatePendingReturnOriginSide}
                />
              ))}
            </div>
          </div>
          <div className="module-group">
            <div className="module-group-head">
              夹爪
              <small>EPG006 串口 · Omega.7 夹爪映射</small>
            </div>
            <div className="hardware-settings-grid">
              {sideOrder.map((side) => (
                <GripperCard
                  key={side}
                  side={side}
                  config={config}
                  updateConfig={updateConfig}
                  focusHash={focusHash}
                  currentMm={gripperPositions[hardwareSideForOperatorSide(side) === 'left' ? 0 : 1] ?? -1}
                  issueManualGripperMove={issueManualGripperMove}
                  injectLog={injectLog}
                  requestComparison={setPendingComparison}
                />
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {activeTab === 'vision' ? (
        <section className="hardware-settings-page">
          <div className="module-group">
            <div className="module-group-head">
              PICO-4 视觉推流
              <small>ADB · 网口 · 推流</small>
            </div>
            <PicoVisionCard
              config={config}
              updateConfig={updateConfig}
              focusHash={focusHash}
              injectLog={injectLog}
            />
          </div>
          <div className="module-group">
            <div className="module-group-head">
              相机
              <small>预览 · 调参 · 重连</small>
            </div>
            <WristCameraIdentification onSaved={(savedCameras) => useTelemetryStore.setState((state) => ({
              config: { ...state.config, cameras: savedCameras },
            }))} />
            <div className="hardware-settings-grid">
              {cameraOrder.map((key) => (
                <CameraCard
                  key={key}
                  cameraKey={key}
                  camera={cameras.find((camera) => camera.key === key)}
                  config={config}
                  updateConfig={updateConfig}
                  focusHash={focusHash}
                  injectLog={injectLog}
                  requestComparison={setPendingComparison}
                />
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {pendingComparison && (
        <ActionCompareModal
          open
          title={pendingComparison.title}
          tone={pendingComparison.tone}
          impact={pendingComparison.impact}
          expected={pendingComparison.expected}
          current={pendingComparison.current}
          proposed={pendingComparison.proposed}
          confirmText={pendingComparison.confirmText}
          onConfirm={() => {
            void pendingComparison.onConfirm()
            setPendingComparison(null)
          }}
          onCancel={() => setPendingComparison(null)}
        />
      )}

      {snapshotDraft && (
        <div className="ui-modal-mask" role="presentation" onClick={() => setSnapshotDraft(null)}>
          <div className="ui-modal" role="dialog" aria-label="保存参数快照" onClick={(event) => event.stopPropagation()}>
            <header className="ui-modal-head">
              <strong>{snapshotModalTitle(snapshotDraft.scope)}</strong>
            </header>
            <div className="ui-modal-body ui-form">
              <UiField label="快照名称">
                <UiInput value={snapshotDraft.name} onChange={(event) => setSnapshotDraft({ ...snapshotDraft, name: event.target.value })} />
              </UiField>
            </div>
            <div className="ui-modal-actions">
              <UiButton onClick={() => setSnapshotDraft(null)}>取消</UiButton>
              <UiButton
                variant="primary"
                onClick={() => {
                  saveParameterSnapshot(snapshotDraft.scope, snapshotDraft.name)
                  setSnapshotDraft(null)
                }}
              >
                保存
              </UiButton>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
