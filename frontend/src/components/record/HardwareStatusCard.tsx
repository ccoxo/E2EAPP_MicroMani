import { Card } from 'antd'
import { useTelemetryStore } from '../../stores/telemetry'
import type { DiagnosticItem, TelemetryFrame } from '../../types'

interface HwItem {
  name: string
  getValue: (frame: TelemetryFrame, diagnostics: DiagnosticItem[]) => string
  getOk: (frame: TelemetryFrame, diagnostics: DiagnosticItem[]) => boolean
}

function diagnosticOk(diagnostics: DiagnosticItem[], key: string) {
  const status = diagnostics.find((item) => item.key === key)?.status
  return status === 'ok'
}

function cameraByKey(frame: TelemetryFrame, key: 'global' | 'wrist_left' | 'wrist_right') {
  return frame.cameras.find((camera) => camera.key === key)
}

function cameraOk(frame: TelemetryFrame, key: 'global' | 'wrist_left' | 'wrist_right') {
  const camera = cameraByKey(frame, key)
  return (camera?.fps ?? 0) >= 25 && camera?.health === 'ok'
}

const HW_ITEMS: HwItem[] = [
  {
    name: 'HAL Service',
    getValue: () => '8090',
    getOk: (frame) => frame.halOk,
  },
  {
    name: 'ATI 左臂',
    getValue: () => '模拟',
    getOk: (_frame, diagnostics) => diagnosticOk(diagnostics, 'ati-left'),
  },
  {
    name: 'ATI 右臂',
    getValue: () => '模拟',
    getOk: (_frame, diagnostics) => diagnosticOk(diagnostics, 'ati-right'),
  },
  {
    name: '相机 全局',
    getValue: (frame) => `${(cameraByKey(frame, 'global')?.fps ?? 0).toFixed(1)} Hz`,
    getOk: (frame) => cameraOk(frame, 'global'),
  },
  {
    name: '相机 左腕',
    getValue: (frame) => `${(cameraByKey(frame, 'wrist_left')?.fps ?? 0).toFixed(1)} Hz`,
    getOk: (frame) => cameraOk(frame, 'wrist_left'),
  },
  {
    name: '相机 右腕',
    getValue: (frame) => `${(cameraByKey(frame, 'wrist_right')?.fps ?? 0).toFixed(1)} Hz`,
    getOk: (frame) => cameraOk(frame, 'wrist_right'),
  },
  {
    name: 'Omega.7 左/右',
    getValue: (_frame, diagnostics) => diagnosticOk(diagnostics, 'omega7') ? '已连接' : '待确认',
    getOk: (_frame, diagnostics) => diagnosticOk(diagnostics, 'omega7'),
  },
  {
    name: '夹爪 左/右',
    getValue: (_frame, diagnostics) => diagnosticOk(diagnostics, 'gripper') ? 'RS485 OK' : '待确认',
    getOk: (_frame, diagnostics) => diagnosticOk(diagnostics, 'gripper'),
  },
]

const dotStyle = (ok: boolean): React.CSSProperties => ({
  width: 8,
  height: 8,
  borderRadius: '50%',
  background: ok ? '#3B6D11' : '#E65100',
  flexShrink: 0,
})

export default function HardwareStatusCard() {
  const frame = useTelemetryStore((s) => s.frame)
  const diagnostics = useTelemetryStore((s) => s.diagnostics)

  return (
    <Card title="硬件状态" size="small">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {HW_ITEMS.map((item) => {
          const ok = item.getOk(frame, diagnostics)
          const value = item.getValue(frame, diagnostics)
          return (
            <div
              key={item.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '2px 4px',
                fontSize: 11,
              }}
            >
              <div style={dotStyle(ok)} />
              <span style={{ flex: 1, color: ok ? 'inherit' : '#E65100' }}>{item.name}</span>
              <span style={{ fontFamily: 'monospace', color: '#8c8c8c', fontSize: 10 }}>{value}</span>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
