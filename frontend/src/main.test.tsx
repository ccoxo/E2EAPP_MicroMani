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

  it('does not install pagehide runtime lifecycle listeners', async () => {
    await import('./main')

    expect(mocks.installRuntimeReleaseOnClose).not.toHaveBeenCalled()
    expect(mocks.installAutoShutdownOnClose).not.toHaveBeenCalled()
  })
})
