import { Alert, Button, Checkbox, Modal, Steps } from 'antd'
import { useEffect, useState } from 'react'
import { motionSideReturnOriginReady } from '../../motionReturnReady'
import { useTelemetryStore } from '../../stores/telemetry'
import type { DiagnosticItem, RecordSessionState, TelemetryFrame, TelemetryLinkStatus } from '../../types'

interface StepDef {
  title: string
  description: string
  autoCheck: boolean
  check: ((
    frame: TelemetryFrame,
    recordSession: RecordSessionState,
    diagnostics: DiagnosticItem[],
    telemetryLink: TelemetryLinkStatus,
  ) => boolean) | null
  required?: boolean
  actionButton?: {
    label: string
    disabled?: (frame: TelemetryFrame, recordSession: RecordSessionState) => boolean
  }
}
/** 计算对应的业务值或展示值。 */
function diagnosticReady(diagnostics: DiagnosticItem[], key: string) {
  return diagnostics.find((item) => item.key === key)?.status === 'ok'
}
/** 计算对应的业务值或展示值。 */
function teleopHandsReady(frame: TelemetryFrame) {
  const requiredHands = frame.teleopHands.filter(
    (hand) => !hand.message.toLowerCase().includes('logical teleop hand disconnected'),
  )
  return requiredHands.length > 0 && requiredHands.every((hand) => hand.connected && hand.lastReadOk)
}
function requiredResetSides(recordSession: RecordSessionState) {
  return recordSession.resetRequiredSides.length > 0 ? recordSession.resetRequiredSides : ['left' as const]
}
function requiredMotionReturnReady(frame: TelemetryFrame, recordSession: RecordSessionState) {
  return requiredResetSides(recordSession).every((side) =>
    motionSideReturnOriginReady(side, frame.motionEnabled, frame.motionAxisEnabled),
  )
}
function cameraWarnings(frame: TelemetryFrame) {
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
    title: '力觉 Tare',
    description: '当前现场暂不具备条件，此项仅保留操作入口，不阻塞开始采集。',
    autoCheck: true,
    required: false,
    check: (frame, recordSession) =>
      recordSession.forceTareActive &&
      Math.abs(frame.forceLeft[2] ?? 0) < 0.1 &&
      Math.abs(frame.forceRight[2] ?? 0) < 0.1,
  },
  {
    title: '验证力觉示数',
    description: '当前现场暂不具备条件，此项仅作为参考，不阻塞开始采集。',
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
  const frame = useTelemetryStore((s) => s.frame)
  const diagnostics = useTelemetryStore((s) => s.diagnostics)
  const telemetryLink = useTelemetryStore((s) => s.telemetryLink)
  const recordSession = useTelemetryStore((s) => s.recordSession)
  const tareRecordForceSensors = useTelemetryStore((s) => s.tareRecordForceSensors)
  const homeRecordArms = useTelemetryStore((s) => s.homeRecordArms)
  const refreshHardwareStatus = useTelemetryStore((s) => s.refreshHardwareStatus)
  const [manualChecked, setManualChecked] = useState<Record<number, boolean>>({})

  useEffect(() => {
    if (open) void refreshHardwareStatus()
  }, [open, refreshHardwareStatus])

  const stepStatuses = STEPS.map((step, i) => {
    if (step.autoCheck && step.check) {
      return step.check(frame, recordSession, diagnostics, telemetryLink)
    }
    return manualChecked[i] ?? false
  })
  const warnings = cameraWarnings(frame)

  const allDone = STEPS.every((step, i) => step.required === false || stepStatuses[i])
  const currentStep = STEPS.findIndex((step, i) => step.required !== false && !stepStatuses[i])
  const activeStep = currentStep === -1 ? STEPS.length : currentStep

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

  return (
    <Modal
      title="采集会话开始前硬件检查"
      open={open}
      closable={false}
      maskClosable={false}
      width={500}
      footer={[
        <Button key="cancel" onClick={handleClose}>
          取消
        </Button>,
        <Button key="confirm" type="primary" disabled={!allDone} onClick={handleConfirm}>
          确认开始
        </Button>,
      ]}
    >
      {warnings.length > 0 && (
        <Alert
          type="warning"
          message={warnings.join(' · ')}
          style={{ marginBottom: 8, padding: '2px 8px', fontSize: 11 }}
          showIcon={false}
        />
      )}
      <Steps
        direction="vertical"
        size="small"
        current={activeStep}
        style={{ marginTop: 8 }}
        items={STEPS.map((step, i) => {
          const ok = stepStatuses[i]
          const required = step.required !== false
          return {
            title: step.title,
            description: (
              <div style={{ paddingBottom: 8 }}>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginBottom: 6 }}>
                  {step.description}
                </div>

                {step.autoCheck && step.check && (
                  <Alert
                    type={ok ? 'success' : required ? 'warning' : 'info'}
                    message={ok ? '检查通过' : required ? '等待条件满足' : '暂不阻塞'}
                    style={{ padding: '2px 8px', fontSize: 11 }}
                    showIcon={false}
                  />
                )}

                {!step.autoCheck && (
                  <Checkbox
                    checked={manualChecked[i] ?? false}
                    onChange={(e) =>
                      setManualChecked((prev) => ({ ...prev, [i]: e.target.checked }))
                    }
                  >
                    已完成
                  </Checkbox>
                )}

                {i === 2 && (
                  <Button size="small" style={{ marginTop: 6 }} onClick={tareRecordForceSensors}>
                    执行 Tare
                  </Button>
                )}

                {step.actionButton && (
                  <Button
                    size="small"
                    style={{ marginTop: 6 }}
                    disabled={recordSession.returnOriginInFlight || (step.actionButton.disabled?.(frame, recordSession) ?? false)}
                    loading={recordSession.returnOriginInFlight}
                    onClick={homeRecordArms}
                  >
                    {step.actionButton.label}
                  </Button>
                )}
              </div>
            ),
            status: ok ? 'finish' : i === activeStep ? 'process' : 'wait',
          }
        })}
      />
    </Modal>
  )
}
