import { describe, expect, it } from 'vitest'

import { manualMaxVelocity, manualSpeedScale } from './manualSpeed'

describe('manualSpeedScale', () => {
  it('uses a faster coarse speed while preserving medium and fine precision', () => {
    expect(manualSpeedScale('coarse')).toBe(2)
    expect(manualSpeedScale('medium')).toBe(0.5)
    expect(manualSpeedScale('fine')).toBe(0.2)
  })

  it('keeps faster coarse speed within the motion velocity cap', () => {
    expect(manualMaxVelocity(6, 30, 'coarse')).toBe(12)
    expect(manualMaxVelocity(30, 30, 'coarse')).toBe(30)
  })
})
