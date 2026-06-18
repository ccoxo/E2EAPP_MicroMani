import type { RobotSide } from './data'
import type { ManualControlAxis } from './types'

export function isManualAxisDisabledForCard(cardNo: number | null | undefined, axis: ManualControlAxis) {
  return Number(cardNo) === 0 && axis === 'Yaw'
}

export function isManualAxisDisabled(side: RobotSide, axis: ManualControlAxis) {
  return isManualAxisDisabledForCard(side === 'right' ? 0 : 1, axis)
}
