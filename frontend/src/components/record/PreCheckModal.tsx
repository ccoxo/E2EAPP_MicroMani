/*
 * 阅读导航 01｜入口与界面
 * 职责：执行录制前的逻辑连接、回原点和相机检查，区分阻断条件与提示。
 * 先看：StepDef → diagnosticReady → teleopHandsReady → requiredResetSides。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motionDeviceState } from '../../stores/motionCommands'
import { motionSideReturnOriginReady } from '../../motionReturnReady'
import { preCheckFrameEqual, preCheckFrameSlice, useFrameField, type PreCheckFrameSlice } from '../../stores/frameSelectors'
import { useTelemetryStore } from '../../stores/telemetry'
import type { DiagnosticItem, RecordSessionState, TelemetryLinkStatus } from '../../types'
import { UiButton, UiTag, UiText } from '../ui'

type PreCheckFrame = PreCheckFrameSlice

interface StepDef {
  title: string
  description: string
  autoCheck: boolean
  check: ((
    frame: PreCheckFrame,
    recordSession: RecordSessionState,
    diagnostics: DiagnosticItem[],
    telemetryLink: TelemetryLinkStatus,
  ) => boolean) | null
  /** 未通过时的具体原因，仅提示，不并入 allDone。 */
  reasons?: ((
    frame: PreCheckFrame,
    recordSession: RecordSessionState,
    diagnostics: DiagnosticItem[],
    telemetryLink: TelemetryLinkStatus,
  ) => string[]) | null
  required?: boolean
  actionButton?: {
    label: string
    disabled?: (frame: PreCheckFrame, recordSession: RecordSessionState) => boolean
  }
}
/** 计算对应的业务值或展示值。 */
function diagnosticReady(diagnostics: DiagnosticItem[], key: string) {
  return diagnostics.find((item) => item.key === key)?.status === 'ok'
}
/** 计算对应的业务值或展示值。 */
function teleopHandsReady(frame: Pick<PreCheckFrame, 'teleopHands'>) {
  const requiredHands = frame.teleopHands.filter(
    (hand) => !hand.message.toLowerCase().includes('logical teleop hand disconnected'),
  )
  return requiredHands.length > 0 && requiredHands.every((hand) => hand.connected && hand.lastReadOk)
}
function requiredResetSides(recordSession: RecordSessionState) {
  return recordSession.resetRequiredSides.length > 0 ? recordSession.resetRequiredSides : ['left' as const]
}
function requiredMotionReturnReady(frame: Pick<PreCheckFrame, 'motionEnabled' | 'motionAxisEnabled'>, recordSession: RecordSessionState) {
  return requiredResetSides(recordSession).every((side) =>
    motionSideReturnOriginReady(side, frame.motionEnabled, frame.motionAxisEnabled),
  )
}
function cameraWarnings(frame: Pick<PreCheckFrame, 'cameras'>) {
  return frame.cameras
    .map((camera) => {
      const backend = typeof camera.backend === 'string' ? camera.backend.toLowerCase() : ''
      const workerFallback = camera.workerActive === false && backend.includes('fallback')
      return { camera, workerFallback }
    })
    .filter(({ camera, workerFallback }) => camera.health === 'ok' && (camera.fps < 25 || workerFallback))
    .map(({ camera, workerFallback }) => `${camera.label}: ${camera.fps.toFixed(1)} Hz${workerFallback ? ' fallback' : ''}`)
}

const STEPS: StepDef[] = [
  {
    title: '硬件连接',
    description: '确认 HAL、WebSocket、相机、Omega.7 和夹爪串口均可识别。',
    autoCheck: true,
    check: (frame, _recordSession, diagnostics, telemetryLink) =>
      telemetryLink.state === 'live' &&
      frame.halOk &&
      frame.wsOk &&
      frame.cameras.every((camera) => camera.health === 'ok') &&
      diagnosticReady(diagnostics, 'omega7') &&
      teleopHandsReady(frame) &&
      diagnosticReady(diagnostics, 'gripper'),
    reasons: (frame, _recordSession, diagnostics, telemetryLink) => {
      const items: string[] = []
      if (telemetryLink.state !== 'live') items.push('遥测链路未就绪')
      if (!frame.halOk) items.push('HAL 不可用')
      if (!frame.wsOk) items.push('WebSocket 中断')
      const badCameras = frame.cameras.filter((c) => c.health !== 'ok').map((c) => c.label)
      if (badCameras.length) items.push(`相机异常：${badCameras.join('、')}`)
      if (!diagnosticReady(diagnostics, 'omega7')) items.push('Omega.7 诊断未通过')
      if (!teleopHandsReady(frame)) items.push('主手未全部连上或读数未恢复')
      if (!diagnosticReady(diagnostics, 'gripper')) items.push('夹爪串口诊断未通过')
      return items
    },
  },
  {
    title: '自动回到工作原点',
    description: '点击自动回工作原点，将左右从臂移动到已记录的工作原点；确认停止后勾选完成。',
    autoCheck: false,
    check: null,
    actionButton: {
      label: '自动回工作原点',
      disabled: (frame, recordSession) => !requiredMotionReturnReady(frame, recordSession),
    },
  },
  {
    title: '启动力觉自检',
    description: 'HKVL 须完成双侧同步去皮、残差验证及人工安全确认；自检状态跨录制会话保持。',
    autoCheck: true,
    check: (frame) => frame.forceStatus?.source !== 'hkvl_serial' || (
      frame.forceStatus.calibration?.state === 'ready' && frame.forceStatus.safety?.latched === false
    ),
  },
  {
    title: '验证力觉示数',
    description: '显示当前力值是否接近零；HKVL 的强制残差验证由 HAL 启动自检执行。',
    autoCheck: true,
    required: false,
    check: (frame) =>
      frame.forceLeft.every((v) => Math.abs(v) < 0.2) &&
      frame.forceRight.every((v) => Math.abs(v) < 0.2),
  },
]

interface PreCheckModalProps {
  open: boolean
  onConfirm: () => void
  onCancel: () => void
}
/** 渲染当前界面单元，并连接所需数据。 */
export default function PreCheckModal({ open, onConfirm, onCancel }: PreCheckModalProps) {
  const frame = useFrameField(preCheckFrameSlice, preCheckFrameEqual)
  const diagnostics = useTelemetryStore((s) => s.diagnostics)
  const telemetryLink = useTelemetryStore((s) => s.telemetryLink)
  const recordSession = useTelemetryStore((s) => s.recordSession)
  const forceSource = useTelemetryStore((s) => s.config.force.source)
  const homeRecordArms = useTelemetryStore((s) => s.homeRecordArms)
  const refreshHardwareStatus = useTelemetryStore((s) => s.refreshHardwareStatus)
  const [manualChecked, setManualChecked] = useState<Record<number, boolean>>({})
  const navigate = useNavigate()

  useEffect(() => {
    if (open) void refreshHardwareStatus()
  }, [open, refreshHardwareStatus])

  if (!open) return null

  const stepStatuses = STEPS.map((step, i) => {
    if (step.autoCheck && step.check) {
      if (i === 2 && forceSource === 'hkvl_serial') {
        return frame.forceStatus?.source === 'hkvl_serial'
          && frame.forceStatus.calibration?.state === 'ready' && frame.forceStatus.safety?.latched === false
      }
      return step.check(frame, recordSession, diagnostics, telemetryLink)
    }
    return (manualChecked[i] ?? false) && !recordSession.returnOriginInFlight && requiredResetSides(recordSession).every((side) => recordSession.resetReturnedSides.includes(side))
  })
  const warnings = cameraWarnings(frame)

  const allDone = STEPS.every((step, i) => step.required === false || stepStatuses[i])
  const currentStep = STEPS.findIndex((step, i) => step.required !== false && !stepStatuses[i])
  const activeStep = currentStep === -1 ? STEPS.length : currentStep

  const returnReady = requiredMotionReturnReady(frame, recordSession)
  const returnSides = requiredResetSides(recordSession)
  const returnBlockReasons: string[] = []
  if (!returnReady) {
    for (const side of returnSides) {
      const label = side === 'left' ? '左' : '右'
      const device = motionDeviceState(frame, telemetryLink, side)
      if (device === 'unknown') {
        returnBlockReasons.push(`${label}臂使能状态未知（遥测不可用或反馈不可读）`)
      } else if (device !== 'enabled') {
        returnBlockReasons.push(`${label}臂${device === 'partial' ? '部分使能' : device === 'disabled' ? '未使能' : '未就绪'}，需先使能再回工作原点`)
      }
    }
  }
  const hardwareReasons = STEPS[0].reasons?.(frame, recordSession, diagnostics, telemetryLink) ?? []

  /** 处理对应的用户交互。 */
  const handleClose = () => {
    setManualChecked({})
    onCancel()
  }

  /** 处理对应的用户交互。 */
  const handleConfirm = () => {
    setManualChecked({})
    onConfirm()
  }

  /** 关闭弹窗并跳到设置页手动控制。 */
  const goManualSettings = () => {
    handleClose()
    navigate('/settings#manual')
  }

  return (
    <div className="ui-modal-mask" role="presentation" onClick={handleClose}>
      <div
        className="ui-modal"
        style={{ width: 'min(520px, calc(100vw - 32px))' }}
        role="dialog"
        aria-label="采集会话开始前硬件检查"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="ui-modal-head">
          <strong>采集会话开始前硬件检查</strong>
        </header>
        <div className="ui-modal-body">
          {warnings.length > 0 && (
            <UiTag tone="warning">{warnings.join(' · ')}</UiTag>
          )}
          <ol className="ui-steps">
            {STEPS.map((step, i) => {
              const ok = stepStatuses[i]
              const required = step.required !== false
              return (
                <li key={step.title} className={`ui-step ${ok ? 'done' : i === activeStep ? 'current' : ''}`}>
                  <div className="ui-step-title">
                    <span className="ui-step-index">{i + 1}</span>
                    <strong>{step.title}</strong>
                    {ok ? <UiTag tone="success">通过</UiTag> : required ? <UiTag tone="warning">待满足</UiTag> : <UiTag tone="muted">可选</UiTag>}
                  </div>
                  <div className="ui-step-body">
                    <UiText secondary style={{ fontSize: 12, marginBottom: 6 }}>{step.description}</UiText>
                    {step.autoCheck && step.check && (
                      <div className={`ui-alert ui-alert-${ok ? 'success' : required ? 'warning' : 'info'}`}>
                        {ok ? '检查通过' : (
                          <>
                            <div>{required ? '等待条件满足' : '暂不阻塞'}</div>
                            {i === 0 && !ok && hardwareReasons.length > 0 && (
                              <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12 }}>
                                {hardwareReasons.map((reason) => (
                                  <li key={reason}>{reason}</li>
                                ))}
                              </ul>
                            )}
                          </>
                        )}
                      </div>
                    )}
                    {!step.autoCheck && (
                      <>
                        <label className="ui-checkbox">
                          <input
                            type="checkbox"
                            disabled={recordSession.returnOriginInFlight || !requiredResetSides(recordSession).every((side) => recordSession.resetReturnedSides.includes(side))}
                            checked={manualChecked[i] ?? false}
                            onChange={(event) =>
                              setManualChecked((prev) => ({ ...prev, [i]: event.target.checked }))
                            }
                          />
                          已完成
                        </label>
                        {step.actionButton && !returnReady && (
                          <div className="ui-alert ui-alert-warning" style={{ marginTop: 6 }} role="status" aria-label="回工作原点受阻原因">
                            <div>回工作原点按钮暂不可用，需先获得到位确认：</div>
                            <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12 }}>
                              {returnBlockReasons.map((reason) => (
                                <li key={reason}>{reason}</li>
                              ))}
                            </ul>
                            <UiButton
                              style={{ marginTop: 6 }}
                              onClick={goManualSettings}
                              aria-label="去设置页使能"
                            >
                              去设置使能
                            </UiButton>
                          </div>
                        )}
                      </>
                    )}
                    {i === 2 && (
                      <UiButton style={{ marginTop: 6 }} onClick={() => { handleClose(); navigate('/settings#safety') }}>
                        前往力觉自检
                      </UiButton>
                    )}
                    {step.actionButton && (
                      <UiButton
                        style={{ marginTop: 6 }}
                        disabled={recordSession.returnOriginInFlight || (step.actionButton.disabled?.(frame, recordSession) ?? false)}
                        loading={recordSession.returnOriginInFlight}
                        onClick={homeRecordArms}
                      >
                        {step.actionButton.label}
                      </UiButton>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
        <div className="ui-modal-actions">
          <UiButton onClick={handleClose}>取消</UiButton>
          <UiButton variant="primary" disabled={!allDone} onClick={handleConfirm}>
            确认开始
          </UiButton>
        </div>
      </div>
    </div>
  )
}
