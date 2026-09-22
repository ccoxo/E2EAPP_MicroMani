/*
 * 阅读导航 01｜入口与界面
 * 职责：提供微调任务参数输入、任务列表和取消操作。
 * 先看：FineTuneView。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { PauseCircle, PlayCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cancelFineTuneJobApi, fetchFineTuneJobs, startFineTuneJobApi, type FineTuneJobApi } from '../api'
import { UiButton, UiSpace, UiTag, UiText, UiTitle } from '../components/ui'

function jobTone(status: string) {
  if (status === 'running') return 'processing' as const
  if (status === 'done' || status === 'succeeded') return 'success' as const
  if (status === 'failed' || status === 'error') return 'error' as const
  return 'muted' as const
}
/** 渲染当前界面单元，并连接所需数据。 */
export function FineTuneView() {
  const [datasetId, setDatasetId] = useState('micro_assembly_v1')
  const [baseModel, setBaseModel] = useState('act')
  const [jobs, setJobs] = useState<FineTuneJobApi[]>([])
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const requestId = useRef(0)
  const pendingRef = useRef<string | null>(null)
  const loading = pending !== null

 /** 从后端读取对应数据。 */
  useEffect(() => {
    const id = ++requestId.current
    void fetchFineTuneJobs().then((items) => {
      if (requestId.current === id) setJobs(items)
    }).catch((failure) => {
      if (requestId.current === id) setError(`微调任务读取失败：${String(failure)}`)
    })
    return () => {
      requestId.current += 1
    }
  }, [])

  const runCommand = async (key: string, command?: () => Promise<unknown>) => {
    if (pendingRef.current === key || (pendingRef.current && !key.startsWith('cancel:'))) return
    const id = ++requestId.current
    pendingRef.current = key
    setPending(key)
    setError('')
    try {
      if (command) await command()
      if (requestId.current !== id) return
      const items = await fetchFineTuneJobs()
      if (requestId.current === id) setJobs(items)
    } catch (failure) {
      if (requestId.current === id) setError(`微调操作失败：${String(failure)}`)
    } finally {
      if (requestId.current === id) {
        pendingRef.current = null
        setPending(null)
      }
    }
  }

  const startJob = () => { void runCommand('create', () => startFineTuneJobApi(datasetId, baseModel)) }
  const cancelJob = (jobId: string) => { void runCommand(`cancel:${jobId}`, () => cancelFineTuneJobApi(jobId)) }

  return (
    <div className="view-stack">
      <section className="page-header">
        <div>
          <UiTitle level={2}>微调 Fine-tune</UiTitle>
          <UiText secondary>创建 LeRobot 数据集到策略模型的本地训练计划。</UiText>
        </div>
        <UiSpace wrap>
          <input
            className="ui-input"
            value={datasetId}
            onChange={(event) => setDatasetId(event.target.value)}
            placeholder="dataset id"
          />
          <input
            className="ui-input"
            value={baseModel}
            onChange={(event) => setBaseModel(event.target.value)}
            placeholder="base model"
          />
          <UiButton variant="primary" loading={loading} icon={<PlayCircle size={16} />} onClick={startJob}>
            创建任务
          </UiButton>
        </UiSpace>
      </section>
      {error && <div role="alert" className="ui-alert ui-alert-error">{error}<UiButton disabled={loading} onClick={() => void runCommand('refresh')}>重试</UiButton></div>}

      <section className="panel-surface">
        <table className="ui-table">
          <thead>
            <tr>
              <th>任务</th>
              <th>数据集</th>
              <th>基模型</th>
              <th>状态</th>
              <th>输出目录</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <div className="empty">暂无微调任务</div>
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id}>
                  <td>{job.id}</td>
                  <td>{job.datasetId}</td>
                  <td>{job.baseModel}</td>
                  <td><UiTag tone={jobTone(job.status)}>{job.status}</UiTag></td>
                  <td>{job.outputDir}</td>
                  <td>
                    <UiButton loading={pending === `cancel:${job.id}`} icon={<PauseCircle size={14} />} onClick={() => cancelJob(job.id)}>
                      Cancel
                    </UiButton>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>
    </div>
  )
}
