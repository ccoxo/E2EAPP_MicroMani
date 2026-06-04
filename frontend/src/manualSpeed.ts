import type { ManualSpeedMode } from './types'
/** 计算或执行手动控制的对应逻辑。 */
export function manualSpeedScale(mode: ManualSpeedMode) {
  if (mode === 'coarse') return 2
  if (mode === 'medium') return 0.5
  return 0.2
}
/** 计算或执行手动控制的对应逻辑。 */
export function manualMaxVelocity(configuredMaxSpeed: number, velocityCap: number, mode: ManualSpeedMode) {
  const cappedConfiguredSpeed = Math.min(configuredMaxSpeed, velocityCap)
  return Math.min(velocityCap, Math.max(0.001, cappedConfiguredSpeed * manualSpeedScale(mode)))
}
