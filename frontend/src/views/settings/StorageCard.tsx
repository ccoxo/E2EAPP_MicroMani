import { FolderOpen } from 'lucide-react'
import { UiField, UiInput, UiNumber, UiSwitch, UiTag } from '../../components/ui'
import type { AppConfig } from '../../types'
import { HardwareConfigCard, MetricBox } from './shared'

/** 数据目录与保存帧率独立于相机预览设置。 */
export function StorageCard({ config, updateConfig, focusHash }: {
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
}) {
  const recordFps = config.storage.recordFps ?? config.cameras.fps
  const updateStorage = (patch: Partial<AppConfig['storage']>) =>
    updateConfig({ storage: { ...config.storage, ...patch } })

  return (
    <HardwareConfigCard
      id="storage"
      focusHash={focusHash}
      icon={<FolderOpen size={20} />}
      title="数据存储"
      subtitle="录制完成的数据集写入目录"
      state="ok"
      badges={<UiTag tone="processing">Dataset Root</UiTag>}
      wide
    >
      <div className="hardware-metric-grid">
        <MetricBox label="当前目录" value={config.storage.datasetRoot} hint="支持绝对路径 / ~ 用户目录" />
        <MetricBox label="录制 FPS" value={recordFps} hint="数据集保存帧率" />
        <MetricBox label="录制力反馈" value={config.storage.recordForce ? '启用' : '关闭'} hint="ACT 不需要时建议关闭" />
        <MetricBox label="视频 CRF" value={config.storage.videoCrf} />
        <MetricBox label="Hub 上传" value={config.storage.pushToHub ? '启用' : '关闭'} />
      </div>
      <div className="hardware-form-grid ui-form">
        <UiField label="数据集根目录">
          <UiInput value={config.storage.datasetRoot} onChange={(event) => updateStorage({ datasetRoot: event.target.value })} />
        </UiField>
        <UiField label="录制 FPS" tooltip="数据集保存帧率；与相机采集目标 FPS 分开设置。">
          <UiNumber min={1} max={60} step={1} value={recordFps} onChange={(value) => updateStorage({ recordFps: Math.max(1, Math.min(60, Math.round(value ?? 30))) })} />
        </UiField>
        <UiField label="录制力反馈" tooltip="关闭后不采样、不等待力反馈，也不把力反馈作为录制质量门槛。">
          <UiSwitch checked={config.storage.recordForce} checkedChildren="启用" unCheckedChildren="关闭" onChange={(value) => updateStorage({ recordForce: value })} />
        </UiField>
      </div>
    </HardwareConfigCard>
  )
}
