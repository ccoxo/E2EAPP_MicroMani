import { useTelemetryStore } from '../stores/telemetry'
import { useShallow } from 'zustand/react/shallow'
import { motionCommandLabel, motionDeviceLabels, motionDeviceState, motionTelemetryIsLive } from '../stores/motionCommands'
import type { ManualControlSide } from '../types'
import { controlSafetyBlockReason } from '../utils/controlSafety'

/** 两处运动控制入口共用命令进度和遥测解释，side 均为硬件侧。 */
export function useMotionEnable(side: ManualControlSide) {
  const command = useTelemetryStore((state) => state.motionCommand[side])
  const deviceState = useTelemetryStore((state) => motionDeviceState(state.frame, state.telemetryLink, side))
  const live = useTelemetryStore((state) => motionTelemetryIsLive(state.frame, state.telemetryLink))
  const blockedReason = useTelemetryStore((state) => controlSafetyBlockReason(state, false))
  const axes = useTelemetryStore(useShallow((state) => state.frame.motionAxisEnabled?.[side]))
  const confirmedAxes = useTelemetryStore(useShallow((state) => state.frame.motionAxisEnabledConfirmed?.[side]))
  const setMotionEnabled = useTelemetryStore((state) => state.setMotionEnabled)
  const busy = command.transportPending || command.phase === 'waitingConfirm'
  const desired = command.queuedEnabled ?? (busy ? command.targetEnabled : null)
  const canEnable = live && !blockedReason
  const feedbackConfirmed = Array.isArray(confirmedAxes) && confirmedAxes.length === 6 && confirmedAxes.every(Boolean)
  const deviceLabel = deviceState === 'unknown' || feedbackConfirmed
    ? motionDeviceLabels[deviceState]
    : `${motionDeviceLabels[deviceState]}（硬件反馈未确认）`
  return {
    command,
    deviceState,
    deviceLabel,
    feedbackConfirmed,
    commandLabel: motionCommandLabel(command),
    live,
    canEnable,
    enableBlockedReason: blockedReason ?? (live ? null : '遥测不可用'),
    axes,
    busy,
    desired,
    nextEnabled: canEnable && (desired === null ? deviceState !== 'enabled' : !desired),
    setMotionEnabled,
  }
}
