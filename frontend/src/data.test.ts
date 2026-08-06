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
    expect(defaultConfig.teleop.strategyVersion).toBe('e2e_omega7_native_v32_card0_yaw_20260804')
  })
})

describe('HKVL-36A force safety defaults', () => {
  it('uses the published full-scale ratings as provisional stop ceilings', () => {
    expect(defaultConfig.safety.fxyWarnN).toBe(2)
    expect(defaultConfig.safety.fxyStopN).toBe(30)
    expect(defaultConfig.safety.fzWarnN).toBe(3)
    expect(defaultConfig.safety.fzStopN).toBe(30)
    expect(defaultConfig.safety.momentWarnNm).toBe(0.02)
    expect(defaultConfig.safety.momentStopNm).toBe(1)
  })
})

describe('HKVL-36A hardware-side binding', () => {
  it('binds hardware left to COM15/Card1 and hardware right to COM14/Card0', () => {
    expect(defaultConfig.force.serial.leftPort).toBe('COM15')
    expect(defaultConfig.force.serial.rightPort).toBe('COM14')
    expect(defaultConfig.motion.leftCardNo).toBe(1)
    expect(defaultConfig.motion.rightCardNo).toBe(0)
    expect(defaultConfig.force.compliance.enabled).toBe(false)
    expect(defaultConfig.force.compliance.left.mappingConfirmed).toBe(false)
    expect(defaultConfig.force.compliance.right.mappingConfirmed).toBe(false)
    expect(defaultConfig.force.compliance.left.matrix).toEqual([1, 0, 0, 1])
    expect(defaultConfig.force.compliance.right.matrix).toEqual([1, 0, 0, 1])
    expect(defaultConfig.force.axisSign.left).toEqual([1, 1, -1, -1, -1, 1])
    expect(defaultConfig.force.axisSign.right).toEqual([1, -1, 1, -1, 1, -1])
  })
})
