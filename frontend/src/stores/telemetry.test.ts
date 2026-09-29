/*
 * 阅读导航 07｜测试与验证
 * 职责：验证配置默认值迁移、相机绑定和候选 HAL 二进制诊断提示。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { defaultConfig, defaultDiagnostics } from '../data'
import { diagnosticsFromHardwareStatus, normalizeConfig, useTelemetryStore } from './telemetry'

describe('telemetry config normalization', () => {
  it('恢复顶部序列号时保留自定义腕部绑定，且不修改输入', () => {
    const config = structuredClone(defaultConfig)
    config.cameras.globalIdentity = 'USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000'
    config.cameras.wristLeftIdentity = 'USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000'
    config.cameras.wristRightIdentity = 'custom-confirmed-right'
    const before = structuredClone(config)
    const normalized = normalizeConfig(config)
    expect(normalized.cameras.globalIdentity).toBe('20250606105')
    expect(normalized.cameras.wristLeftIdentity).toBe('')
    expect(normalized.cameras.wristLeft).toBe('index -1')
    expect(normalized.cameras.wristRightIdentity).toBe('custom-confirmed-right')
    expect(config).toEqual(before)
    expect(normalizeConfig(normalized)).toEqual(normalized)
  })

  it('旧索引标签不覆盖已经确认的稳定身份', () => {
    const config = structuredClone(defaultConfig)
    Object.assign(config.cameras, {
      global: 'IMX335 / index 1', globalIdentity: 'custom-top',
      wristLeft: 'IMX335 / index 2', wristLeftIdentity: 'confirmed-left',
      wristRight: 'IMX335 / index 0', wristRightIdentity: 'confirmed-right',
    })
    expect(normalizeConfig(config).cameras).toEqual(config.cameras)
  })

  it('保留用户显式选择的 NI-DAQ 备用数据源及通道', () => {
    const config = structuredClone(defaultConfig)
    config.force.source = 'nidaq'
    config.force.leftIp = 'Dev7/ai0:5'

    expect(normalizeConfig(config).force).toMatchObject({ source: 'nidaq', leftIp: 'Dev7/ai0:5' })
  })

  it('丢弃旧开机回原点配置并保留工作原点及遥操作准备配置', () => {
    const legacy = structuredClone(defaultConfig)
    Object.assign(legacy.motion, { homeOnStartup: { enabled: true, mode: 'work_origin' } })
    const normalized = normalizeConfig(legacy)
    expect(normalized.motion).not.toHaveProperty('homeOnStartup')
    expect(normalized.motion.origin).toEqual(legacy.motion.origin)
    expect(normalized.teleop.homeBeforeStart).toBe(legacy.teleop.homeBeforeStart)
    expect(legacy.motion).toHaveProperty('homeOnStartup')
  })

  it('migrates stale PICO and camera hardware defaults', () => {
    const staleConfig = structuredClone(defaultConfig)
    staleConfig.picoVision.ip = '10.90.132.51'
    staleConfig.cameras.global = 'AR0234 / index 1'
    staleConfig.cameras.wristLeft = 'IMX258 / index 2'
    staleConfig.cameras.wristRight = 'IMX258 / index 0'

    const normalized = normalizeConfig(staleConfig)

    expect(normalized.picoVision.ip).toBe('10.90.129.166')
    expect(normalized.cameras.global).toBe('IMX335 / index 0')
    expect(normalized.cameras.wristLeft).toBe('index -1')
    expect(normalized.cameras.wristRight).toBe('index -1')
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

    expect(normalized.cameras.globalIdentity).toBe('20250606105')
    expect(normalized.cameras.wristLeft).toBe('index -1')
    expect(normalized.cameras.wristLeftIdentity).toBe('')
    expect(normalized.cameras.wristRight).toBe('index -1')
    expect(normalized.cameras.wristRightIdentity).toBe('')
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
    })

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
