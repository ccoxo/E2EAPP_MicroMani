/*
 * 阅读导航 01｜入口与界面
 * 职责：提供全局急停交互并调用状态仓库中的急停操作。
 * 先看：GlobalEmergencyStopButton。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { RotateCcw, ShieldAlert } from 'lucide-react'
import { useTelemetryStore } from '../stores/telemetry'
import { canAcknowledgeControlSafety } from '../utils/controlSafety'
import { UiButton, UiTooltip } from './ui'
/** 渲染当前界面单元，并连接所需数据。 */
export function GlobalEmergencyStopButton() {
  const safetyLatched = useTelemetryStore((state) => Boolean(state.frame.forceStatus?.safety?.latched))
  const safety = useTelemetryStore((state) => state.controlSafety)
  const canAcknowledge = useTelemetryStore(canAcknowledgeControlSafety)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const acknowledgeSafety = useTelemetryStore((state) => state.acknowledgeSafety)
  const active = safetyLatched || safety.emergencyRequested
  const status = safety.emergencyError ? '急停未确认'
    : safety.emergencyPending ? '急停发送中'
      : safety.acknowledging ? '等待安全反馈'
        : active ? '安全锁定' : '急停'

  return (
    <div className={`floating-emergency-stack ${active ? 'floating-emergency-stack-active' : ''}`}>
      <UiTooltip title="硬件急停">
        <UiButton
          aria-label="全局急停"
          className={`floating-emergency-stop ${active ? 'floating-emergency-stop-active' : ''}`}
          danger
          icon={<ShieldAlert size={24} />}
          onClick={triggerEmergencyStop}
        >
          <span className="floating-emergency-copy">
            <strong>{status}</strong>
            <small>{active ? '可再次急停' : '硬件'}</small>
          </span>
        </UiButton>
      </UiTooltip>
      {active && (
        <UiTooltip title="只确认安全态，不恢复运动">
          <UiButton
            aria-label="确认安全态"
            className="floating-safety-reset"
            icon={<RotateCcw size={16} />}
            disabled={!canAcknowledge}
            onClick={acknowledgeSafety}
          >
            确认安全态
          </UiButton>
        </UiTooltip>
      )}
      {safety.emergencyError && <div role="alert" className="floating-safety-error">{safety.emergencyError}。请使用实体急停并检查设备。</div>}
    </div>
  )
}
