import type { ManualSpeedMode } from './types'

/** Scale operator jog speed by the selected precision mode. */
export function manualSpeedScale(mode: ManualSpeedMode) {
  if (mode === 'coarse') return 2
  if (mode === 'medium') return 0.5
  return 0.2
}
/** Apply both the configured speed and the hard UI/HAL velocity cap. */
export function manualMaxVelocity(configuredMaxSpeed: number, velocityCap: number, mode: ManualSpeedMode) {
  const cappedConfiguredSpeed = Math.min(configuredMaxSpeed, velocityCap)
  return Math.min(velocityCap, Math.max(0.001, cappedConfiguredSpeed * manualSpeedScale(mode)))
}
