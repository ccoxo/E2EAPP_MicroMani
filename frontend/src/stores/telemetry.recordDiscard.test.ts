import { afterEach, expect, it, vi } from 'vitest'
import * as api from '../api'
import { useTelemetryStore as store } from './telemetry'
import type { RecordQualityReport } from '../types'

afterEach(() => vi.restoreAllMocks())

it.each([false, true])('重录等待丢弃确认且不复用已保存编号，失败=%s', async (failure) => {
  const report: RecordQualityReport = {
    index: 4, frameCount: 30, durationS: 1, status: 'ok', passed: true,
    maxForceLeft: 0, maxForceRight: 0, lateFrames: 0,
    cameraDrops: { global: 0, wristLeft: 0, wristRight: 0 }, warnings: [],
  }
  let resolve!: (value: Awaited<ReturnType<typeof api.discardEpisode>>) => void
  let reject!: (reason: Error) => void
  const request = vi.spyOn(api, 'discardEpisode').mockReturnValue(new Promise((ok, fail) => { resolve = ok; reject = fail }))
  store.setState((state) => ({ recordSession: { ...state.recordSession, phase: 'reviewing',
    currentEpisode: 5, savedEpisodes: 3, latestQualityReport: report, episodeHistory: [report] } }))
  store.getState().rejectRecordQualityReport()
  store.getState().rejectRecordQualityReport()
  expect(request).toHaveBeenCalledTimes(1)
  expect(store.getState().recordSession).toMatchObject({ phase: 'discarding', currentEpisode: 5, savedEpisodes: 3 })
  if (failure) reject(new Error('停止未确认'))
  else resolve({ ok: true, data: {}, ts: Date.now() })
  await vi.waitFor(() => expect(store.getState().recordSession.phase).toBe(failure ? 'reviewing' : 'resetting'))
  expect(store.getState().recordSession.currentEpisode).toBe(5)
  expect(store.getState().recordSession.savedEpisodes).toBe(failure ? 3 : 2)
  expect(store.getState().recordSession.latestQualityReport).toEqual(failure ? report : null)
  expect(store.getState().recordSession.episodeHistory).toHaveLength(1)
  expect(store.getState().recordSession.episodeHistory[0]).toMatchObject({ index: 4, status: failure ? 'ok' : 'discarded' })
})
