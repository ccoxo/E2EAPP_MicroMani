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
