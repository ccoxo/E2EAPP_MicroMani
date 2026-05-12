import { Alert, Button, Checkbox, Modal, Steps } from 'antd'
import { useState } from 'react'
import { homeAll } from '../../api'
import { useTelemetryStore } from '../../stores/telemetry'
import type { RecordSessionState, TelemetryFrame } from '../../types'

interface StepDef {
  title: string
  description: string
  autoCheck: boolean
  check: ((frame: TelemetryFrame, recordSession: RecordSessionState) => boolean) | null
  required?: boolean
  actionButton?: { label: string; apiCall: () => void }
}

const STEPS: StepDef[] = [
  {
    title: '硬件连接',
    description: '确认 HAL、WebSocket 正常，三路相机在线且帧率高于 25Hz。',
    autoCheck: true,
    check: (frame) =>
      frame.halOk &&
      frame.wsOk &&
      frame.cameras.every((camera) => camera.fps >= 25 && camera.health === 'ok'),
  },
  {
    title: '自动回到工作原点',
    description: '点击自动回零，将左右从臂移动到已采集的工作原点；确认停止后勾选完成。',
    autoCheck: false,
    check: null,
    actionButton: { label: '自动回零', apiCall: () => { void homeAll().catch(() => undefined) } },
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

export default function PreCheckModal({ open, onConfirm, onCancel }: PreCheckModalProps) {
  const frame = useTelemetryStore((s) => s.frame)
  const recordSession = useTelemetryStore((s) => s.recordSession)
  const tareRecordForceSensors = useTelemetryStore((s) => s.tareRecordForceSensors)
  const [manualChecked, setManualChecked] = useState<Record<number, boolean>>({})

  const stepStatuses = STEPS.map((step, i) => {
    if (step.autoCheck && step.check) {
      return step.check(frame, recordSession)
    }
    return manualChecked[i] ?? false
  })

  const allDone = STEPS.every((step, i) => step.required === false || stepStatuses[i])
  const currentStep = STEPS.findIndex((step, i) => step.required !== false && !stepStatuses[i])
  const activeStep = currentStep === -1 ? STEPS.length : currentStep

  const handleClose = () => {
    setManualChecked({})
    onCancel()
  }

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
                  <Button size="small" style={{ marginTop: 6 }} onClick={step.actionButton.apiCall}>
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
