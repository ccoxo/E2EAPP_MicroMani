/*
 * 阅读导航 01｜入口与界面
 * 职责：展示由 hardwareStatus 推导的硬件连接行及遥测新鲜度。
 * 先看：dotStyle → HardwareStatusCard。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { deriveHardwareStatusRows, telemetryLinkLabel, type HardwareStatusTone } from '../../hardwareStatus'
import { hardwareStatusFrameEqual, hardwareStatusFrameSlice, useFrameField } from '../../stores/frameSelectors'
import { useTelemetryStore } from '../../stores/telemetry'
import { UiCard } from '../ui'

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
  // 只订阅派生所需切片；15Hz 整帧替换若内容等价则不重渲染。
  const frameSlice = useFrameField(hardwareStatusFrameSlice, hardwareStatusFrameEqual)
  const config = useTelemetryStore((state) => state.config)
  const telemetryLink = useTelemetryStore((state) => state.telemetryLink)
  const rows = deriveHardwareStatusRows(frameSlice, config, telemetryLink)
  const linkTone: HardwareStatusTone = telemetryLink.state === 'live'
    ? 'ok'
    : telemetryLink.state === 'offline'
      ? 'error'
      : 'unknown'

  return (
    <UiCard
      title="硬件状态"
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
    </UiCard>
  )
}
