/*
 * 阅读导航 07｜测试与验证
 * 职责：验证 React 启动时安装页面退出的资源释放监听。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
