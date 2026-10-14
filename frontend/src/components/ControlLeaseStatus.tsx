import { useTelemetryStore } from '../stores/telemetry'
import { controlLeaseBlockReason } from '../stores/controlLease'
import { UiButton } from './ui'

/** 租约由后端维护执行侧链路，不表示解除急停或已经使能。 */
export function ControlLeaseStatus() {
  const lease = useTelemetryStore((state) => state.controlLease)
  const observerMode = useTelemetryStore((state) => state.observerMode)
  const setObserverMode = useTelemetryStore((state) => state.setObserverMode)
  const safetyLatched = useTelemetryStore((state) => Boolean(state.frame.forceStatus?.safety?.latched))
  if (!lease.required) return null
  const reason = controlLeaseBlockReason(lease)
  return (
    <div className="ui-alert ui-alert-warning" role="status" aria-label="控制安全租约">
      <span>{observerMode ? '观察模式' : lease.status === 'active' ? '控制模式' : '控制模式（未取得控制权）'}：{reason || (safetyLatched
        ? '控制租约已确认；硬件仍处于安全锁定，请核对设备后显式确认解除。'
        : '控制租约已确认。')}</span>
      <UiButton onClick={() => setObserverMode(!observerMode)}>
        {observerMode ? '申请控制' : '切换到观察模式'}
      </UiButton>
      <span>{observerMode ? '申请控制不会自动解除急停或使能。' : '切换观察模式将释放控制权，并触发现有断连安全停机保护。'}</span>
      {lease.status === 'expired' && <UiButton onClick={() => {
        const store = useTelemetryStore.getState()
        store.stopBackend()
        store.startBackend()
      }}>重新连接并核验</UiButton>}
    </div>
  )
}
