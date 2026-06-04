import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createRoot: vi.fn(),
  render: vi.fn(),
  installRuntimeReleaseOnClose: vi.fn(),
  installAutoShutdownOnClose: vi.fn(),
}))

vi.mock('react-dom/client', () => ({
  createRoot: mocks.createRoot,
}))

vi.mock('./api', () => ({
  installRuntimeReleaseOnClose: mocks.installRuntimeReleaseOnClose,
  installAutoShutdownOnClose: mocks.installAutoShutdownOnClose,
}))

vi.mock('./App.tsx', () => ({
  default: () => null,
}))

describe('frontend bootstrap runtime lifecycle', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    document.body.innerHTML = '<div id="root"></div>'
    mocks.createRoot.mockReturnValue({ render: mocks.render })
  })

  it('installs pagehide runtime release listener', async () => {
    await import('./main')

    expect(mocks.installRuntimeReleaseOnClose).toHaveBeenCalledTimes(1)
    expect(mocks.installAutoShutdownOnClose).not.toHaveBeenCalled()
  })
})
