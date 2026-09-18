import { describe, expect, it } from 'vitest'
import {
  recordDisplayConsistent,
  recordPhaseLabel,
  recordSessionBusy,
  recordUiIsRecording,
} from './recordDisplay'

describe('录制业务派生', () => {
  it('仅 phase=recording 视为业务录制中', () => {
    expect(recordUiIsRecording('recording')).toBe(true)
    expect(recordUiIsRecording('saving')).toBe(false)
    expect(recordUiIsRecording('idle')).toBe(false)
  })

  it('忙态覆盖 starting/saving/finishing', () => {
    expect(recordSessionBusy('starting')).toBe(true)
    expect(recordSessionBusy('saving')).toBe(true)
    expect(recordSessionBusy('finishing')).toBe(true)
    expect(recordSessionBusy('recording')).toBe(false)
    expect(recordSessionBusy('idle')).toBe(false)
  })

  it('标签与 phase 一一对应', () => {
    expect(recordPhaseLabel('recording')).toBe('录制中')
    expect(recordPhaseLabel('resetting')).toBe('复位中')
  })

  it('phase 已录制时允许 frame.recording 短暂 false（重连窗口）', () => {
    expect(recordDisplayConsistent('recording', false)).toBe(true)
    expect(recordDisplayConsistent('idle', true)).toBe(false)
    expect(recordDisplayConsistent('idle', false)).toBe(true)
  })
})
