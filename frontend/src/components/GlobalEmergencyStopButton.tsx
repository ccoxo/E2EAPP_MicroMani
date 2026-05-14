import { Button, Tooltip } from 'antd'
import { RotateCcw, ShieldAlert } from 'lucide-react'
import { useTelemetryStore } from '../stores/telemetry'

export function GlobalEmergencyStopButton() {
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const acknowledgeSafety = useTelemetryStore((state) => state.acknowledgeSafety)
  const active = dangerIndex >= 1

  return (
    <div className={`floating-emergency-stack ${active ? 'floating-emergency-stack-active' : ''}`}>
      <Tooltip title="硬件急停，快捷键 F12" placement="left">
        <Button
          aria-label="全局急停 F12"
          className={`floating-emergency-stop ${active ? 'floating-emergency-stop-active' : ''}`}
          danger
          icon={<ShieldAlert size={24} />}
          onClick={triggerEmergencyStop}
          type="primary"
        >
          <span className="floating-emergency-copy">
            <strong>{active ? '已急停' : '急停'}</strong>
            <small>F12</small>
          </span>
        </Button>
      </Tooltip>
      {active && (
        <Tooltip title="只确认安全态，不恢复运动" placement="left">
          <Button
            aria-label="确认安全复位"
            className="floating-safety-reset"
            icon={<RotateCcw size={16} />}
            onClick={acknowledgeSafety}
          >
            确认复位
          </Button>
        </Tooltip>
      )}
    </div>
  )
}
