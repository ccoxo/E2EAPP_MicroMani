import { useTelemetryStore } from '../stores/telemetry'
import {
  gripperCommandProgressLabel,
  gripperCommandTone,
  gripperFeedbackHealth,
  gripperFeedbackLabel,
  gripperFeedbackTone,
  gripperRequestedEnabled,
  gripperRequestLabel,
  gripperRequestTone,
} from '../gripperDisplay'
import type { ManualControlSide } from '../types'

/** 夹爪三套展示：请求启停（配置）、命令进度、反馈健康；互不冒充。 */
export function useGripperDisplay(side: ManualControlSide) {
  const requestedEnabled = useTelemetryStore((state) => gripperRequestedEnabled(state.config, side))
  const command = useTelemetryStore((state) => state.gripperCommand[side])
  const feedback = useTelemetryStore((state) =>
    gripperFeedbackHealth(state.frame, state.telemetryLink, side),
  )
  const issueManualGripperMove = useTelemetryStore((state) => state.issueManualGripperMove)
  const busy = command.phase === 'sending'
  return {
    requestedEnabled,
    requestLabel: gripperRequestLabel(requestedEnabled ? 'enabled' : 'disabled'),
    requestTone: gripperRequestTone(requestedEnabled ? 'enabled' : 'disabled'),
    command,
    commandLabel: gripperCommandProgressLabel(command),
    commandTone: gripperCommandTone(command.phase),
    feedback,
    feedbackLabel: gripperFeedbackLabel(feedback),
    feedbackTone: gripperFeedbackTone(feedback),
    busy,
    issueManualGripperMove,
  }
}
