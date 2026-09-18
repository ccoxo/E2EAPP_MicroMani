/*
 * 阅读导航 02｜前端契约与状态
 * 职责：定义手动粗、中、细速度倍率，并将实际速度限制在配置上限内。
 * 先看：manualSpeedScale → manualMaxVelocity。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
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
