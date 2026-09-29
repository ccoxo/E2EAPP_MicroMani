/*
 * HAL 通信设置卡；与其它硬件卡同等密度。
 * 先看：HalCard。
 */
import { Network, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { reconnectHal } from '../../api'
import {
  UiButton,
  UiField,
  UiInput,
  UiNumber,
  UiSpace,
  UiSwitch,
  UiTag,
} from '../../components/ui'
import { useTelemetryStore } from '../../stores/telemetry'
import type { AppConfig, LogEntry } from '../../types'
import { HardwareConfigCard, MetricBox } from './shared'
import { commandLog } from './sharedHelpers'

export function HalCard({
  config,
  updateConfig,
  focusHash,
  injectLog,
}: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const halOk = useTelemetryStore((state) => state.frame.halOk)
  const wsOk = useTelemetryStore((state) => state.frame.wsOk)
  const wsHz = useTelemetryStore((state) => state.frame.resource.wsHz)
  const telemetryLink = useTelemetryStore((state) => state.telemetryLink)
  const [reconnecting, setReconnecting] = useState(false)

  const state = !halOk ? 'error' : !wsOk ? 'warn' : telemetryLink.state === 'live' ? 'ok' : 'pending'

  const handleReconnect = async () => {
    setReconnecting(true)
    try {
      await reconnectHal()
      commandLog(injectLog, '[HAL]', 'HAL 重连请求已发送')
    } catch (error) {
      injectLog('ERROR', `HAL 重连失败：${error instanceof Error ? error.message : String(error)}`, '[HAL]')
    } finally {
      setReconnecting(false)
    }
  }

  return (
    <HardwareConfigCard
      id="hal"
      focusHash={focusHash}
      icon={<Network size={20} />}
      title="HAL 通信"
      subtitle={`${config.hal.baseUrl} · ${config.hal.wsUrl}`}
      state={state}
      badges={
        <>
          <UiTag tone="processing">轴数 {config.hal.axisCount}</UiTag>
          <UiTag tone={config.hal.apiConfirmed ? 'success' : 'warning'}>
            {config.hal.apiConfirmed ? 'API 已确认' : 'API 待确认'}
          </UiTag>
          <UiTag tone={wsOk ? 'success' : 'error'}>WS {wsHz.toFixed(0)} Hz</UiTag>
        </>
      }
      actions={
        <UiSpace>
          <UiButton icon={<RefreshCw size={15} />} loading={reconnecting} onClick={() => void handleReconnect()}>
            重连 HAL
          </UiButton>
          <UiButton
            onClick={() => updateConfig({ hal: { ...config.hal, apiConfirmed: true } })}
            disabled={config.hal.apiConfirmed}
          >
            确认 API
          </UiButton>
        </UiSpace>
      }
      wide
    >
      <div className="hardware-metric-grid">
        <MetricBox label="HAL Service" value={halOk ? '在线' : '离线'} tone={halOk ? 'ok' : 'warn'} />
        <MetricBox label="WebSocket" value={wsOk ? `${wsHz.toFixed(0)} Hz` : '中断'} tone={wsOk ? 'ok' : 'warn'} />
        <MetricBox label="运动线程" value={`${config.motion.motionThreadHz} Hz`} hint="HalServer 控制环" />
        <MetricBox label="遥测新鲜度" value={telemetryLink.state} hint="UI 节流约 15Hz" />
        <MetricBox label="轴数" value={String(config.hal.axisCount)} hint="双臂 6+6" />
        <MetricBox label="API 确认" value={config.hal.apiConfirmed ? '已确认' : '待确认'} tone={config.hal.apiConfirmed ? 'ok' : 'warn'} />
      </div>
      <div className="hardware-form-grid hardware-form-grid-compact ui-form">
        <UiField label="HAL API 地址">
          <UiInput
            value={config.hal.baseUrl}
            onChange={(event) => updateConfig({ hal: { ...config.hal, baseUrl: event.target.value } })}
          />
        </UiField>
        <UiField label="遥测 WebSocket">
          <UiInput
            value={config.hal.wsUrl}
            onChange={(event) => updateConfig({ hal: { ...config.hal, wsUrl: event.target.value } })}
          />
        </UiField>
        <UiField label="轴数">
          <UiNumber
            min={1}
            max={24}
            value={config.hal.axisCount}
            onChange={(value) => updateConfig({ hal: { ...config.hal, axisCount: Number(value ?? 12) } })}
          />
        </UiField>
        <UiField label="运动线程 Hz">
          <UiNumber
            min={100}
            max={8000}
            value={config.motion.motionThreadHz}
            onChange={(value) => updateConfig({ motion: { ...config.motion, motionThreadHz: Number(value ?? 1000) } })}
          />
        </UiField>
        <UiField label="API 已确认">
          <UiSwitch
            checked={config.hal.apiConfirmed}
            checkedChildren="是"
            unCheckedChildren="否"
            onChange={(checked) => updateConfig({ hal: { ...config.hal, apiConfirmed: checked } })}
          />
        </UiField>
      </div>
    </HardwareConfigCard>
  )
}
