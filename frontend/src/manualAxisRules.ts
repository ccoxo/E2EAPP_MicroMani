import type { RobotSide } from './data'
import type { ManualControlAxis } from './types'

export function isManualAxisDisabled(side: RobotSide, axis: ManualControlAxis) {
  return side === 'right' && axis === 'Yaw'
}
