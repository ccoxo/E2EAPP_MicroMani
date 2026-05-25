export const manualAxisStepLimitPulse = 100000
const manualTranslationStepLimitUm = 5000
const manualRotationStepLimitDeg = 2
const manualCoarseRotationStepLimitDeg = 10
/** 计算或执行手动控制的对应逻辑。 */
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
