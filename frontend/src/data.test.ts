import { describe, expect, it } from 'vitest'
import { axisHardwareSpecs, defaultConfig, defaultKinematics } from './data'

describe('motion calibration defaults', () => {
  it('keeps frontend default kinematics aligned with backend runtime defaults', () => {
    const leftPulsePerUnit = [5000, 5000, 10000, 1666.666667, 2500, 3333.333]
    const rightPulsePerUnit = [5000, 10000, 5000, 1666.666667, 2500, 333.3333]
    const leftSignedPulsePerUnit = [-5000, 5000, -10000, 1666.666667, -2500, -3333.333]
    const rightSignedPulsePerUnit = [-5000, -10000, -5000, 1666.666667, 2500, 333.3333]

    expect(defaultKinematics.leftPulsePerUnit).toEqual(leftPulsePerUnit)
    expect(defaultKinematics.rightPulsePerUnit).toEqual(rightPulsePerUnit)
    expect(defaultKinematics.leftSignedPulsePerUnit).toEqual(leftSignedPulsePerUnit)
    expect(defaultKinematics.rightSignedPulsePerUnit).toEqual(rightSignedPulsePerUnit)
    expect(defaultConfig.motion.kinematics.leftPulsePerUnit).toEqual(leftPulsePerUnit)
    expect(defaultConfig.motion.kinematics.rightPulsePerUnit).toEqual(rightPulsePerUnit)
    expect(axisHardwareSpecs.map((axis) => axis.leftPulsePerUnit)).toEqual(leftPulsePerUnit)
    expect(axisHardwareSpecs.map((axis) => axis.rightPulsePerUnit)).toEqual(rightPulsePerUnit)
  })
})

describe('Omega7 teleop defaults', () => {
  it('keeps the left hand gravity compensation weak by default', () => {
    expect(defaultConfig.teleop.leftGravityCompensation).toBe(true)
    expect(defaultConfig.teleop.rightGravityCompensation).toBe(true)
    expect(defaultConfig.teleop.leftForceFeedback).toBe(true)
    expect(defaultConfig.teleop.rightForceFeedback).toBe(true)
    expect(defaultConfig.teleop.leftGravityScale).toBe(0.45)
    expect(defaultConfig.teleop.rightGravityScale).toBe(1.0)
    expect(defaultConfig.teleop.strategyVersion).toBe('e2e_omega7_native_v31_gravity_scale_20260617')
  })
})
