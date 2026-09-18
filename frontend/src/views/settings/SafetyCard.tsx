/*
 * 安全链路 / 急停 / 软限位设置卡；与其它硬件卡同等密度。
 * 先看：SafetyCard。
 */
import { ShieldAlert } from 'lucide-react'
import { ForceStartupSelfCheck } from '../../components/ForceStartupSelfCheck'
import { UiButton, UiField, UiNumber, UiSpace, UiSwitch, UiTag } from '../../components/ui'
import { useTelemetryStore } from '../../stores/telemetry'
import { canAcknowledgeControlSafety } from '../../utils/controlSafety'
import type { AppConfig } from '../../types'
import { HardwareConfigCard, MetricBox } from './shared'

export function SafetyCard({
  config,
  updateConfig,
  focusHash,
  triggerEmergencyStop,
  acknowledgeSafety,
}: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  triggerEmergencyStop: () => void
  acknowledgeSafety: () => void
}) {
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const safetyLatched = useTelemetryStore((state) => Boolean(state.frame.forceStatus?.safety?.latched))
  const latchReason = useTelemetryStore((state) => state.frame.forceStatus?.safety?.reason ?? '')
  const canAck = useTelemetryStore(canAcknowledgeControlSafety)
  const emergencyRequested = useTelemetryStore((state) => state.controlSafety.emergencyRequested)
  const complianceEnabled = config.force.compliance.enabled
  const leftConfirmed = config.force.compliance.left.mappingConfirmed
  const rightConfirmed = config.force.compliance.right.mappingConfirmed
  const mappingsConfirmed = leftConfirmed && rightConfirmed

  const state = emergencyRequested || safetyLatched || dangerIndex >= 1 ? 'error' : dangerIndex > 0.7 ? 'warn' : 'ok'
  const updateSafety = (patch: Partial<AppConfig['safety']>) =>
    updateConfig({ safety: { ...config.safety, ...patch } })

  return (
    <HardwareConfigCard
      id="safety"
      focusHash={focusHash}
      icon={<ShieldAlert size={20} />}
      title="安全链路 / 急停 / 软限位"
      subtitle="HAL 侧力锁存 · 软限位拦截 · Watchdog"
      state={state}
      badges={
        <>
          <UiTag tone={dangerIndex >= 1 ? 'error' : dangerIndex > 0.7 ? 'warning' : 'success'}>
            danger {dangerIndex.toFixed(2)}
          </UiTag>
          <UiTag tone={safetyLatched || emergencyRequested ? 'error' : 'muted'}>{safetyLatched || emergencyRequested ? 'LOCK' : '未锁存'}</UiTag>
          {complianceEnabled && (
            <UiTag tone="processing">位置导纳开</UiTag>
          )}
        </>
      }
      actions={
        <UiSpace wrap>
          <UiButton danger icon={<ShieldAlert size={15} />} onClick={triggerEmergencyStop}>
            急停
          </UiButton>
          <UiButton disabled={!canAck} onClick={acknowledgeSafety}>
            确认安全态
          </UiButton>
        </UiSpace>
      }
      wide
    >
      {config.force.source === 'hkvl_serial' && <ForceStartupSelfCheck />}
      <div className="hardware-metric-grid">
        <MetricBox label="danger_index" value={dangerIndex.toFixed(3)} tone={dangerIndex > 0.7 ? 'warn' : 'ok'} />
        <MetricBox label="安全锁存" value={safetyLatched ? '已锁存' : '未锁存'} tone={safetyLatched ? 'warn' : 'ok'} />
        <MetricBox label="锁存原因" value={latchReason || '—'} hint={safetyLatched ? '需确认后才能恢复运动' : undefined} />
        <MetricBox label="Fx/Fy 警告 N" value={config.safety.fxyWarnN.toFixed(1)} />
        <MetricBox label="Fx/Fy 急停 N" value={config.safety.fxyStopN.toFixed(1)} hint="暂按量程" />
        <MetricBox label="Fz 警告 N" value={config.safety.fzWarnN.toFixed(1)} />
        <MetricBox label="Fz 急停 N" value={config.safety.fzStopN.toFixed(1)} hint="暂按量程" />
        <MetricBox label="Moment 警告 Nm" value={config.safety.momentWarnNm.toFixed(3)} />
        <MetricBox label="Moment 急停 Nm" value={config.safety.momentStopNm.toFixed(3)} hint="暂按量程" />
        <MetricBox label="Yaw 软限位 °" value={config.safety.yawSoftLimitDeg.toFixed(1)} />
        <MetricBox label="Watchdog ms" value={String(config.safety.watchdogMs)} hint="HAL 命令超时" />
        <MetricBox label="导纳映射确认" value={mappingsConfirmed ? '双侧已确认' : '待确认'} tone={mappingsConfirmed ? 'ok' : 'warn'} />
      </div>

      <div className="hardware-form-grid hardware-form-grid-compact ui-form">
        <UiField label="Fx/Fy 警告 N">
          <UiNumber min={0} step={0.1} value={config.safety.fxyWarnN} onChange={(value) => updateSafety({ fxyWarnN: Number(value ?? 2) })} />
        </UiField>
        <UiField label="Fx/Fy 急停 N（暂按量程）">
          <UiNumber min={0} step={0.1} value={config.safety.fxyStopN} onChange={(value) => updateSafety({ fxyStopN: Number(value ?? 30) })} />
        </UiField>
        <UiField label="Fz 警告 N">
          <UiNumber min={0} step={0.1} value={config.safety.fzWarnN} onChange={(value) => updateSafety({ fzWarnN: Number(value ?? 3) })} />
        </UiField>
        <UiField label="Fz 急停 N（暂按量程）">
          <UiNumber min={0} step={0.1} value={config.safety.fzStopN} onChange={(value) => updateSafety({ fzStopN: Number(value ?? 30) })} />
        </UiField>
        <UiField label="Moment 警告 Nm">
          <UiNumber min={0} step={0.001} value={config.safety.momentWarnNm} onChange={(value) => updateSafety({ momentWarnNm: Number(value ?? 0.02) })} />
        </UiField>
        <UiField label="Moment 急停 Nm">
          <UiNumber min={0} step={0.001} value={config.safety.momentStopNm} onChange={(value) => updateSafety({ momentStopNm: Number(value ?? 1) })} />
        </UiField>
        <UiField label="Yaw 软限位 °">
          <UiNumber min={0} max={90} step={0.5} value={config.safety.yawSoftLimitDeg} onChange={(value) => updateSafety({ yawSoftLimitDeg: Number(value ?? 7) })} />
        </UiField>
        <UiField label="Watchdog ms">
          <UiNumber min={10} max={500} value={config.safety.watchdogMs} onChange={(value) => updateSafety({ watchdogMs: Number(value ?? 50) })} />
        </UiField>
      </div>

      <div className="gripper-config-section" style={{ marginTop: 12 }}>
        <div className="hardware-subtitle-row">
          <b>位置导纳（X/Z）</b>
          <span>未确认映射时默认关闭；开启后依赖力方向标定</span>
        </div>
        <div className="hardware-form-grid hardware-form-grid-compact ui-form">
          <UiField label="启用 X/Z 顺应">
            <UiSwitch
              checked={complianceEnabled}
              checkedChildren="开"
              unCheckedChildren="关"
              disabled={!mappingsConfirmed && !complianceEnabled}
              onChange={(checked) =>
                updateConfig({
                  force: {
                    ...config.force,
                    compliance: { ...config.force.compliance, enabled: checked },
                  },
                })
              }
            />
          </UiField>
          <UiField label="左臂映射已确认">
            <UiSwitch
              checked={leftConfirmed}
              checkedChildren="是"
              unCheckedChildren="否"
              disabled={complianceEnabled}
              onChange={(checked) =>
                updateConfig({
                  force: {
                    ...config.force,
                    compliance: {
                      ...config.force.compliance,
                      left: { ...config.force.compliance.left, mappingConfirmed: checked },
                    },
                  },
                })
              }
            />
          </UiField>
          <UiField label="右臂映射已确认">
            <UiSwitch
              checked={rightConfirmed}
              checkedChildren="是"
              unCheckedChildren="否"
              disabled={complianceEnabled}
              onChange={(checked) =>
                updateConfig({
                  force: {
                    ...config.force,
                    compliance: {
                      ...config.force.compliance,
                      right: { ...config.force.compliance.right, mappingConfirmed: checked },
                    },
                  },
                })
              }
            />
          </UiField>
        </div>
        {complianceEnabled && (
          <UiTag tone="warning">位置导纳已开启；急停与软限位仍由 HAL 拦截</UiTag>
        )}
        {!mappingsConfirmed && !complianceEnabled && (
          <UiTag tone="muted">需双侧映射确认后才能打开位置导纳</UiTag>
        )}
      </div>

      <div className="gripper-config-section" style={{ marginTop: 12 }}>
        <div className="hardware-subtitle-row">
          <b>防护层级</b>
          <span>不会恢复伺服时的分层说明</span>
        </div>
        <div className="hardware-metric-grid">
          <MetricBox label="Layer 1" value="力阈值 + Watchdog" hint="HAL 实时线程" />
          <MetricBox label="Layer 2" value="轴软限位" hint="工作原点相对" />
          <MetricBox label="Layer 3" value="软限位 <1ms" hint="Motion Thread 内拦截" />
        </div>
        <UiTag tone="warning" className="safety-layer-note">left Fz stop threshold 超限后不会恢复伺服，需人工确认安全态</UiTag>
      </div>
    </HardwareConfigCard>
  )
}
