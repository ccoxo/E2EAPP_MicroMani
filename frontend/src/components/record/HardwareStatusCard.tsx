import { Card } from 'antd'
import { deriveHardwareStatusRows, telemetryLinkLabel, type HardwareStatusTone } from '../../hardwareStatus'
import { useTelemetryStore } from '../../stores/telemetry'

const toneColor: Record<HardwareStatusTone, string> = {
  ok: '#3B6D11',
  warn: '#E65100',
  error: '#C62828',
  unknown: '#8c8c8c',
}

function dotStyle(tone: HardwareStatusTone): React.CSSProperties {
  return {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: toneColor[tone],
    flexShrink: 0,
  }
}

export default function HardwareStatusCard() {
  const frame = useTelemetryStore((state) => state.frame)
  const config = useTelemetryStore((state) => state.config)
  const telemetryLink = useTelemetryStore((state) => state.telemetryLink)
  const rows = deriveHardwareStatusRows(frame, config, telemetryLink)
  const linkTone: HardwareStatusTone = telemetryLink.state === 'live'
    ? 'ok'
    : telemetryLink.state === 'offline'
      ? 'error'
      : 'unknown'

  return (
    <Card
      title="硬件状态"
      size="small"
      extra={(
        <span style={{ color: toneColor[linkTone], fontSize: 10 }}>
          {telemetryLinkLabel(telemetryLink)}
        </span>
      )}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {rows.map((row) => (
          <div
            key={row.key}
            data-hardware-key={row.key}
            data-hardware-tone={row.tone}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '2px 4px',
              fontSize: 11,
            }}
          >
            <div style={dotStyle(row.tone)} />
            <span style={{ flex: 1, color: toneColor[row.tone] }}>{row.name}</span>
            <span style={{ fontFamily: 'monospace', color: '#8c8c8c', fontSize: 10 }}>{row.value}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}
