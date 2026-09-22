/*
 * 阅读导航 02｜前端契约与状态
 * 职责：根据所需轴的使能反馈判断某侧是否具备返回工作原点条件。
 * 先看：MotionSide → requiredAxisIndexes → motionSideReturnOriginReady。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import type { TelemetryFrame } from './types'

type MotionSide = 'left' | 'right'

export function motionSideReturnOriginReady(
  side: MotionSide,
  _motionEnabled: TelemetryFrame['motionEnabled'],
  motionAxisEnabled: TelemetryFrame['motionAxisEnabled'],
) {
  const axisEnabled = motionAxisEnabled?.[side] ?? []
  return axisEnabled.length === 6 && axisEnabled.every((enabled) => enabled === true)
}

