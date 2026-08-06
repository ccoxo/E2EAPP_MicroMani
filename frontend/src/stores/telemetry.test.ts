import { describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { defaultConfig, defaultDiagnostics } from '../data'
import { diagnosticsFromHardwareStatus, normalizeConfig, useTelemetryStore } from './telemetry'

describe('telemetry config normalization', () => {
  it('migrates stale PICO and camera hardware defaults', () => {
    const staleConfig = structuredClone(defaultConfig)
    staleConfig.picoVision.ip = '10.90.132.51'
    staleConfig.cameras.global = 'AR0234 / index 1'
    staleConfig.cameras.wristLeft = 'IMX258 / index 2'
    staleConfig.cameras.wristRight = 'IMX258 / index 0'

    const normalized = normalizeConfig(staleConfig)

    expect(normalized.picoVision.ip).toBe('10.90.129.166')
    expect(normalized.cameras.global).toBe('IMX335 / index 1')
    expect(normalized.cameras.wristLeft).toBe('IMX335 / index 0')
    expect(normalized.cameras.wristRight).toBe('IMX335 / index 2')
  })

  it('migrates previous IMX335 wrist identity binding', () => {
    const staleConfig = structuredClone(defaultConfig)
    staleConfig.cameras.global = 'IMX335 / index 1'
    staleConfig.cameras.globalIdentity = 'USB\\VID_0ABD&PID_8050&MI_00\\7&124CCBA8&0&0000'
    staleConfig.cameras.wristLeft = 'IMX335 / index 2'
    staleConfig.cameras.wristLeftIdentity = 'USB\\VID_0ABD&PID_8050&MI_00\\7&7861A93&0&0000'
    staleConfig.cameras.wristRight = 'IMX335 / index 0'
    staleConfig.cameras.wristRightIdentity = 'USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000'

    const normalized = normalizeConfig(staleConfig)

    expect(normalized.cameras.globalIdentity).toBe('USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000')
    expect(normalized.cameras.wristLeft).toBe('IMX335 / index 0')
    expect(normalized.cameras.wristLeftIdentity).toBe('USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000')
    expect(normalized.cameras.wristRight).toBe('IMX335 / index 2')
    expect(normalized.cameras.wristRightIdentity).toBe('USB\\VID_0ABD&PID_8050&MI_00\\8&3724732E&0&0000')
  })
})

describe('hardware diagnostics', () => {
  it('warns when a newer HAL runtime binary is waiting for restart', () => {
    const diagnostics = diagnosticsFromHardwareStatus(defaultDiagnostics, {
      runtime: {
        halDeployment: {
          restartRequired: true,
          message: 'HalServer.next.exe differs from HalServer.exe',
          components: {
            HalServer: { pendingNext: true },
          },
        },
      },
    })

    expect(diagnostics.find((item) => item.key === 'hal-health')).toMatchObject({
      status: 'warn',
      remediation: 'HalServer.next.exe differs from HalServer.exe',
    })
  })

  it('warns when backend source changed after the process started', () => {
    const diagnostics = diagnosticsFromHardwareStatus(defaultDiagnostics, {
      runtime: {
        backendDeployment: {
          restartRequired: true,
          message: 'Backend source changed after process start; restart backend',
          latestPath: 'backend/app.py',
        },
      },
    } as any)

    expect(diagnostics.find((item) => item.key === 'hal-health')).toMatchObject({
      status: 'warn',
      remediation: 'Backend source changed after process start; restart backend',
    })
  })
})

describe('PICO network auto configuration', () => {
  it('replaces the local settings state with the backend-persisted detection result', async () => {
    const detectedConfig = structuredClone(defaultConfig)
    detectedConfig.picoVision.ip = '10.90.140.22'
    detectedConfig.picoVision.gateway = '10.90.0.1'
    detectedConfig.picoVision.ifIndex = 13
    vi.spyOn(api, 'autoConfigurePicoNetwork').mockResolvedValue({
      ok: true,
      data: {
        network: {
          ifIndex: 13,
          gateway: '10.90.0.1',
          localIp: '10.90.1.42',
          interfaceAlias: 'Ethernet',
          prefixLength: 17,
          selection: 'related-address',
          changed: true,
        },
        config: detectedConfig,
      },
      ts: Date.now(),
    })

    const network = await useTelemetryStore.getState().autoConfigurePicoNetwork()

    expect(network.interfaceAlias).toBe('Ethernet')
    expect(useTelemetryStore.getState().config.picoVision).toMatchObject({
      ip: '10.90.140.22',
      gateway: '10.90.0.1',
      ifIndex: 13,
    })
    vi.restoreAllMocks()
    useTelemetryStore.setState({ config: structuredClone(defaultConfig) })
  })
})
