/*
 * Omega.7 主手设置卡；从 SettingsView 按域拆出。
 * 先看：TeleopHandCard。
 */
import {
  connectTeleopHand,
  disconnectTeleopHand,
  setTeleopGravityCompensation,
  zeroTeleopForceFeedback,
} from '../../api'
import * as appApi from '../../api'
import {
  Gamepad2,
  PlugZap,
  RotateCcw,
  ShieldAlert,
  Usb,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  UiButton,
  UiField,
  UiNumber,
  UiSelect,
  UiSlider,
  UiSpace,
  UiSwitch,
  UiTag,
} from '../../components/ui'
import {
  armHardwareSpecs,
  hardwareSideForOperatorSide,
  semanticAxes,
  type RobotSide,
} from '../../data'
import { motionSideReturnOriginReady } from '../../motionReturnReady'
import type { TeleopHandFrameSlice } from '../../stores/frameSelectors'
import { useTelemetryStore } from '../../stores/telemetry'
import { controlSafetyBlockReason } from '../../utils/controlSafety'
import type {
  AppConfig,
  ConnectionState,
  LogEntry,
} from '../../types'
import { HardwareConfigCard, MetricBox, commandLog, type InlineStatusTone } from './shared'

function commandErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function physicalText(connected: boolean) {
  return connected ? '在线' : '离线'
}

export function TeleopHandCard({
  side,
  config,
  updateConfig,
  focusHash,
  frame,
  injectLog,
  pendingReturnOriginSide,
  setPendingReturnOriginSide,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  frame: TeleopHandFrameSlice
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
  pendingReturnOriginSide: RobotSide | null
  setPendingReturnOriginSide: (side: RobotSide | null) => boolean
}) {
  const sideSpec = armHardwareSpecs[side]
  const controlBlockReason = useTelemetryStore((state) => controlSafetyBlockReason(state))
  const mappedHardwareSide = hardwareSideForOperatorSide(side)
  const hardwareSide = config.teleop.swapTeleopChannels ? mappedHardwareSide : side
  const id = `teleop-${side}`
  const handState = frame.teleopHands.find((item) => item.side === side)
  const logicalConnected = side === 'left' ? config.teleop.leftConnected : config.teleop.rightConnected
  const connected = logicalConnected && Boolean(handState?.connected)
  const openId = side === 'left' ? config.teleop.leftOpenId : config.teleop.rightOpenId
  const translationScale = side === 'left' ? config.teleop.leftTranslationScale : config.teleop.rightTranslationScale
  const rotationScale = side === 'left' ? config.teleop.leftRotationScale : config.teleop.rightRotationScale
  const gravityCompensation = side === 'left' ? config.teleop.leftGravityCompensation : config.teleop.rightGravityCompensation
  const forceFeedback = side === 'left' ? config.teleop.leftForceFeedback : config.teleop.rightForceFeedback
  const rawGravityScale = side === 'left' ? config.teleop.leftGravityScale : config.teleop.rightGravityScale
  const gravityScale = Number.isFinite(rawGravityScale) ? Math.max(0, Math.min(1, rawGravityScale)) : side === 'left' ? 0.45 : 1
 /** 描述当前方法的功能边界。 */
 const updateTeleop = (patch: Partial<AppConfig['teleop']>) => updateConfig({ teleop: { ...config.teleop, ...patch } })
 /** 设置当前流程的对应状态。 */
 const setConnected = (value: boolean) => updateTeleop(side === 'left' ? { leftConnected: value } : { rightConnected: value })
 /** 设置当前流程的对应状态。 */
 const setOpenId = (value: number) => updateTeleop(side === 'left' ? { leftOpenId: value } : { rightOpenId: value })
 /** 设置当前流程的对应状态。 */
 const setTranslationScale = (value: number) => updateTeleop(side === 'left' ? { leftTranslationScale: value } : { rightTranslationScale: value })
 /** 设置当前流程的对应状态。 */
 const setRotationScale = (value: number) => updateTeleop(side === 'left' ? { leftRotationScale: value } : { rightRotationScale: value })
 /** 设置当前流程的对应状态。 */
 const setGravityCompensation = (value: boolean) => updateTeleop(side === 'left' ? { leftGravityCompensation: value } : { rightGravityCompensation: value })
 /** 设置当前流程的对应状态。 */
 const setForceFeedback = (value: boolean) => updateTeleop(side === 'left' ? { leftForceFeedback: value } : { rightForceFeedback: value })
 /** 璁剧疆褰撳墠娴佺▼鐨勫搴旂姸鎬併€?*/
 const setGravityScale = (value: number) => updateTeleop(side === 'left' ? { leftGravityScale: value } : { rightGravityScale: value })
  const axisOutputScale = side === 'left' ? config.teleop.leftAxisOutputScale : config.teleop.rightAxisOutputScale
  const enabledAxes = side === 'left' ? config.teleop.leftEnabledAxes : config.teleop.rightEnabledAxes
 /** 设置当前流程的对应状态。 */
 const setAxisOutputScale = (axisIndex: number, value: number) => {
    const next = [...axisOutputScale]
    next[axisIndex] = value
    updateTeleop(side === 'left' ? { leftAxisOutputScale: next } : { rightAxisOutputScale: next })
  }
 /** 设置当前流程的对应状态。 */
 const setEnabledAxis = (axisIndex: number, value: boolean) => {
    const next = [...enabledAxes]
    next[axisIndex] = value
    updateTeleop(side === 'left' ? { leftEnabledAxes: next } : { rightEnabledAxes: next })
  }
  const physicalConnected = Boolean(handState?.connected)
  const liveReadOk = logicalConnected && physicalConnected && Boolean(handState?.lastReadOk)
  const pose = liveReadOk ? (handState?.pose ?? [0, 0, 0, 0, 0, 0]) : [0, 0, 0, 0, 0, 0]
  const positionMm = pose.slice(0, 3).map((value) => value * 1000)
  const rotationDeg = pose.slice(3, 6)
  const targetWorkOriginValid = hardwareSide === 'left' ? config.motion.origin.leftValid : config.motion.origin.rightValid
  const startupReturnsToWorkOrigin = config.teleop.homeBeforeStart
  const targetWorkOriginBlocked = startupReturnsToWorkOrigin && !targetWorkOriginValid
  const targetWorkOriginLabel = hardwareSide === 'left' ? '目标硬件左臂' : '目标硬件右臂'
  const readState: ConnectionState = !physicalConnected ? 'error' : targetWorkOriginBlocked ? 'warn' : !logicalConnected ? 'pending' : handState?.lastReadOk ? 'ok' : 'warn'
  const [connectionPending, setConnectionPending] = useState(false)
  const [connectSyncPending, setConnectSyncPending] = useState(false)
  const [connectionHint, setConnectionHint] = useState('')
  const teleopConnectPending = connectionPending || connectSyncPending
  useEffect(() => {
    if (!connectSyncPending) return
    if (!logicalConnected || physicalConnected) {
      setConnectSyncPending(false)
      setConnectionPending(false)
    }
  }, [connectSyncPending, logicalConnected, physicalConnected])
  const liveOpenId = handState?.openId ?? openId
  const omegaSummary = `OpenID ${liveOpenId} / device ${handState?.deviceId ?? '-'}`
  const handedText = handState?.leftHanded == null ? 'handedness -' : handState.leftHanded ? 'left-handed' : 'right-handed'
  const omegaDetail = `SN ${handState?.serial || '-'} · ${handedText} · Force Dimension USB`
  const connectionSummary = logicalConnected
    ? `逻辑已连接 · 物理${physicalText(physicalConnected)} · 读数${liveReadOk ? '正常' : '待恢复'}`
    : `逻辑未连接 · 物理${physicalText(physicalConnected)}`
  const workOriginBlockMessage = `${targetWorkOriginLabel}工作原点未设置`
  const connectionDetail = connectionHint || (targetWorkOriginBlocked ? '连接已阻止' : handState?.message) || (logicalConnected ? 'HAL-native 会等读数恢复后再放行动作' : '等待操作员连接')
  const teleopConnectionTone: InlineStatusTone =
    !physicalConnected ? 'error' : targetWorkOriginBlocked ? 'warn' : !logicalConnected ? 'pending' : handState?.lastReadOk ? 'ok' : 'warn'
  const teleopDeviceTone: InlineStatusTone = physicalConnected ? 'ok' : 'error'
  const teleopHasConnectionError = teleopConnectionTone === 'error' || teleopDeviceTone === 'error'
  const targetSideLabel = side === 'left' ? '左臂' : '右臂'
  const hardwareSideLabel = hardwareSide === 'left' ? '硬件左臂' : '硬件右臂'
  const teleopRouteHint = config.teleop.swapTeleopChannels ? `操作视角 · ${hardwareSideLabel}` : '同侧硬件通道'
  const returnOriginReady = motionSideReturnOriginReady(hardwareSide, frame.motionEnabled, frame.motionAxisEnabled)
  /** 发送或封装对应的后端命令。 */
  const returnToWorkOrigin = () => {
    const reason = controlSafetyBlockReason(useTelemetryStore.getState())
    if (reason) {
      injectLog('WARNING', `${sideSpec.shortLabel}回工作原点受阻：${reason}`, '[HAL]')
      return
    }
    if (!returnOriginReady) {
      injectLog('WARNING', `${sideSpec.shortLabel}未使能，已阻止返回工作原点`, '[HAL]')
      return
    }
    if (!setPendingReturnOriginSide(side)) return
    commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}返回工作原点`)
    void appApi.returnMotionOriginSide(hardwareSide)
      .then(() => injectLog('INFO', `${sideSpec.shortLabel}返回工作原点完成`, '[HAL]'))
      .catch((error) => injectLog('ERROR', `${sideSpec.shortLabel}返回工作原点失败: ${commandErrorMessage(error)}`, '[HAL]'))
      .finally(() => setPendingReturnOriginSide(null))
  }
  /** 发送或封装对应的后端命令。 */
  const toggleConnection = () => {
    if (teleopConnectPending) return
    const reason = logicalConnected ? null : controlSafetyBlockReason(useTelemetryStore.getState())
    if (reason) {
      setConnectionHint(reason)
      injectLog('WARNING', `${sideSpec.shortLabel}主手连接受阻：${reason}`, '[HAL]')
      return
    }
    if (!logicalConnected && targetWorkOriginBlocked) {
      setConnectionHint(workOriginBlockMessage)
      injectLog('WARNING', `${sideSpec.shortLabel} Omega.7 connect blocked: ${workOriginBlockMessage}`, '[HAL]')
      return
    }
    setConnectionPending(true)
    if (logicalConnected) {
      setConnectSyncPending(false)
      setConnectionHint('断开请求已发送')
      commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel} Omega.7 logical disconnect`)
      void disconnectTeleopHand(side)
        .then(() => {
          setConnected(false)
          setConnectionHint('逻辑连接已断开')
        })
        .catch((error) => {
          const message = commandErrorMessage(error)
          setConnectionHint(message)
          injectLog('ERROR', `${sideSpec.shortLabel} Omega.7 disconnect failed: ${message}`, '[HAL]')
        })
        .finally(() => setConnectionPending(false))
      return
    }
    setConnectionHint('逻辑连接请求已发送，后台同步 HAL')
    commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel} Omega.7 connect dhdOpenID(${openId})`)
    const generation = useTelemetryStore.getState().controlSafety.generation
    let waitForTelemetry = false
    void connectTeleopHand(side)
      .then((result) => {
        const current = useTelemetryStore.getState()
        const blocked = controlSafetyBlockReason(current)
        if (current.controlSafety.generation !== generation || blocked) {
          const message = `主手连接确认已取消：${blocked ?? '期间发生安全事件，请重新操作'}`
          setConnectionHint(message)
          injectLog('WARNING', `${sideSpec.shortLabel}${message}`, '[HAL]')
          return
        }
        const payload = result as { data?: { connected?: boolean; backgroundSync?: boolean; physicalConnected?: boolean; lastReadOk?: boolean; message?: string } }
        const nextConnected = payload.data?.connected ?? true
        setConnected(nextConnected)
        if (payload.data?.backgroundSync) {
          if (nextConnected) {
            waitForTelemetry = true
            setConnectSyncPending(true)
          }
          setConnectionHint(`${nextConnected ? '逻辑已连接' : '连接被拒绝'} · 后台同步中，等待遥测刷新${payload.data?.message ? ` · ${payload.data.message}` : ''}`)
        } else {
          const physical = payload.data?.physicalConnected ? '物理在线' : '物理离线'
          const read = payload.data?.lastReadOk ? '读数正常' : '读数待恢复'
          setConnectionHint(`${nextConnected ? '逻辑已连接' : '连接被拒绝'} · ${physical} · ${read}${payload.data?.message ? ` · ${payload.data.message}` : ''}`)
        }
        if (!nextConnected && payload.data?.message) {
          injectLog('WARNING', `${sideSpec.shortLabel} Omega.7 connect rejected: ${payload.data.message}`, '[HAL]')
        }
      })
      .catch((error) => {
        const message = commandErrorMessage(error)
        setConnectionHint(message)
        injectLog('ERROR', `${sideSpec.shortLabel} Omega.7 connect failed: ${message}`, '[HAL]')
      })
      .finally(() => {
        if (!waitForTelemetry) setConnectionPending(false)
      })
  }
  /** 设置当前流程的对应状态。 */
  const setGravityScaleValue = (value: number | null) => {
    const reason = gravityCompensation ? controlSafetyBlockReason(useTelemetryStore.getState()) : null
    if (reason) {
      injectLog('WARNING', `${sideSpec.shortLabel}重力补偿调整受阻：${reason}`, '[HAL]')
      return
    }
    const nextScale = Math.max(0, Math.min(1, Number(value ?? gravityScale)))
    setGravityScale(nextScale)
    if (!gravityCompensation) return
    void setTeleopGravityCompensation(side, { enabled: true, scale: nextScale }).catch((error) =>
      injectLog('ERROR', `${sideSpec.shortLabel} gravity scale command failed: ${String(error)}`, '[HAL]'),
    )
    commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel} gravity compensation scale ${nextScale.toFixed(2)}`)
  }
  const setGravityEnabled = (enabled: boolean) => {
    const reason = enabled ? controlSafetyBlockReason(useTelemetryStore.getState()) : null
    if (reason) {
      injectLog('WARNING', `${sideSpec.shortLabel}重力补偿启用受阻：${reason}`, '[HAL]')
      return
    }
    setGravityCompensation(enabled)
    setForceFeedback(enabled)
    void setTeleopGravityCompensation(side, { enabled, scale: gravityScale }).catch((error) =>
      injectLog('ERROR', `${sideSpec.shortLabel} gravity compensation command failed: ${String(error)}`, '[HAL]'),
    )
    commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}主手重力补偿${enabled ? '启用' : '关闭'}`)
  }
  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Gamepad2 size={20} />}
      title={`${sideSpec.shortLabel} Omega.7 主手`}
      subtitle={`配置 dhdOpenID(${openId}) · Force Dimension SDK / USB 直连`}
      state={readState}
      badges={
        <UiSpace size={6} wrap>
          <UiTag tone={physicalConnected ? (logicalConnected ? 'success' : 'warning') : 'error'}>
            {physicalConnected ? (logicalConnected ? '已连接' : '物理在线') : '物理离线'}
          </UiTag>
          <UiTag tone={connected && liveReadOk ? 'success' : !physicalConnected ? 'error' : logicalConnected ? 'warning' : 'muted'}>
            {connected && liveReadOk ? '读数正常' : !physicalConnected ? '连接失败' : logicalConnected ? '未收到读数' : '逻辑断开'}
          </UiTag>
          <UiTag>SN {handState?.serial || '-'}</UiTag>
          <UiTag>建议固定 USB 口顺序</UiTag>
        </UiSpace>
      }
      actions={
        <UiSpace wrap>
          <UiButton
            icon={<RotateCcw size={15} />}
            onClick={returnToWorkOrigin}
            loading={pendingReturnOriginSide === side}
            disabled={Boolean(controlBlockReason) || !returnOriginReady || pendingReturnOriginSide !== null}
            title={controlBlockReason ?? undefined}
          >
            回工作原点
          </UiButton>
          <span className="teleop-route-label">
            <small>目标臂</small>
            <b>{targetSideLabel}</b>
            <em>{teleopRouteHint}</em>
          </span>
          <UiButton
            variant={logicalConnected ? 'default' : 'primary'}
            danger={logicalConnected}
            icon={<Usb size={15} />}
            onClick={toggleConnection}
            loading={teleopConnectPending}
            disabled={teleopConnectPending || (!logicalConnected && (targetWorkOriginBlocked || Boolean(controlBlockReason)))}
            title={!logicalConnected ? controlBlockReason ?? undefined : undefined}
          >
            {logicalConnected ? '断开主手' : '连接主手'}
          </UiButton>
          <UiButton icon={<PlugZap size={15} />} disabled={!gravityCompensation && Boolean(controlBlockReason)} title={!gravityCompensation ? controlBlockReason ?? undefined : undefined} onClick={() => setGravityEnabled(!gravityCompensation)}>
            重力补偿
          </UiButton>
          <UiButton icon={<ShieldAlert size={15} />} onClick={() => {
            void zeroTeleopForceFeedback(side).catch((error) =>
              injectLog('ERROR', `${sideSpec.shortLabel} zero force feedback failed: ${String(error)}`, '[HAL]'),
            )
            commandLog(injectLog, '[HAL]', `${sideSpec.shortLabel}主手清零力反馈`)
          }}>
            清零力反馈
          </UiButton>
        </UiSpace>
      }
    >
      {controlBlockReason && <div role="status" className="ui-alert ui-alert-warning">{controlBlockReason}</div>}
      {teleopHasConnectionError && (
        <div className="hardware-error-callout teleop-error-callout" role="alert">
          <b>{sideSpec.shortLabel}主手物理离线</b>
          <span>{connectionDetail}</span>
        </div>
      )}
      {targetWorkOriginBlocked && (
        <div className="hardware-error-callout teleop-error-callout" role="alert">
          <b>{workOriginBlockMessage}</b>
          <span>连接前需要有效的目标硬件臂工作原点。</span>
        </div>
      )}
      <div className="teleop-connection-strip">
        <div className={`teleop-status-${teleopConnectionTone}`}>
          <b>连接</b>
          <span>{connectionSummary}</span>
          <small>{connectionDetail}</small>
        </div>
        <div className={`teleop-status-${teleopDeviceTone}`}>
          <b>Omega.7</b>
          <span>{omegaSummary}</span>
          <small>{omegaDetail}</small>
        </div>
      </div>
      <div className="hardware-metric-grid">
        <MetricBox label="X / Y / Z" value={liveReadOk ? `${positionMm[0].toFixed(1)}, ${positionMm[1].toFixed(1)}, ${positionMm[2].toFixed(1)} mm` : '-'} tone={liveReadOk ? 'ok' : 'warn'} />
        <MetricBox label="Roll / Pitch / Yaw" value={liveReadOk ? `${rotationDeg[0].toFixed(2)}, ${rotationDeg[1].toFixed(2)}, ${rotationDeg[2].toFixed(2)}°` : '-'} hint={`旋转比例 ${rotationScale}`} tone={liveReadOk ? 'ok' : 'warn'} />
        <MetricBox label="按钮 0 / 1" value={liveReadOk ? `${handState?.clutchPressed ? '按下' : '释放'} / ${handState?.gripperPressed ? '按下' : '释放'}` : '-'} />
        <MetricBox label="设备" value={`id ${handState?.deviceId ?? -1} · ${handState?.systemName || 'Omega.7'}`} hint={handState?.message || undefined} tone={physicalConnected && !handState?.message ? 'ok' : 'warn'} />
        <MetricBox label="夹爪间隙" value={handState?.gripperGapMm == null ? '-' : `${handState.gripperGapMm.toFixed(1)} mm`} />
        <MetricBox label="左右手属性" value={handState?.leftHanded == null ? '-' : handState.leftHanded ? 'Left-handed' : 'Right-handed'} />
      </div>
      <div className="hardware-form-grid teleop-hand-form ui-form">
        <UiField label="配置 OpenID">
          <UiNumber min={0} value={openId} onChange={(value) => setOpenId(Number(value ?? sideSpec.omegaDeviceId))} />
        </UiField>
        <UiField label="命令更新周期 ms">
          <UiNumber min={1} value={config.teleop.commandIntervalMs} onChange={(value) => updateTeleop({ commandIntervalMs: Number(value ?? 10) })} />
        </UiField>
        <UiField label="平移单步上限 um">
          <UiNumber
            min={1}
            step={100}
            value={config.teleop.translationStepUm}
            onChange={(value) => updateTeleop({ translationStepUm: Number(value ?? 5000) })}
          />
        </UiField>
        <UiField label="旋转单步上限 °">
          <UiNumber
            min={0.001}
            step={0.01}
            value={config.teleop.rotationStepDeg}
            onChange={(value) => updateTeleop({ rotationStepDeg: Number(value ?? 0.2) })}
          />
        </UiField>
        <UiField label="稳定模式">
          <UiSelect             value={config.teleop.stabilityMode}
            options={[
              { value: 'hold', label: 'Hold' },
              { value: 'track', label: 'Track' },
              { value: 'off', label: 'Off / Free' },
            ]}
            onChange={(value) => updateTeleop({ stabilityMode: value })}
          />
        </UiField>
        <UiField label="平移比例">
          <UiNumber min={0} step={0.01} value={translationScale} onChange={(value) => setTranslationScale(Number(value ?? 0.3))} />
        </UiField>
        <UiField label="旋转比例">
          <UiNumber min={0} step={0.01} value={rotationScale} onChange={(value) => setRotationScale(Number(value ?? 0.1))} />
        </UiField>
        <UiField label="Gravity compensation scale">
          <UiSpace wrap>
            <UiSlider className="teleop-gravity-scale-slider" disabled={gravityCompensation && Boolean(controlBlockReason)} min={0} max={1} step={0.05} value={gravityScale} onChange={(value) => setGravityScaleValue(Number(value))} />
            <UiNumber disabled={gravityCompensation && Boolean(controlBlockReason)} min={0} max={1} step={0.05} value={gravityScale} onChange={(value) => setGravityScaleValue(value == null ? null : Number(value))} />
          </UiSpace>
        </UiField>
        <UiField label="Translation step pulse">
          <UiNumber min={1} step={100} value={config.teleop.translationStepLimitPulse} onChange={(value) => updateTeleop({ translationStepLimitPulse: Number(value ?? 4000) })} />
        </UiField>
        <UiField label="Rotation step pulse">
          <UiNumber min={1} step={50} value={config.teleop.rotationStepLimitPulse} onChange={(value) => updateTeleop({ rotationStepLimitPulse: Number(value ?? 1250) })} />
        </UiField>
        <UiField label="平移死区">
          <UiNumber min={0} step={0.00001} value={config.teleop.translationDeadzone} onChange={(value) => updateTeleop({ translationDeadzone: Number(value ?? 0) })} />
        </UiField>
        <UiField label="旋转死区 °">
          <UiNumber min={0} step={0.01} value={config.teleop.rotationDeadzone} onChange={(value) => updateTeleop({ rotationDeadzone: Number(value ?? 0.02) })} />
        </UiField>
        <UiField label="Translation pulse deadband">
          <UiNumber min={0} step={1} value={config.teleop.translationPulseDeadband} onChange={(value) => updateTeleop({ translationPulseDeadband: Number(value ?? 2) })} />
        </UiField>
        <UiField label="Rotation pulse deadband">
          <UiNumber min={0} step={1} value={config.teleop.rotationPulseDeadband} onChange={(value) => updateTeleop({ rotationPulseDeadband: Number(value ?? 2) })} />
        </UiField>
        <UiField label="Translation min delta">
          <UiNumber min={0} step={0.00001} value={config.teleop.incrementalTranslationMinEffectiveDelta} onChange={(value) => updateTeleop({ incrementalTranslationMinEffectiveDelta: Number(value ?? 0.000025) })} />
        </UiField>
        <UiField label="Reverse deadzone">
          <UiNumber min={0} step={0.00001} value={config.teleop.incrementalTranslationReverseDeadzone} onChange={(value) => updateTeleop({ incrementalTranslationReverseDeadzone: Number(value ?? 0.00005) })} />
        </UiField>
        <UiField label="Translation speed um/s">
          <UiSpace wrap>
            <UiNumber min={0} value={config.teleop.translationStartVelocityUmS} onChange={(value) => updateTeleop({ translationStartVelocityUmS: Number(value ?? 600) })} />
            <UiNumber min={1} value={config.teleop.translationMaxVelocityUmS} onChange={(value) => updateTeleop({ translationMaxVelocityUmS: Number(value ?? 8000) })} />
          </UiSpace>
        </UiField>
        <UiField label="Rotation speed deg/s">
          <UiSpace wrap>
            <UiNumber min={0} step={0.05} value={config.teleop.rotationStartVelocityDegS} onChange={(value) => updateTeleop({ rotationStartVelocityDegS: Number(value ?? 1) })} />
            <UiNumber min={1} step={0.1} value={config.teleop.rotationMaxVelocityDegS} onChange={(value) => updateTeleop({ rotationMaxVelocityDegS: Number(value ?? 12) })} />
          </UiSpace>
        </UiField>
        <UiField label="Profile acc/dec s">
          <UiSpace wrap>
            <UiNumber min={0.001} step={0.01} value={config.teleop.motionProfileAccSec} onChange={(value) => updateTeleop({ motionProfileAccSec: Number(value ?? 0.05) })} />
            <UiNumber min={0.001} step={0.01} value={config.teleop.motionProfileDecSec} onChange={(value) => updateTeleop({ motionProfileDecSec: Number(value ?? 0.05) })} />
          </UiSpace>
        </UiField>
        <UiField label="诊断日志">
          <UiSwitch checked={config.teleop.diagLog} checkedChildren="开" unCheckedChildren="关" onChange={(value) => updateTeleop({ diagLog: value })} />
        </UiField>
      </div>
      <div className="teleop-switch-row">
        {semanticAxes.map((axis, axisIndex) => (
          <span key={axis}>
            <small>{axis}</small>
            <UiNumber min={0} step={0.05} value={axisOutputScale[axisIndex] ?? 1} onChange={(value) => setAxisOutputScale(axisIndex, Number(value ?? 1))} />
            <UiSwitch checked={enabledAxes[axisIndex] ?? true} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => setEnabledAxis(axisIndex, value)} />
          </span>
        ))}
      </div>
      <div className="teleop-switch-row">
        <span>
          <small>Swap hands</small>
          <UiSwitch checked={config.teleop.swapHands} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => updateTeleop({ swapHands: value })} />
        </span>
        <span>
          <small>Swap teleop channels</small>
          <UiSwitch checked={config.teleop.swapTeleopChannels} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => updateTeleop({ swapTeleopChannels: value })} />
        </span>
        <span>
          <small>重力补偿</small>
          <UiSwitch checked={gravityCompensation} disabled={!gravityCompensation && Boolean(controlBlockReason)} checkedChildren="开" unCheckedChildren="关" onChange={setGravityEnabled} />
        </span>
        <span>
          <small>力反馈使能</small>
          <UiSwitch checked={forceFeedback} checkedChildren="开" unCheckedChildren="关" onChange={setForceFeedback} />
        </span>
        <span>
          <small>Require clutch</small>
          <UiSwitch checked={config.teleop.requireClutch} checkedChildren="On" unCheckedChildren="Off" onChange={(value) => updateTeleop({ requireClutch: value })} />
        </span>
        <span>
          <small>TCP fallback</small>
          <UiNumber min={1} max={65535} value={config.teleop.tcpFallbackPort} onChange={(value) => updateTeleop({ tcpFallbackPort: Number(value ?? 12345) })} />
        </span>
      </div>
    </HardwareConfigCard>
  )
}
/** 计算或执行手动控制的对应逻辑。 */
