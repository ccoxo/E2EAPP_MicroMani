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
    expect(normalized.cameras.wristLeft).toBe('IMX335 / index 2')
    expect(normalized.cameras.wristRight).toBe('IMX335 / index 0')
  })
})
