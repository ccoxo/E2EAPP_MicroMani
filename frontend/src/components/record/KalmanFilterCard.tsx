import { Card, InputNumber, Switch, Tag } from 'antd'
import { useTelemetryStore } from '../../stores/telemetry'
import type { AppConfig } from '../../types'

type TeleopConfig = AppConfig['teleop']
type NumericTeleopKey = {
  [K in keyof TeleopConfig]: TeleopConfig[K] extends number ? K : never
}[keyof TeleopConfig]

interface KalmanParam {
  key: NumericTeleopKey
  label: string
  min: number
  step: number
}

const globalParams: KalmanParam[] = [
  { key: 'kalmanBeta', label: '遗忘因子 beta', min: 0, step: 0.01 },
  { key: 'kalmanMinVariance', label: '方差下限', min: 1e-15, step: 1e-12 },
  { key: 'kalmanMaxVariance', label: '方差上限', min: 1e-12, step: 1 },
  { key: 'kalmanDtMinSec', label: 'dt 下限', min: 0.0001, step: 0.001 },
  { key: 'kalmanDtMaxSec', label: 'dt 上限', min: 0.0001, step: 0.001 },
]

const translationParams: KalmanParam[] = [
  { key: 'kalmanTranslationPositionVariance', label: '平移位置 P', min: 1e-15, step: 1e-8 },
  { key: 'kalmanTranslationVelocityVariance', label: '平移速度 V', min: 1e-15, step: 1e-4 },
  { key: 'kalmanTranslationMeasurementVariance', label: '平移测量方差 R', min: 1e-15, step: 1e-8 },
  { key: 'kalmanTranslationProcessPositionVariance', label: '平移过程方差 Qp', min: 1e-15, step: 1e-10 },
  { key: 'kalmanTranslationProcessVelocityVariance', label: '平移过程方差 Qv', min: 1e-15, step: 1e-8 },
  { key: 'kalmanTranslationIntentVelocityThreshold', label: '平移阈值 v_th', min: 1e-12, step: 0.0001 },
]

const rotationParams: KalmanParam[] = [
  { key: 'kalmanRotationPositionVariance', label: '旋转位置 P', min: 1e-15, step: 0.01 },
  { key: 'kalmanRotationVelocityVariance', label: '旋转速度 V', min: 1e-15, step: 0.1 },
  { key: 'kalmanRotationMeasurementVariance', label: '旋转测量方差 R', min: 1e-15, step: 0.01 },
  { key: 'kalmanRotationProcessPositionVariance', label: '旋转过程方差 Qp', min: 1e-15, step: 1e-4 },
  { key: 'kalmanRotationProcessVelocityVariance', label: '旋转过程方差 Qv', min: 1e-15, step: 1e-4 },
  { key: 'kalmanRotationIntentVelocityThreshold', label: '旋转阈值 v_th', min: 1e-12, step: 0.1 },
]

function formatValue(value: number) {
  if (Math.abs(value) > 0 && Math.abs(value) < 0.001) return value.toExponential(1)
  return Number.isInteger(value) ? String(value) : value.toPrecision(3)
}

export default function KalmanFilterCard() {
  const config = useTelemetryStore((state) => state.config)
  const updateConfig = useTelemetryStore((state) => state.updateConfig)
  const teleop = config.teleop

  const updateTeleop = (patch: Partial<TeleopConfig>) => {
    updateConfig({ teleop: { ...teleop, ...patch } })
  }

  const renderParam = (param: KalmanParam) => (
    <label className="record-kalman-param" key={param.key}>
      <span>{param.label}</span>
      <InputNumber
        aria-label={param.label}
        min={param.min}
        step={param.step}
        value={teleop[param.key]}
        onChange={(value) => updateTeleop({ [param.key]: Number(value ?? teleop[param.key]) } as Partial<TeleopConfig>)}
      />
    </label>
  )

  return (
    <Card
      size="small"
      title="卡尔曼滤波"
      extra={<Tag color={teleop.kalmanFilterEnabled ? 'processing' : 'default'}>{teleop.kalmanFilterEnabled ? '开启' : '关闭'}</Tag>}
    >
      <div className="record-kalman-head">
        <span></span>
        <Switch
          aria-label="卡尔曼滤波开关"
          checked={teleop.kalmanFilterEnabled}
          onChange={(checked) => updateTeleop({ kalmanFilterEnabled: checked })}
        />
      </div>

      <div className="record-kalman-groups">
        <section>
          <b>全局</b>
          <div className="record-kalman-param-grid">{globalParams.map(renderParam)}</div>
        </section>
        <section>
          <b>平移</b>
          <div className="record-kalman-param-grid">{translationParams.map(renderParam)}</div>
        </section>
        <section>
          <b>旋转</b>
          <div className="record-kalman-param-grid">{rotationParams.map(renderParam)}</div>
        </section>
      </div>

      <div className="record-kalman-summary">
        <span>beta {formatValue(teleop.kalmanBeta)}</span>
        <span>dt {formatValue(teleop.kalmanDtMinSec)}-{formatValue(teleop.kalmanDtMaxSec)}s</span>
      </div>
    </Card>
  )
}
