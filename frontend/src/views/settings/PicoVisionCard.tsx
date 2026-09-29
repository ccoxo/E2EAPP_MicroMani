/*
 * PICO-4 视觉推流设置卡；与其它硬件卡同等密度。
 * 先看：PicoVisionCard。
 */
import { Camera, Network, Play, PlugZap, RefreshCw, Square } from 'lucide-react'
import { useState } from 'react'
import {
  checkPicoStatus,
  connectPicoAdb,
  startPicoVision,
  stopPicoVision,
} from '../../api'
import {
  UiButton,
  UiField,
  UiInput,
  UiNumber,
  UiSelect,
  UiSpace,
  UiTag,
  UiText,
} from '../../components/ui'
import { useTelemetryStore } from '../../stores/telemetry'
import type { AppConfig, LogEntry } from '../../types'
import { HardwareConfigCard, MetricBox } from './shared'
import { commandLog } from './sharedHelpers'

const rotationOptions = [
  { value: 'none', label: '不旋转' },
  { value: 'cw90', label: '顺时针 90°' },
  { value: 'ccw90', label: '逆时针 90°' },
  { value: '180', label: '180°' },
]

const cameraSourceOptions = [
  { value: 'global', label: '全局相机' },
  { value: 'wrist_left', label: '左腕相机' },
  { value: 'wrist_right', label: '右腕相机' },
]

export function PicoVisionCard({
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
  const picoConnection = useTelemetryStore((state) => state.picoConnection)
  const picoNetworkInfo = useTelemetryStore((state) => state.picoNetworkInfo)
  const setPicoConnectionStatus = useTelemetryStore((state) => state.setPicoConnectionStatus)
  const autoConfigurePicoNetwork = useTelemetryStore((state) => state.autoConfigurePicoNetwork)
  const [busy, setBusy] = useState<'adb' | 'net' | 'check' | 'start' | 'stop' | null>(null)

  const pico = config.picoVision
  const updatePico = (patch: Partial<AppConfig['picoVision']>) =>
    updateConfig({ picoVision: { ...pico, ...patch } })

  const connectionState = picoConnection.state
  const cardState =
    connectionState === 'ok' ? 'ok' : connectionState === 'warn' ? 'warn' : connectionState === 'error' ? 'error' : 'pending'

  const run = async (key: NonNullable<typeof busy>, fn: () => Promise<unknown>) => {
    setBusy(key)
    try {
      await fn()
    } catch (error) {
      injectLog('ERROR', `PICO 命令失败: ${error instanceof Error ? error.message : String(error)}`, '[CAMERA]')
    } finally {
      setBusy(null)
    }
  }

  return (
    <HardwareConfigCard
      id="teleop"
      focusHash={focusHash}
      icon={<Camera size={20} />}
      title="PICO-4 视觉推流"
      subtitle={`${pico.ip}:${pico.adbPort} · video ${pico.videoPort} · cmd ${pico.commandPort}`}
      state={cardState}
      badges={
        <>
          <UiTag tone="processing">IF {pico.ifIndex}</UiTag>
          <UiTag tone={connectionState === 'ok' ? 'success' : connectionState === 'error' ? 'error' : 'muted'}>
            ADB {connectionState === 'ok' ? '已连接' : connectionState === 'error' ? '离线' : '待检查'}
          </UiTag>
          <UiTag tone="processing">IMX335 / index 1</UiTag>
        </>
      }
      actions={
        <UiSpace wrap>
          <UiButton
            icon={<PlugZap size={15} />}
            loading={busy === 'adb'}
            onClick={() =>
              void run('adb', async () => {
                const result = await connectPicoAdb()
                const ok = result.data?.ok ?? result.ok
                setPicoConnectionStatus(ok ? 'ok' : 'error', result.data?.message ?? (ok ? 'ADB 已连接' : 'ADB 连接失败'))
                commandLog(injectLog, '[CAMERA]', ok ? '无线 ADB 已连接' : '无线 ADB 连接失败')
              })
            }
          >
            连接无线 ADB
          </UiButton>
          <UiButton
            icon={<RefreshCw size={15} />}
            loading={busy === 'net'}
            onClick={() =>
              void run('net', async () => {
                await autoConfigurePicoNetwork(pico.ip)
                commandLog(injectLog, '[CAMERA]', 'PICO 网口自动识别完成')
              })
            }
          >
            重新识别网口
          </UiButton>
          <UiButton
            loading={busy === 'check'}
            onClick={() =>
              void run('check', async () => {
                const result = await checkPicoStatus()
                const ok = result.data?.ok ?? result.ok
                setPicoConnectionStatus(ok ? 'ok' : 'error', result.data?.message ?? (ok ? '设备在线' : '设备离线'))
                commandLog(injectLog, '[CAMERA]', ok ? 'PICO 状态检查：在线' : 'PICO 状态检查：离线')
              })
            }
          >
            检查状态
          </UiButton>
          <UiButton
            variant="primary"
            icon={<Play size={15} />}
            loading={busy === 'start'}
            onClick={() =>
              void run('start', async () => {
                const result = await startPicoVision()
                commandLog(injectLog, '[CAMERA]', result.data?.message ?? '视觉推流已启动')
              })
            }
          >
            启动视觉
          </UiButton>
          <UiButton
            icon={<Square size={15} />}
            loading={busy === 'stop'}
            onClick={() =>
              void run('stop', async () => {
                const result = await stopPicoVision()
                commandLog(injectLog, '[CAMERA]', result.data?.message ?? '视觉推流已停止')
              })
            }
          >
            停止视觉
          </UiButton>
        </UiSpace>
      }
      wide
    >
      <div className="pico-status-strip" data-testid="pico-status-strip">
        <b>{pico.ip}:{pico.adbPort}</b>
        <span>IF {pico.ifIndex}</span>
        <span>IMX335 / index 1</span>
        <UiText secondary>{picoConnection.message || '尚未检查 PICO ADB'}</UiText>
      </div>
      <div className="hardware-metric-grid">
        <MetricBox label="PICO IP" value={pico.ip} />
        <MetricBox label="网关" value={pico.gateway} />
        <MetricBox label="网卡 IF" value={String(pico.ifIndex)} />
        <MetricBox label="ADB 端口" value={String(pico.adbPort)} />
        <MetricBox label="视频端口" value={String(pico.videoPort)} />
        <MetricBox label="命令端口" value={String(pico.commandPort)} />
        <MetricBox label="旋转" value={rotationOptions.find((r) => r.value === pico.rotation)?.label ?? pico.rotation} />
        <MetricBox label="相机源" value={cameraSourceOptions.find((c) => c.value === pico.cameraSource)?.label ?? pico.cameraSource} />
        <MetricBox
          label="自动配网结果"
          value={picoNetworkInfo ? `${picoNetworkInfo.localIp} → ${picoNetworkInfo.gateway}` : '未执行'}
          hint={picoNetworkInfo?.selection}
        />
      </div>
      <div className="hardware-form-grid hardware-form-grid-compact ui-form">
        <UiField label="PICO IP">
          <UiInput value={pico.ip} onChange={(event) => updatePico({ ip: event.target.value })} />
        </UiField>
        <UiField label="网关">
          <UiInput value={pico.gateway} onChange={(event) => updatePico({ gateway: event.target.value })} />
        </UiField>
        <UiField label="网卡 IF Index">
          <UiNumber min={0} value={pico.ifIndex} onChange={(value) => updatePico({ ifIndex: Number(value ?? 0) })} />
        </UiField>
        <UiField label="ADB 端口">
          <UiNumber min={1} max={65535} value={pico.adbPort} onChange={(value) => updatePico({ adbPort: Number(value ?? 5555) })} />
        </UiField>
        <UiField label="视频端口">
          <UiNumber min={1} max={65535} value={pico.videoPort} onChange={(value) => updatePico({ videoPort: Number(value ?? 12345) })} />
        </UiField>
        <UiField label="命令端口">
          <UiNumber min={1} max={65535} value={pico.commandPort} onChange={(value) => updatePico({ commandPort: Number(value ?? 13579) })} />
        </UiField>
        <UiField label="画面旋转">
          <UiSelect
            value={pico.rotation}
            options={rotationOptions}
            onChange={(value) => updatePico({ rotation: value as AppConfig['picoVision']['rotation'] })}
          />
        </UiField>
        <UiField label="相机源">
          <UiSelect
            value={pico.cameraSource}
            options={cameraSourceOptions}
            onChange={(value) => updatePico({ cameraSource: value as AppConfig['picoVision']['cameraSource'] })}
          />
        </UiField>
      </div>
      <div style={{ marginTop: 8 }}>
        <UiButton
          icon={<Network size={14} />}
          loading={busy === 'net'}
          onClick={() =>
            void run('net', () => autoConfigurePicoNetwork(pico.ip))
          }
        >
          按当前 IP 自动配网
        </UiButton>
      </div>
    </HardwareConfigCard>
  )
}
