import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { ModelView } from './ModelView'
import { FineTuneView } from './FineTuneView'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}
const models = { models: [{ id: 'act', name: '服务模型', status: 'ready', latencyMs: 12, note: '', updatedAt: 0 }], activeModelId: '' }
const job = { id: 'job_1', datasetId: 'data', baseModel: 'act', status: 'running', outputDir: 'output' } as api.FineTuneJobApi

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe.each(['model', 'fineTune'] as const)('%s 请求生命周期', (kind) => {
  const title = kind === 'model' ? '模型' : '微调'
  const load = () => kind === 'model'
    ? vi.spyOn(api, 'fetchModels').mockResolvedValue(models)
    : vi.spyOn(api, 'fetchFineTuneJobs').mockResolvedValue([job])
  const mount = () => render(kind === 'model' ? <ModelView /> : <FineTuneView />)
  const createButton = () => screen.getByRole('button', { name: kind === 'model' ? '启动服务' : '创建任务' })

  it('初始网络失败显示可重试错误，不产生未处理拒绝', async () => {
    const fetchSpy = load().mockRejectedValueOnce(new Error('Failed to fetch'))
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to fetch')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('创建后等列表刷新结束才解除 loading，期间不重复创建', async () => {
    const fetchSpy = load()
    const command = kind === 'model' ? vi.spyOn(api, 'startModelApi').mockResolvedValue({ ok: true }) : vi.spyOn(api, 'startFineTuneJobApi').mockResolvedValue({ ok: true })
    mount()
    await screen.findByText(kind === 'model' ? '服务模型' : 'job_1')
    const refresh = deferred<unknown>()
    fetchSpy.mockImplementationOnce(() => refresh.promise as never)
    fireEvent.click(createButton())
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    expect(createButton()).toBeDisabled()
    fireEvent.click(createButton())
    expect(command).toHaveBeenCalledTimes(1)
    await act(async () => refresh.resolve(kind === 'model' ? models : [job]))
    expect(createButton()).toBeEnabled()
  })

  it('命令失败会显示错误并恢复可操作状态', async () => {
    load()
    if (kind === 'model') vi.spyOn(api, 'startModelApi').mockRejectedValue(new Error('服务拒绝'))
    else vi.spyOn(api, 'startFineTuneJobApi').mockRejectedValue(new Error('服务拒绝'))
    mount()
    await screen.findByText(kind === 'model' ? '服务模型' : 'job_1')
    fireEvent.click(createButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('服务拒绝')
    expect(createButton()).toBeEnabled()
  })

  it('切页卸载后迟到的命令成功不再续发列表读取', async () => {
    const fetchSpy = load()
    const command = deferred<unknown>()
    if (kind === 'model') vi.spyOn(api, 'startModelApi').mockReturnValue(command.promise)
    else vi.spyOn(api, 'startFineTuneJobApi').mockReturnValue(command.promise)
    const view = mount()
    await screen.findByText(kind === 'model' ? '服务模型' : 'job_1')
    fireEvent.click(createButton())
    view.unmount()
    await act(async () => command.resolve({ ok: true }))
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it(`旧初始${title}列表不能覆盖新命令后的列表`, async () => {
    const initial = deferred<unknown>()
    const fetchSpy = load().mockImplementationOnce(() => initial.promise as never)
    if (kind === 'model') vi.spyOn(api, 'importModelApi').mockResolvedValue({ ok: true })
    else vi.spyOn(api, 'startFineTuneJobApi').mockResolvedValue({ ok: true })
    mount()
    fireEvent.click(kind === 'model' ? screen.getByRole('button', { name: '导入 checkpoint' }) : createButton())
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
    await screen.findByText(kind === 'model' ? '服务模型' : 'job_1')
    await act(async () => initial.resolve(kind === 'model' ? { models: [], activeModelId: '' } : []))
    expect(screen.getByText(kind === 'model' ? '服务模型' : 'job_1')).toBeInTheDocument()
  })
})
