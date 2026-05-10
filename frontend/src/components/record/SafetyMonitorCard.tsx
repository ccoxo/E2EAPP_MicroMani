import { Card } from 'antd'
import { useTelemetryStore } from '../../stores/telemetry'

const getDangerColor = (d: number) => {
  if (d < 0.3) return '#3B6D11'
  if (d < 0.5) return '#7cb305'
  if (d < 0.7) return '#d48806'
  if (d < 1.0) return '#E65100'
  return '#cf1322'
}

interface DangerBarProps {
  side: '左' | '右'
  danger: number
}

function DangerBar({ side, danger }: DangerBarProps) {
  const pct = Math.min((danger / 1.5) * 100, 100)
  const color = getDangerColor(danger)
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 2 }}>
        <span style={{ color: '#8c8c8c' }}>{side}臂危险度</span>
        <span style={{ fontFamily: 'monospace', color }}>{danger.toFixed(3)}</span>
      </div>
      <div style={{ height: 8, background: '#eef2f6', borderRadius: 4, overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            width: `${pct}%`,
            background: color,
            borderRadius: 4,
            transition: 'width 0.15s ease',
          }}
        />
      </div>
    </div>
  )
}

interface ForceValueSummaryProps {
  forceLeft: number[]
  forceRight: number[]
}

function ForceValueSummary({ forceLeft, forceRight }: ForceValueSummaryProps) {
  const fzLeft = forceLeft[2] ?? 0
  const fzRight = forceRight[2] ?? 0
  return (
    <div style={{ marginTop: 6, display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11 }}>
      <span style={{ color: '#8c8c8c' }}>
        Fz 左 <span style={{ fontFamily: 'monospace' }}>{fzLeft >= 0 ? '+' : ''}{fzLeft.toFixed(2)} N</span>
      </span>
      <span style={{ color: '#8c8c8c' }}>
        Fz 右 <span style={{ fontFamily: 'monospace' }}>{fzRight >= 0 ? '+' : ''}{fzRight.toFixed(2)} N</span>
      </span>
    </div>
  )
}

function forceDanger(values: number[]) {
  const thresholds = [4, 4, 5, 0.04, 0.04, 0.04]
  return values.reduce((max, value, index) => Math.max(max, Math.abs(value) / (thresholds[index] ?? 1)), 0)
}

export default function SafetyMonitorCard() {
  const dangerIndex = useTelemetryStore((s) => s.frame.dangerIndex)
  const forceLeft = useTelemetryStore((s) => s.frame.forceLeft)
  const forceRight = useTelemetryStore((s) => s.frame.forceRight)
  const dangerLeft = Math.max(dangerIndex * 0.82, forceDanger(forceLeft))
  const dangerRight = Math.max(dangerIndex * 0.78, forceDanger(forceRight))

  return (
    <Card title="力觉安全监控" size="small">
      <DangerBar side="左" danger={dangerLeft} />
      <DangerBar side="右" danger={dangerRight} />
      <div style={{ fontSize: 10, color: '#8c8c8c', marginTop: 4 }}>
        D&gt;=1.0 自动急停 / 警告阈值 0.5
      </div>
      <ForceValueSummary forceLeft={forceLeft} forceRight={forceRight} />
    </Card>
  )
}
