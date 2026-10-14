import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.resetModules()
})

it.each([
  ['http:', '192.168.101.18', '', 'http://192.168.101.18:18082', 'ws://192.168.101.18:18082/ws'],
  ['http:', '127.0.0.1', '', 'http://127.0.0.1:18082', 'ws://127.0.0.1:18082/ws'],
  ['https:', 'appstation.local', '19082', 'https://appstation.local:19082', 'wss://appstation.local:19082/ws'],
])('页面地址 %s//%s 决定后端连接地址', async (protocol, hostname, port, expectedApi, expectedWs) => {
  vi.stubEnv('VITE_API_BASE', '')
  vi.stubEnv('VITE_WS_URL', '')
  vi.stubEnv('VITE_BACKEND_PORT', port)
  vi.stubGlobal('window', { location: { protocol, hostname } })
  const { apiBase, wsUrl } = await import('./api')
  expect(apiBase).toBe(expectedApi)
  expect(wsUrl).toBe(expectedWs)
})

it('保留显式 API 和 WebSocket 地址配置', async () => {
  vi.stubEnv('VITE_API_BASE', 'https://backend.example')
  vi.stubEnv('VITE_WS_URL', 'wss://telemetry.example/ws')
  const { apiBase, wsUrl } = await import('./api')
  expect(apiBase).toBe('https://backend.example')
  expect(wsUrl).toBe('wss://telemetry.example/ws')
})

it('WebSocket 默认跟随显式 API 地址', async () => {
  vi.stubEnv('VITE_API_BASE', 'https://backend.example:19082')
  vi.stubEnv('VITE_WS_URL', '')
  const { wsUrl } = await import('./api')
  expect(wsUrl).toBe('wss://backend.example:19082/ws')
})
