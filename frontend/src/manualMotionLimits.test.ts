import { describe, expect, it } from 'vitest'

import { manualAxisStepLimitFromPulse } from './manualMotionLimits'

describe('manualAxisStepLimitFromPulse', () => {
  it('allows coarse rotation requests up to the backend chunked cap', () => {
    expect(manualAxisStepLimitFromPulse(333.3333, true, 'coarse')).toBe(10)
  })

  it('keeps medium and fine rotation requests aligned with HAL single-step caps', () => {
    expect(manualAxisStepLimitFromPulse(333.3333, true, 'medium')).toBe(2)
    expect(manualAxisStepLimitFromPulse(333.3333, true, 'fine')).toBe(2)
  })

  it('keeps translation requests aligned with HAL single-step caps', () => {
    expect(manualAxisStepLimitFromPulse(5, false, 'coarse')).toBe(5000)
  })

  it('preserves stricter pulse-derived limits when they are smaller than HAL caps', () => {
    expect(manualAxisStepLimitFromPulse(100000, false, 'coarse')).toBe(1)
    expect(manualAxisStepLimitFromPulse(100000, true, 'coarse')).toBe(1)
  })
})
