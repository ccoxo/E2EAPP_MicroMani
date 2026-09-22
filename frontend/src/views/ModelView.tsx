/*
 * 阅读导航 01｜入口与界面
 * 职责：展示、导入和启停策略模型，调用后端模型接口。
 * 先看：ModelView。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { Bot, FolderOpen, PlayCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchModels, importModelApi, mockMode, startModelApi, stopModelApi, type PolicyModelApi } from '../api'
import { UiButton, UiProgress, UiSegmented, UiSpace, UiTag, UiText, UiTitle } from '../components/ui'

const fallbackModels: PolicyModelApi[] = [
  { id: 'act', name: 'ACT', latencyMs: 32, status: 'ready', note: 'local baseline policy', updatedAt: 0 },
  { id: 'diffusion_policy', name: 'Diffusion Policy', latencyMs: 108, status: 'ready', note: 'async policy', updatedAt: 0 },
  { id: 'smolvla', name: 'OpenVLA / SmolVLA', latencyMs: 146, status: 'not_loaded', note: 'checkpoint pending', updatedAt: 0 },
]

function statusTone(status: string) {
  if (status === 'running') return 'success' as const
  if (status === 'ready') return 'processing' as const
  return 'warning' as const
}
/** 渲染当前界面单元，并连接所需数据。 */
export function ModelView() {
  const [models, setModels] = useState<PolicyModelApi[]>(mockMode ? fallbackModels : [])
  const [activeModelId, setActiveModelId] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const requestId = useRef(0)
  const pendingRef = useRef<string | null>(null)
  const loading = pending !== null
  const [precisionByModel, setPrecisionByModel] = useState<Record<string, string>>({})

 /** 从后端读取对应数据。 */
  useEffect(() => {
    const id = ++requestId.current
    void fetchModels().then((result) => {
      if (requestId.current !== id) return
      setModels(mockMode && !result.models.length ? fallbackModels : result.models)
      setActiveModelId(result.activeModelId)
    }).catch((failure) => {
      if (requestId.current === id) setError(`模型列表读取失败：${String(failure)}`)
    })
    return () => {
      requestId.current += 1
    }
  }, [])

  const runCommand = async (key: string, command?: () => Promise<unknown>) => {
    // 停止可打断在途启动；旧响应失去列表回写及继续刷新资格。
    if (pendingRef.current === key || (pendingRef.current && !key.startsWith('stop:'))) return
    const id = ++requestId.current
    pendingRef.current = key
    setPending(key)
    setError('')
    try {
      if (command) await command()
      if (requestId.current !== id) return
      const result = await fetchModels()
      if (requestId.current !== id) return
      setModels(mockMode && !result.models.length ? fallbackModels : result.models)
      setActiveModelId(result.activeModelId)
    } catch (failure) {
      if (requestId.current === id) setError(`模型操作失败：${String(failure)}`)
    } finally {
      if (requestId.current === id) {
        pendingRef.current = null
        setPending(null)
      }
    }
  }

  const startModel = (modelId: string) => { void runCommand(`start:${modelId}`, () => startModelApi(modelId)) }
  const stopModel = (modelId: string) => { void runCommand(`stop:${modelId}`, () => stopModelApi(modelId)) }

  return (
    <div className="view-stack">
      <section className="page-header">
        <div>
          <UiTitle level={2}>模型 Model</UiTitle>
          <UiText secondary>管理可执行策略、推理服务和 VLA 任务模板。</UiText>
        </div>
        <UiSpace>
          <UiButton variant="primary" loading={loading} icon={<FolderOpen size={16} />} onClick={() => void runCommand('import', () => importModelApi('local_checkpoint'))}>
            导入 checkpoint
          </UiButton>
          <UiButton loading={loading} disabled={!models.length} icon={<PlayCircle size={16} />} onClick={() => startModel(activeModelId || models[0]?.id || 'act')}>
            启动服务
          </UiButton>
        </UiSpace>
      </section>
      {error && <div role="alert" className="ui-alert ui-alert-error">{error}<UiButton disabled={loading} onClick={() => void runCommand('refresh')}>重试</UiButton></div>}
      <section className="model-grid">
        {models.map((model) => (
          <div className="panel-surface model-tile" key={model.id}>
            <div className="section-title">
              <span><Bot size={17} /> {model.name}</span>
              <UiTag tone={statusTone(model.status)}>{model.status}</UiTag>
            </div>
            <UiText secondary>{model.note}</UiText>
            <UiProgress
              percent={Math.min(100, Math.max(0, model.latencyMs))}
              format={() => `${model.latencyMs || 0}ms`}
            />
            <UiSegmented
              value={(precisionByModel[model.id] ?? 'fp16') as 'fp32' | 'fp16' | 'int8'}
              options={[
                { label: 'FP32', value: 'fp32' as const },
                { label: 'FP16', value: 'fp16' as const },
                { label: 'INT8', value: 'int8' as const },
              ]}
              onChange={(value) => setPrecisionByModel((current) => ({ ...current, [model.id]: value }))}
            />
            <UiSpace>
              <UiButton disabled={loading} onClick={() => startModel(model.id)}>Start</UiButton>
              <UiButton loading={pending === `stop:${model.id}`} onClick={() => stopModel(model.id)}>Stop</UiButton>
              {activeModelId === model.id ? <UiTag tone="success">active</UiTag> : null}
            </UiSpace>
          </div>
        ))}
      </section>
    </div>
  )
}
