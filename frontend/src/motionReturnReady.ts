import type { TelemetryFrame } from './types'

type MotionSide = 'left' | 'right'

function requiredAxisIndexes(side: MotionSide) {
  return side === 'right' ? [0, 1, 2, 3, 4] : [0, 1, 2, 3, 4, 5]
}

export function motionSideReturnOriginReady(
  side: MotionSide,
  motionEnabled: TelemetryFrame['motionEnabled'],
  motionAxisEnabled: TelemetryFrame['motionAxisEnabled'],
) {
  const axisEnabled = motionAxisEnabled?.[side] ?? []
  const requiredAxes = requiredAxisIndexes(side)
  if (requiredAxes.some((axisIndex) => axisEnabled[axisIndex] === false)) return false
  if (requiredAxes.every((axisIndex) => axisEnabled[axisIndex] === true)) return true
  return motionEnabled?.[side] === true
}

