import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.resetModules() })

it('录制冷启动超过十秒仍等待原请求，不撤权或重发', async () => {
  vi.resetModules()
  vi.stubEnv('MODE', 'development')
  vi.useFakeTimers()
  let finish!: (response: Response) => void
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve }))
  vi.stubGlobal('fetch', fetcher)
  const api = await import('./api')
  const revoke = vi.fn()
  api.installControlCommandTimeoutHandler(revoke)
  const pending = api.postCommand('/record/session/create', { dataset_name: 'cold-start', task: 'test' })
  const resolved = expect(pending).resolves.toMatchObject({ ok: true })
  await vi.advanceTimersByTimeAsync(15_000)
  expect(revoke).not.toHaveBeenCalled()
  expect(fetcher).toHaveBeenCalledOnce()
  const options = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1]
  expect(options.signal?.aborted).toBe(false)
  finish(new Response(JSON.stringify({ ok: true, data: { active: true } })))
  await resolved
  await vi.advanceTimersByTimeAsync(60_000)
  expect(revoke).not.toHaveBeenCalled()
})

it('录制初始化超过独立上限仍撤权且不自动重试', async () => {
  vi.resetModules()
  vi.stubEnv('MODE', 'development')
  vi.useFakeTimers()
  const fetcher = vi.fn(() => new Promise<Response>(() => undefined))
  vi.stubGlobal('fetch', fetcher)
  const api = await import('./api')
  const revoke = vi.fn()
  api.installControlCommandTimeoutHandler(revoke)
  const rejected = expect(api.postCommand('/api/record/session/create', { dataset_name: 'stalled', task: 'test' })).rejects.toThrow('执行结果未知')
  await vi.advanceTimersByTimeAsync(59_999)
  expect(revoke).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2)
  await rejected
  expect(revoke).toHaveBeenCalledOnce()
  expect(fetcher).toHaveBeenCalledOnce()
  const options = (fetcher.mock.calls[0] as unknown as [string, RequestInit])[1]
  expect(options.signal?.aborted).toBe(true)
})
