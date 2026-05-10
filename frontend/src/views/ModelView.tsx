import { Button, Progress, Radio, Space, Tag, Typography } from 'antd'
import { Bot, FolderOpen, PlayCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchModels, importModelApi, startModelApi, stopModelApi, type PolicyModelApi } from '../api'

const fallbackModels: PolicyModelApi[] = [
  { id: 'act', name: 'ACT', latencyMs: 32, status: 'ready', note: 'local baseline policy', updatedAt: 0 },
  { id: 'diffusion_policy', name: 'Diffusion Policy', latencyMs: 108, status: 'ready', note: 'async policy', updatedAt: 0 },
  { id: 'smolvla', name: 'OpenVLA / SmolVLA', latencyMs: 146, status: 'not_loaded', note: 'checkpoint pending', updatedAt: 0 },
]

export function ModelView() {
  const [models, setModels] = useState<PolicyModelApi[]>(fallbackModels)
  const [activeModelId, setActiveModelId] = useState('')
  const [loading, setLoading] = useState(false)

  const refresh = () => {
    setLoading(true)
    void fetchModels()
      .then((result) => {
        if (result.models.length) setModels(result.models)
        setActiveModelId(result.activeModelId)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    let cancelled = false
    void fetchModels().then((result) => {
      if (cancelled) return
      if (result.models.length) setModels(result.models)
      setActiveModelId(result.activeModelId)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const startModel = (modelId: string) => {
    setLoading(true)
    void startModelApi(modelId).then(refresh).finally(() => setLoading(false))
  }

  const stopModel = (modelId: string) => {
    setLoading(true)
    void stopModelApi(modelId).then(refresh).finally(() => setLoading(false))
  }

  return (
    <div className="view-stack">
      <section className="page-header">
        <div>
          <Typography.Title level={2}>模型 Model</Typography.Title>
          <Typography.Text type="secondary">管理可执行策略、推理服务和 VLA 任务模板。</Typography.Text>
        </div>
        <Space>
          <Button type="primary" loading={loading} icon={<FolderOpen size={16} />} onClick={() => void importModelApi('local_checkpoint').then(refresh)}>
            导入 checkpoint
          </Button>
          <Button loading={loading} icon={<PlayCircle size={16} />} onClick={() => startModel(activeModelId || models[0]?.id || 'act')}>
            启动服务
          </Button>
        </Space>
      </section>
      <section className="model-grid">
        {models.map((model) => (
          <div className="panel-surface model-tile" key={model.id}>
            <div className="section-title">
              <span><Bot size={17} /> {model.name}</span>
              <Tag color={model.status === 'running' ? 'success' : model.status === 'ready' ? 'processing' : 'warning'}>{model.status}</Tag>
            </div>
            <Typography.Text type="secondary">{model.note}</Typography.Text>
            <Progress percent={Math.min(100, Math.max(0, model.latencyMs))} format={() => `${model.latencyMs || 0}ms`} />
            <Radio.Group defaultValue="fp16" optionType="button">
              <Radio.Button value="fp32">FP32</Radio.Button>
              <Radio.Button value="fp16">FP16</Radio.Button>
              <Radio.Button value="int8">INT8</Radio.Button>
            </Radio.Group>
            <Space>
              <Button size="small" onClick={() => startModel(model.id)}>Start</Button>
              <Button size="small" onClick={() => stopModel(model.id)}>Stop</Button>
              {activeModelId === model.id ? <Tag color="green">active</Tag> : null}
            </Space>
          </div>
        ))}
      </section>
    </div>
  )
}
