import { Card, Tag } from 'antd'
import { memo, type CSSProperties } from 'react'
import { AxisGroupChart, ForceChart } from '../Charts'
import { useTelemetryStore } from '../../stores/telemetry'
import type { TelemetrySample } from '../../types'

const axes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw'] as const
const forceLabels = ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'] as const

function formatAxisValue(value: number, index: number) {
  const unit = index < 3 ? 'um' : 'deg'
  const display = index < 3 ? Math.round(value) : value.toFixed(1)
  return `${value >= 0 ? '+' : ''}${display} ${unit}`
}

function axisRatio(value: number, index: number) {
  const max = index < 3 ? 700 : index === 5 ? 90 : 110
  return Math.min(100, Math.abs(value) / max * 100)
}

function forceRatio(value: number, index: number) {
  const max = index < 3 ? 5 : 0.04
  return Math.min(100, Math.abs(value) / max * 100)
}

function forceTone(value: number, index: number) {
  const abs = Math.abs(value)
  if (index < 3) {
    if (abs >= 4) return 'danger'
    if (abs >= 2) return 'warn'
  } else {
    if (abs >= 0.04) return 'danger'
    if (abs >= 0.02) return 'warn'
  }
  return 'ok'
}

function poseStyle(values: number[]) {
  const x = Math.max(-42, Math.min(42, (values[0] ?? 0) / 14))
  const y = Math.max(-32, Math.min(32, (values[1] ?? 0) / 16))
  const yaw = Math.max(-70, Math.min(70, values[5] ?? 0))
  return {
    '--pose-x': `${x}px`,
    '--pose-y': `${-y}px`,
    '--pose-rotate': `${yaw}deg`,
  } as CSSProperties
}

const ArmMonitor = memo(function ArmMonitor({
  side,
  title,
  positions,
  axisOffset,
  force,
  history,
}: {
  side: 'left' | 'right'
  title: string
  positions: number[]
  axisOffset: number
  force: number[]
  history: TelemetrySample[]
}) {
  const forcePeak = Math.max(...force.map((value) => Math.abs(value)))
  const valueAt = (index: number) => positions[axisOffset + index] ?? 0
  const poseValues = [
    valueAt(0),
    valueAt(1),
    valueAt(2),
    valueAt(3),
    valueAt(4),
    valueAt(5),
  ]
  const yaw = valueAt(5)
  const dangerTone = forcePeak >= 4 || Math.abs(yaw) >= 70 ? 'danger' : forcePeak >= 2 || Math.abs(yaw) >= 60 ? 'warn' : 'ok'

  return (
    <section className={`record-arm-monitor record-arm-monitor-${dangerTone}`}>
      <div className="record-arm-monitor-head">
        <div>
          <b>{title}</b>
          <span>运动轨迹 / 力觉实时趋势</span>
        </div>
        <Tag color={dangerTone === 'danger' ? 'error' : dangerTone === 'warn' ? 'warning' : 'success'}>
          {dangerTone === 'danger' ? '风险' : dangerTone === 'warn' ? '注意' : '平稳'}
        </Tag>
      </div>

      <div className="record-arm-monitor-body">
        <div className="record-arm-visual-stack">
          <div className="record-arm-pose">
            <div className="record-arm-pose-grid" />
            <div className="record-arm-pose-crosshair" />
            <div className="record-arm-pose-dot" style={poseStyle(poseValues)}>
              <span />
            </div>
            <div className="record-arm-pose-label">
              X/Y {formatAxisValue(valueAt(0), 0)} / {formatAxisValue(valueAt(1), 1)}
            </div>
          </div>

          <div className="record-axis-meter-list">
            {axes.map((axis, index) => {
              const value = valueAt(index)
              return (
                <div className="record-axis-meter" key={axis}>
                  <span>{axis}</span>
                  <div>
                    <i style={{ width: `${axisRatio(value, index)}%` }} />
                  </div>
                  <b>{formatAxisValue(value, index)}</b>
                </div>
              )
            })}
          </div>
        </div>

        <div className="record-arm-chart-stack">
          <div className="record-mini-chart">
            <AxisGroupChart history={history} side={side} group="translation" height={96} />
          </div>
          <div className="record-mini-chart">
            <AxisGroupChart history={history} side={side} group="rotation" height={96} />
          </div>
        </div>

        <div className="record-force-monitor">
          <div className="record-force-meter-grid">
            {forceLabels.map((label, index) => {
              const value = force[index] ?? 0
              return (
                <div className={`record-force-meter record-force-meter-${forceTone(value, index)}`} key={label}>
                  <span>{label}</span>
                  <b>{value >= 0 ? '+' : ''}{value.toFixed(index < 3 ? 2 : 3)}</b>
                  <div><i style={{ width: `${forceRatio(value, index)}%` }} /></div>
                </div>
              )
            })}
          </div>
          <div className="record-force-chart">
            <ForceChart history={history} side={side} height={126} />
          </div>
        </div>
      </div>
    </section>
  )
})

export default function RecordTelemetryPanel() {
  const positions = useTelemetryStore((state) => state.frame.jointPositions)
  const forceLeft = useTelemetryStore((state) => state.frame.forceLeft)
  const forceRight = useTelemetryStore((state) => state.frame.forceRight)
  const history = useTelemetryStore((state) => state.history)

  return (
    <Card size="small" title="运动与力觉监看" styles={{ body: { padding: 8 } }}>
      <div className="record-telemetry-grid">
        <ArmMonitor side="left" title="左臂" positions={positions} axisOffset={0} force={forceLeft} history={history} />
        <ArmMonitor side="right" title="右臂" positions={positions} axisOffset={6} force={forceRight} history={history} />
      </div>
    </Card>
  )
}
