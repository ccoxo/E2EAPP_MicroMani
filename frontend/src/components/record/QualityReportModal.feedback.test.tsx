import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { RecordQualityReport } from '../../types'
import QualityReportModal from './QualityReportModal'

afterEach(cleanup)

const routine = [
  'late frames: 1',
  'force latency summary: samples=449 warnings=5 avg=3.752ms max=15.0ms',
  'gripper settle timeout',
  'omega stale: 1',
]

function report(warnings: string[], codes: string[]): RecordQualityReport {
  return {
    index: 28, frameCount: 449, durationS: 15, status: 'ok', maxForceLeft: 0, maxForceRight: 0.4, lateFrames: 1,
    cameraDrops: { global: 0, wristLeft: 0, wristRight: 0 }, warnings, passed: false,
    qualityAssessment: {
      recommendation: codes.length ? 'review' : 'accept',
      reasons: codes.map((code) => ({ severity: 'review' as const, code, message: code })),
    },
  }
}

function feedbackTile() {
  return screen.getByText('参与设备反馈').parentElement?.textContent ?? ''
}

it('常规延迟统计和到位超时不再把参与设备反馈标成需复核', () => {
  render(<QualityReportModal open report={report(routine, [])} onReRecord={vi.fn()} onAccept={vi.fn()} />)
  expect(feedbackTile()).toContain('正常')
})

it('后端判对齐超限时参与设备反馈提示复核', () => {
  render(<QualityReportModal open report={report(routine, ['alignment_exceeded'])} onReRecord={vi.fn()} onAccept={vi.fn()} />)
  expect(feedbackTile()).toContain('需复核')
})

it('组帧拒绝等关键失效仍标异常', () => {
  render(<QualityReportModal open report={report([...routine, 'frame assembler recovered: 参与采集设备反馈无效'], [])} onReRecord={vi.fn()} onAccept={vi.fn()} />)
  expect(feedbackTile()).toContain('异常')
})
