/*
 * 夹爪设置卡与手动夹爪控制；从 SettingsView 按域拆出。
 * 展示语义：请求启停 / 命令进度 / 反馈健康 三者分离。
 */
import { Hand, PlugZap, RotateCcw, Square } from 'lucide-react'
import { mockMode } from '../../api'
import { UiButton, UiField, UiInput, UiNumber, UiSlider, UiSpace, UiSwitch, UiTag, UiText, UiTitle } from '../../components/ui'
import { useGripperDisplay } from '../../hooks/useGripperDisplay'
import { useTelemetryStore } from '../../stores/telemetry'
import { controlSafetyBlockReason } from '../../utils/controlSafety'
import {
  armHardwareSpecs,
  hardwareSideForOperatorSide,
  operatorSideLabel,
  type RobotSide,
} from '../../data'
import type {
  AppConfig,
  ConnectionState,
  LogEntry,
  ManualGripperCommand,
} from '../../types'
import {
  commandLog,
  formatGripperPosition,
  HardwareConfigCard,
  MetricBox,
  safeGripperPosition,
  type GripperPortHint,
  type InlineStatusTone,
  type PendingComparison,
} from './shared'

function useGripperSafetyCommand(
  hardwareSide: RobotSide,
  issueManualGripperMove: (side: RobotSide, command: ManualGripperCommand, targetMm?: number) => void,
) {
  const blockReason = useTelemetryStore((state) => controlSafetyBlockReason(state, !mockMode))
  const sendCommand = (command: ManualGripperCommand, targetMm?: number) => {
    const reason = controlSafetyBlockReason(useTelemetryStore.getState(), !mockMode)
    if (command !== 'stop' && command !== 'disable' && reason) {
      useTelemetryStore.getState().injectLog('WARNING', `夹爪操作受阻：${reason}`, '[GRIPPER]')
      return false
    }
    issueManualGripperMove(hardwareSide, command, targetMm)
    return true
  }
  return { blockReason, sendCommand }
}

export function GripperCard({
  side,
  config,
  updateConfig,
  focusHash,
  currentMm,
  issueManualGripperMove,
  injectLog,
  requestComparison,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  currentMm: number
  issueManualGripperMove: (side: RobotSide, command: ManualGripperCommand, targetMm?: number) => void
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
  requestComparison: (comparison: PendingComparison) => void
}) {
  const hardwareSide = hardwareSideForOperatorSide(side)
  const sideSpec = armHardwareSpecs[hardwareSide]
  const operatorLabel = operatorSideLabel(side)
  const id = `gripper-${side}`
  const portKey = hardwareSide === 'left' ? 'leftPort' : 'rightPort'
  const targetKey = hardwareSide === 'left' ? 'targetLeftMm' : 'targetRightMm'
  const slaveKey = hardwareSide === 'left' ? 'leftSlaveId' : 'rightSlaveId'
  const enabledKey = hardwareSide === 'left' ? 'leftEnabled' : 'rightEnabled'
  const gripperEnabled = Boolean(config.gripper[enabledKey])
  const gripperDisplay = useGripperDisplay(hardwareSide)
  const halNativeGripperTeleop = true
  const { blockReason, sendCommand } = useGripperSafetyCommand(hardwareSide, issueManualGripperMove)
  const canCommandGripper = !blockReason
  const gapMinKey = side === 'left' ? 'leftGapMinMm' : 'rightGapMinMm'
  const gapMaxKey = side === 'left' ? 'leftGapMaxMm' : 'rightGapMaxMm'
  const protectedMinGapMm = config.gripper.icfTargetProtectionEnabled
    ? Math.min(Math.max(config.gripper.icfTargetMinGapMm, 0), config.gripper.strokeMm)
    : 0
 /** 设置当前流程的对应状态。 */
 const setTarget = (value: number) => updateConfig({ gripper: { ...config.gripper, [targetKey]: Math.min(Math.max(value, protectedMinGapMm), config.gripper.strokeMm) } })
 /** 设置当前流程的对应状态。 */
 const setForceFeedback = (checked: boolean) => updateConfig({ gripper: { ...config.gripper, forceFeedbackAvailable: checked } })
  const currentText = formatGripperPosition(currentMm)
 /** 设置当前流程的对应状态。 */
 const setTargetAndRun = (label: string, value: number) => {
    const targetValue = Math.min(Math.max(value, protectedMinGapMm), config.gripper.strokeMm)
    if (!sendCommand('target', targetValue)) return
    setTarget(targetValue)
    commandLog(injectLog, '[GRIPPER]', `${operatorLabel}夹爪${label}`)
  }
  const gt = config.teleop.gripperTeleop
  /** 设置当前流程的对应状态。 */
  const setGt = (patch: Partial<typeof gt>) =>
    updateConfig({ teleop: { ...config.teleop, gripperTeleop: { ...gt, ...patch } } })
  const teleopRunning = halNativeGripperTeleop && Boolean(config.teleop.leftConnected || config.teleop.rightConnected)
  const fallbackTeleopPort: GripperPortHint = {
    side: hardwareSide,
    port: config.gripper[portKey],
    slaveId: config.gripper[slaveKey],
    baudrate: config.gripper.baudrate,
  }
  const teleopPort = fallbackTeleopPort
  const teleopPortSummary = `${teleopPort.port ?? '-'} / slave ${teleopPort.slaveId ?? '-'}`
  const teleopPortDetail = `${teleopPort.baudrate ?? config.gripper.baudrate} baud · HAL-native 夹爪遥操作串口`
  const teleopRunSummary = halNativeGripperTeleop
    ? teleopRunning
      ? '随 Omega.7 自动遥操作'
      : '等待主手连接'
    : gripperEnabled
      ? '已使能'
      : '未使能'
  const teleopRunDetail = halNativeGripperTeleop ? '主手连接后 HAL-native 会自动接管夹爪' : '等待 Omega.7 夹爪输入'
  const gripperPortTone: InlineStatusTone = 'pending'
  const gripperRunTone: InlineStatusTone = teleopRunning ? 'ok' : 'pending'
  const gripperHasTeleopError = false
  const gripperErrorDetail = ''
  const gripperCardState: ConnectionState = halNativeGripperTeleop
    ? teleopRunning ? 'ok' : 'pending'
    : config.gripper[enabledKey] ? 'ok' : 'pending'
  // 三套语义分开展示：请求启停 ≠ 命令进度 ≠ 反馈健康。
  const gripperControlLabel = '请求启停'
  const gripperControlValue = gripperDisplay.requestLabel
  const gripperControlTone = gripperDisplay.requestTone === 'success' ? 'ok' : 'warn'
  /** 处理对应的用户交互。 */
  const requestGripperTarget = () =>
    requestComparison({
      title: `${operatorLabel}夹爪执行目标`,
      tone: 'warning',
      impact: `将向${operatorLabel}夹爪下发目标开合命令。`,
      expected: '确认后仍由现有夹爪安全限制和命令力限制保护。',
      current: [
        { label: '当前开度', value: currentText },
        { label: gripperControlLabel, value: gripperControlValue },
      ],
      proposed: [
        { label: '目标开合', value: `${config.gripper[targetKey].toFixed(1)} mm` },
        { label: '命令力限制', value: `≤${config.gripper.commandForceLimitN.toFixed(1)} N` },
      ],
      confirmText: '确认执行',
     onConfirm: () => { sendCommand('target', Math.max(config.gripper[targetKey], protectedMinGapMm)) },
    })
  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Hand size={20} />}
      title={`${operatorLabel}夹爪 · EPG006`}
      subtitle={`RS485 / pyserial · ${config.gripper[portKey]} · 从站 ${config.gripper[slaveKey]}`}
      state={gripperCardState}
      badges={
        <>
          <UiTag tone="processing">0-26 mm</UiTag>
          <UiTag tone="warning">力传感待确认</UiTag>
          <UiTag tone={gripperDisplay.requestTone}>{gripperDisplay.requestLabel}</UiTag>
          {gripperDisplay.commandLabel && (
            <span role="status" aria-label={`${operatorLabel}夹爪命令进度`}>{gripperDisplay.commandLabel}</span>
          )}
          <UiTag tone={gripperDisplay.feedbackTone}>{gripperDisplay.feedbackLabel}</UiTag>
          {halNativeGripperTeleop && <UiTag tone={teleopRunning ? 'success' : 'processing'}>HAL-native 接管</UiTag>}
        </>
      }
    >
      <div className="gripper-settings-stack">
        {blockReason && <div role="status" className="ui-alert ui-alert-warning">{blockReason}</div>}
        <div className="gripper-config-section">
          <div className="hardware-subtitle-row">
            <b>连接参数</b>
            <span>配置保存到后端；下方按钮会通过 RS485 下发夹爪命令</span>
          </div>
          <div className="hardware-form-grid hardware-form-grid-compact ui-form">
            <UiField label="COM 口">
              <UiInput value={config.gripper[portKey]} onChange={(event) => updateConfig({ gripper: { ...config.gripper, [portKey]: event.target.value } })} />
            </UiField>
            <UiField label="波特率"><UiNumber value={config.gripper.baudrate} onChange={(value) => updateConfig({ gripper: { ...config.gripper, baudrate: Number(value ?? 115200) } })} /></UiField>
            <UiField label="从站地址"><UiNumber value={config.gripper[slaveKey]} onChange={(value) => updateConfig({ gripper: { ...config.gripper, [slaveKey]: Number(value ?? sideSpec.gripperSlaveId) } })} /></UiField>
            <UiField label="行程 mm"><UiNumber value={config.gripper.strokeMm} onChange={(value) => updateConfig({ gripper: { ...config.gripper, strokeMm: Number(value ?? 26) } })} /></UiField>
            <UiField label="命令力限制 N"><UiNumber min={0} max={8} value={config.gripper.commandForceLimitN} onChange={(value) => updateConfig({ gripper: { ...config.gripper, commandForceLimitN: Number(value ?? 8) } })} /></UiField>
            <UiField label="采样 Hz">
              <UiNumber min={1} max={60} value={config.gripper.sampleHz} onChange={(value) => updateConfig({ gripper: { ...config.gripper, sampleHz: Number(value ?? 30) } })} />
            </UiField>
            <UiField label="力反馈传感">
              <UiSwitch checked={config.gripper.forceFeedbackAvailable} checkedChildren="已接入" unCheckedChildren="待确认" onChange={setForceFeedback} />
            </UiField>
            <UiField label="ICF 靶保护">
              <UiSwitch checked={config.gripper.icfTargetProtectionEnabled} checkedChildren="开" unCheckedChildren="关" onChange={(value) => updateConfig({ gripper: { ...config.gripper, icfTargetProtectionEnabled: value } })} />
            </UiField>
            <UiField label="最小开度 mm">
              <UiNumber min={0} max={config.gripper.strokeMm} step={0.01} value={config.gripper.icfTargetMinGapMm} onChange={(value) => updateConfig({ gripper: { ...config.gripper, icfTargetMinGapMm: Math.min(Math.max(Number(value ?? 1.02), 0), config.gripper.strokeMm) } })} />
            </UiField>
          </div>
        </div>
        <div className="gripper-status-section">
          <div className="hardware-metric-grid gripper-metric-grid">
            <MetricBox label="请求启停" value={gripperDisplay.requestLabel} tone={gripperControlTone} />
            <MetricBox label="命令进度" value={gripperDisplay.commandLabel || '空闲'} tone={gripperDisplay.command.phase === 'failed' ? 'warn' : 'neutral'} />
            <MetricBox label="反馈健康" value={gripperDisplay.feedbackLabel} tone={gripperDisplay.feedback === 'ok' ? 'ok' : gripperDisplay.feedback === 'unknown' ? 'neutral' : 'warn'} hint="反馈正常 ≠ 已使能" />
            <MetricBox label="当前开度" value={currentText} />
            <MetricBox label="目标开度" value={`${config.gripper[targetKey].toFixed(1)} mm`} />
            <MetricBox label="Omega.7 映射" value={`${gt[gapMinKey].toFixed(1)}-${gt[gapMaxKey].toFixed(1)} mm`} hint="夹持角 0-0.45 rad" />
            <MetricBox label="ICF 靶保护" value={config.gripper.icfTargetProtectionEnabled ? `${protectedMinGapMm.toFixed(2)} mm` : '关闭'} />
            <MetricBox label="夹持角力矩反馈" value="手册未给出" hint="EPG006 章节仅确认位置接口" tone="warn" />
            <MetricBox label="命令侧力限制" value={`≤${config.gripper.commandForceLimitN.toFixed(1)} N`} hint="Omega.7 gripper force 输出上限" />
          </div>
          <UiSlider min={protectedMinGapMm} max={config.gripper.strokeMm} step={0.1} value={Math.max(config.gripper[targetKey], protectedMinGapMm)} onChange={(value) => setTarget(Number(value))} />
        </div>
        <div className="gripper-action-section">
          {!halNativeGripperTeleop && (
            <UiButton
              variant={gripperEnabled ? 'default' : 'primary'}
              icon={<PlugZap size={15} />}
              disabled={!gripperEnabled && !canCommandGripper}
              onClick={() => sendCommand(gripperEnabled ? 'disable' : 'enable')}
            >
              {gripperEnabled ? '断使能' : '使能'}
            </UiButton>
          )}
          <UiButton disabled={!canCommandGripper} onClick={requestGripperTarget}>执行目标</UiButton>
          <UiButton disabled={!canCommandGripper} onClick={() => sendCommand('open')}>打开</UiButton>
          <UiButton disabled={!canCommandGripper} onClick={() => sendCommand('close')}>闭合</UiButton>
          <UiButton disabled={!canCommandGripper} icon={<RotateCcw size={15} />} onClick={() => setTargetAndRun('回零', 0)}>
            回零
          </UiButton>
          <UiButton icon={<Square size={15} />} onClick={() => sendCommand('stop')}>
            停止
          </UiButton>
        </div>
        {halNativeGripperTeleop && (
          <div className="gripper-manual-command-section">
            <div className="hardware-subtitle-row">
              <b>高级手动命令</b>
              <span>通常无需操作；主手连接后 HAL-native 会自动接管夹爪</span>
            </div>
            <UiSpace size={8} wrap>
              <UiButton disabled={!canCommandGripper} icon={<PlugZap size={14} />} onClick={() => sendCommand('enable')}>
                手动下发使能
              </UiButton>
              <UiButton danger onClick={() => sendCommand('disable')}>
                手动断使能
              </UiButton>
            </UiSpace>
          </div>
        )}
        <div className="gripper-config-section">
          <div className="hardware-subtitle-row">
            <b>Omega7 夹爪遥操作</b>
            <UiSpace size={6}>
              <UiTag tone={teleopRunning ? 'success' : 'processing'}>{teleopRunSummary}</UiTag>
            </UiSpace>
          </div>
          {gripperHasTeleopError && (
            <div className="hardware-error-callout gripper-error-callout" role="alert">
            <b>{operatorLabel}夹爪遥操连接异常</b>
              <span>{gripperErrorDetail || teleopRunSummary}</span>
            </div>
          )}
          <div className="gripper-teleop-strip">
            <div className={`gripper-status-${gripperPortTone}`}>
              <b>夹爪串口</b>
              <span>{teleopPortSummary}</span>
              <small>{teleopPortDetail}</small>
            </div>
            <div className={`gripper-status-${gripperRunTone}`}>
              <b>遥操作</b>
              <span>{teleopRunSummary}</span>
              <small>{teleopRunDetail}</small>
            </div>
          </div>
          <div className="hardware-form-grid hardware-form-grid-compact ui-form">
            <UiField label="Gap 最小 mm (夹紧)">
              <UiNumber value={gt[gapMinKey]} onChange={(v) => setGt({ [gapMinKey]: Number(v ?? 0) })} />
            </UiField>
            <UiField label="Gap 最大 mm (张开)">
              <UiNumber value={gt[gapMaxKey]} onChange={(v) => setGt({ [gapMaxKey]: Number(v ?? 50) })} />
            </UiField>
            <UiField label="开阈值 (0-1)">
              <UiNumber min={0} max={1} step={0.05} value={gt.openThreshold} onChange={(v) => setGt({ openThreshold: Number(v ?? 0.3) })} />
            </UiField>
            <UiField label="闭阈值 (0-1)">
              <UiNumber min={0} max={1} step={0.05} value={gt.closeThreshold} onChange={(v) => setGt({ closeThreshold: Number(v ?? 0.7) })} />
            </UiField>
            <UiField label="夹持速度">
              <UiNumber min={1} max={255} value={gt.gripSpeed} onChange={(v) => setGt({ gripSpeed: Number(v ?? 255) })} />
            </UiField>
            <UiField label="夹持力矩">
              <UiNumber min={1} max={255} value={gt.gripTorque} onChange={(v) => setGt({ gripTorque: Number(v ?? 1) })} />
            </UiField>
            <UiField label="释放速度">
              <UiNumber min={1} max={255} value={gt.releaseSpeed} onChange={(v) => setGt({ releaseSpeed: Number(v ?? 255) })} />
            </UiField>
            <UiField label="释放力矩">
              <UiNumber min={1} max={255} value={gt.releaseTorque} onChange={(v) => setGt({ releaseTorque: Number(v ?? 1) })} />
            </UiField>
            <UiField label="诊断日志">
              <UiSwitch checked={gt.diagLog} checkedChildren="开" unCheckedChildren="关" onChange={(v) => setGt({ diagLog: v })} />
            </UiField>
            <UiField label="Gap 自动量程">
              <UiSwitch checked={gt.autoGapCalibration} checkedChildren="开" unCheckedChildren="关" onChange={(v) => setGt({ autoGapCalibration: v })} />
            </UiField>
          </div>
        </div>
      </div>
    </HardwareConfigCard>
  )
}

export function ManualGripperControl({
  side,
  config,
  updateConfig,
  currentMm,
  issueManualGripperMove,
  requestComparison,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  currentMm: number
  issueManualGripperMove: (side: RobotSide, command: ManualGripperCommand, targetMm?: number) => void
  requestComparison: (comparison: PendingComparison) => void
}) {
  const hardwareSide = hardwareSideForOperatorSide(side)
  const operatorLabel = operatorSideLabel(side)
  const portKey = hardwareSide === 'left' ? 'leftPort' : 'rightPort'
  const targetKey = hardwareSide === 'left' ? 'targetLeftMm' : 'targetRightMm'
  const slaveKey = hardwareSide === 'left' ? 'leftSlaveId' : 'rightSlaveId'
  const enabledKey = hardwareSide === 'left' ? 'leftEnabled' : 'rightEnabled'
  const gripperEnabled = Boolean(config.gripper[enabledKey])
  const gripperDisplay = useGripperDisplay(hardwareSide)
  const { blockReason, sendCommand } = useGripperSafetyCommand(hardwareSide, issueManualGripperMove)
  const canCommandGripper = !blockReason
  /** 设置当前流程的对应状态。 */
  const protectedMinGapMm = config.gripper.icfTargetProtectionEnabled
    ? Math.min(Math.max(config.gripper.icfTargetMinGapMm, 0), config.gripper.strokeMm)
    : 0
  const setTarget = (value: number) => updateConfig({ gripper: { ...config.gripper, [targetKey]: Math.min(Math.max(value, protectedMinGapMm), config.gripper.strokeMm) } })
  const currentText = formatGripperPosition(currentMm)
  const jawMm = safeGripperPosition(currentMm)
 /** 处理对应的用户交互。 */
 const requestGripperTarget = () =>
    requestComparison({
      title: `${operatorLabel}夹爪执行目标`,
      tone: 'warning',
      impact: `将向${operatorLabel}夹爪下发目标开合命令。`,
      expected: '确认后仍由现有夹爪安全限制和命令力限制保护。',
      current: [
        { label: '当前开度', value: currentText },
        { label: '请求启停', value: gripperDisplay.requestLabel },
        { label: '反馈健康', value: gripperDisplay.feedbackLabel },
      ],
      proposed: [
        { label: '目标开合', value: `${config.gripper[targetKey].toFixed(1)} mm` },
        { label: '命令力限制', value: `≤${config.gripper.commandForceLimitN.toFixed(1)} N` },
      ],
      confirmText: '确认执行',
      onConfirm: () => { sendCommand('target', Math.max(config.gripper[targetKey], protectedMinGapMm)) },
    })
  return (
    <article className="manual-gripper-card">
      {blockReason && <div role="status" className="ui-alert ui-alert-warning">{blockReason}</div>}
      <div className="manual-card-head">
        <div>
          <UiTitle level={3}>{operatorLabel}夹爪手动控制</UiTitle>
          <UiText secondary>{config.gripper[portKey]} · 从站 {config.gripper[slaveKey]} · EPG006</UiText>
        </div>
        <UiSpace wrap>
          <UiTag tone={gripperDisplay.requestTone}>{gripperDisplay.requestLabel}</UiTag>
          {gripperDisplay.commandLabel && (
            <span role="status" aria-label={`${operatorLabel}夹爪命令进度`}>{gripperDisplay.commandLabel}</span>
          )}
          <UiTag tone={gripperDisplay.feedbackTone}>{gripperDisplay.feedbackLabel}</UiTag>
          <UiTag>0-26 mm</UiTag>
        </UiSpace>
      </div>
      <div className="manual-gripper-body">
        <div className="manual-gripper-visual">
          <span className="gripper-jaw gripper-jaw-left" style={{ transform: `translateX(${-Math.min(34, jawMm * 1.2)}px)` }} />
          <span className="gripper-jaw gripper-jaw-right" style={{ transform: `translateX(${Math.min(34, jawMm * 1.2)}px)` }} />
          <b>{currentText}</b>
        </div>
        <div className="manual-gripper-controls">
          <div className="manual-readout-row">
            <MetricBox label="请求启停" value={gripperDisplay.requestLabel} tone={gripperDisplay.requestTone === 'success' ? 'ok' : 'warn'} />
            <MetricBox label="命令进度" value={gripperDisplay.commandLabel || '空闲'} />
            <MetricBox label="反馈健康" value={gripperDisplay.feedbackLabel} hint="反馈正常 ≠ 已使能" tone={gripperDisplay.feedback === 'ok' ? 'ok' : 'warn'} />
            <MetricBox label="目标开度" value={`${config.gripper[targetKey].toFixed(1)} mm`} />
            <MetricBox label="命令力限制" value={`≤${config.gripper.commandForceLimitN.toFixed(1)} N`} />
            <MetricBox label="ICF 靶保护" value={config.gripper.icfTargetProtectionEnabled ? `${protectedMinGapMm.toFixed(2)} mm` : '关闭'} />
            <MetricBox label="夹爪力矩传感" value="待确认" hint="手册未给出 EPG006 反馈接口" tone="warn" />
          </div>
          <UiSlider min={protectedMinGapMm} max={config.gripper.strokeMm} step={0.1} value={Math.max(config.gripper[targetKey], protectedMinGapMm)} onChange={(value) => setTarget(Number(value))} />
          <div className="manual-command-form ui-form">
            <UiField label="目标开度 mm">
              <UiNumber min={protectedMinGapMm} max={config.gripper.strokeMm} step={0.1} value={Math.max(config.gripper[targetKey], protectedMinGapMm)} onChange={(value) => setTarget(Number(value ?? protectedMinGapMm))} />
            </UiField>
            <UiField label="命令力限制 N">
              <UiNumber min={0} max={8} value={config.gripper.commandForceLimitN} onChange={(value) => updateConfig({ gripper: { ...config.gripper, commandForceLimitN: Number(value ?? 8) } })} />
            </UiField>
          </div>
          <div className="manual-action-row">
            <UiButton disabled={!gripperEnabled && !canCommandGripper} variant={gripperEnabled ? 'default' : 'primary'} icon={<PlugZap size={15} />} onClick={() => sendCommand(gripperEnabled ? 'disable' : 'enable')}>
              {gripperEnabled ? '断使能' : '使能'}
            </UiButton>
            <UiButton disabled={!canCommandGripper} onClick={requestGripperTarget}>执行目标</UiButton>
            <UiButton disabled={!canCommandGripper} onClick={() => sendCommand('open')}>打开</UiButton>
            <UiButton disabled={!canCommandGripper} onClick={() => sendCommand('close')}>闭合</UiButton>
            <UiButton disabled={!canCommandGripper} icon={<RotateCcw size={15} />} onClick={() => sendCommand('home')}>回零</UiButton>
            <UiButton icon={<Square size={15} />} onClick={() => sendCommand('stop')}>停止</UiButton>
          </div>
        </div>
      </div>
    </article>
  )
}
