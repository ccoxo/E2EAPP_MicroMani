import { describe, expect, it } from 'vitest'
import { defaultConfig } from '../data'
import { normalizeConfig } from './telemetry'

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
