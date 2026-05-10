import { Alert, Button, Checkbox, Modal, Steps } from 'antd'
import { useState } from 'react'
import { useTelemetryStore } from '../../stores/telemetry'
import type { RecordSessionState, TelemetryFrame } from '../../types'

interface StepDef {
  title: string
  description: string
  autoCheck: boolean
  check: ((frame: TelemetryFrame, recordSession: RecordSessionState) => boolean) | null
  actionButton?: { label: string; apiCall: () => void }
}

const STEPS: StepDef[] = [
  {
    title: '硬件连接',
    description: '确认所有硬件指示灯为绿色，相机帧率稳定在 30Hz 附近。',
    autoCheck: true,
    check: (frame) =>
      frame.halOk &&
      frame.wsOk &&
      frame.cameras.every((camera) => camera.fps >= 28 && camera.health === 'ok'),
  },
  {
    title: '移动到工作原点',
    description: '通过 Jog Panel 或遥操作将双臂移动到工作原点，并保持静止。',
    autoCheck: false,
    check: null,
  },
  {
    title: '力觉 Tare',
    description: '点击“执行 Tare”，等待静止状态下零力标定完成，约 3 秒。',
    autoCheck: true,
    check: (frame, recordSession) =>
      recordSession.forceTareActive &&
      Math.abs(frame.forceLeft[2] ?? 0) < 0.1 &&
      Math.abs(frame.forceRight[2] ?? 0) < 0.1,
  },
  {
    title: '验证力觉示数',
    description: '静止时所有分量应接近 0，确认无异常。',
    autoCheck: true,
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

  const allDone = stepStatuses.every(Boolean)
  const currentStep = stepStatuses.findIndex((ok) => !ok)
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
          return {
            title: step.title,
            description: (
              <div style={{ paddingBottom: 8 }}>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginBottom: 6 }}>
                  {step.description}
                </div>

                {step.autoCheck && step.check && (
                  <Alert
                    type={ok ? 'success' : 'warning'}
                    message={ok ? '检查通过' : '等待条件满足'}
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
