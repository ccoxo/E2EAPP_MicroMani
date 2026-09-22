import { useTelemetryStore } from '../stores/telemetry'
import { controlLeaseBlockReason } from '../stores/controlLease'
import { UiButton } from './ui'

/** 租约由后端维护执行侧链路，不表示解除急停或已经使能。 */
export function ControlLeaseStatus() {
  const lease = useTelemetryStore((state) => state.controlLease)
  const safetyLatched = useTelemetryStore((state) => Boolean(state.frame.forceStatus?.safety?.latched))
  if (!lease.required) return null
  const reason = controlLeaseBlockReason(lease)
  if (!reason && !safetyLatched) return null
  return (
    <div className="ui-alert ui-alert-warning" role="status" aria-label="控制安全租约">
      {reason || '控制租约已确认；硬件仍处于安全锁定，请核对设备后显式确认解除。'}
      {lease.status === 'expired' && <UiButton onClick={() => {
        const store = useTelemetryStore.getState()
        store.stopBackend()
        store.startBackend()
      }}>重新连接并核验</UiButton>}
    </div>
  )
}
