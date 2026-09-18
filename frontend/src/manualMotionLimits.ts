/*
 * 阅读导航 02｜前端契约与状态
 * 职责：把脉冲标定与 HAL 单步上限组合为界面手动步长限制。
 * 先看：manualAxisStepLimitFromPulse → manualAxisStepLimitPulse。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
export const manualAxisStepLimitPulse = 100000
const manualTranslationStepLimitUm = 5000
const manualRotationStepLimitDeg = 2
const manualCoarseRotationStepLimitDeg = 10

/** Convert the shared pulse cap into the current axis UI unit. */
export function manualAxisStepLimitFromPulse(
  pulsePerUiUnit: number,
  rotation: boolean,
  speedMode?: 'fine' | 'medium' | 'coarse',
) {
  if (pulsePerUiUnit <= 0) return Number.POSITIVE_INFINITY
  const pulseLimit = manualAxisStepLimitPulse / pulsePerUiUnit
  const halLimit =
    rotation && speedMode === 'coarse'
      ? manualCoarseRotationStepLimitDeg
      : rotation
        ? manualRotationStepLimitDeg
        : manualTranslationStepLimitUm
  return Math.min(pulseLimit, halLimit)
}
