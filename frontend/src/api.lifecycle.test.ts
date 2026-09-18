/*
 * 阅读导航 07｜测试与验证
 * 职责：验证页面隐藏时只发送一次运行资源释放请求。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('runtime lifecycle release', () => {
  it('sends release_handles once when the page is hidden', async () => {
    vi.resetModules()
    vi.stubEnv('MODE', 'development')
    vi.stubEnv('VITE_API_BASE', 'http://backend.test')
    const sendBeacon = vi.fn((url: string | URL, data?: BodyInit | null) => {
      void url
      void data
      return true
    })
    Object.defineProperty(window.navigator, 'sendBeacon', {
      configurable: true,
      value: sendBeacon,
    })

    const { installRuntimeReleaseOnClose } = await import('./api')

    installRuntimeReleaseOnClose()
    window.dispatchEvent(new Event('pagehide'))
    window.dispatchEvent(new Event('beforeunload'))

    expect(sendBeacon).toHaveBeenCalledTimes(1)
    const [url, payload] = sendBeacon.mock.calls[0] as [string, Blob]
    expect(url).toBe('http://backend.test/api/runtime/release_handles')
    await expect(payload.text()).resolves.toContain('browser-close')
  })
})
