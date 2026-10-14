import { afterEach, expect, it, vi } from 'vitest'
import * as api from '../api'
import { useTelemetryStore as store } from './telemetry'

afterEach(() => vi.restoreAllMocks())

it('保存后的质量报告带上后端评估与时序统计', async () => {
  const qualityAssessment = { recommendation: 'accept' as const, reasons: [], lateRate: 0.003 }
  vi.spyOn(api, 'saveEpisode').mockResolvedValue({ ok: true, ts: Date.now(), data: { episode: {
    episodeIndex: 32, frames: 375, durationS: 13.9, lateFrames: 1, maxSkewMs: 38.9,
    cameraMinFps: { global: 29.7 }, warnings: ['gripper settle timeout'], qualityAssessment,
  } } })
  store.setState((state) => ({ recording: true, recordSession: { ...state.recordSession, phase: 'recording' } }))
  store.getState().saveRecordEpisode()
  await vi.waitFor(() => expect(store.getState().recordSession.phase).toBe('reviewing'))
  expect(store.getState().recordSession.latestQualityReport).toMatchObject({
    index: 32, frameCount: 375, maxSkewMs: 38.9, cameraMinFps: { global: 29.7 }, qualityAssessment,
  })
})
