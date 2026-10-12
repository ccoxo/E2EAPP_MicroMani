import { afterEach, expect, it, vi } from 'vitest'
import * as api from '../api'
import { useTelemetryStore as store } from './telemetry'
import { defaultConfig } from '../data'

const initial = store.getState()
afterEach(() => { store.setState(initial, true); vi.restoreAllMocks() })

it('续录恢复累计保留数，保存和丢弃以服务端计数为准', async () => {
  vi.spyOn(api, 'fetchRecordStatus').mockResolvedValue({ active: false })
  vi.spyOn(api, 'putConfig').mockResolvedValue(structuredClone(defaultConfig))
  vi.spyOn(api, 'createSession').mockResolvedValue({ ok: true, data: {
    datasetName: 'existing', active: true, recording: true, episodeIndex: 21,
    savedEpisodes: 20, sessionSavedEpisodes: 0,
  } })
  store.getState().startRecordSession('existing', 'task')
  await vi.waitFor(() => expect(store.getState().recordSession.phase).toBe('recording'))
  expect(store.getState().recordSession).toMatchObject({ currentEpisode: 21, savedEpisodes: 20, sessionSavedEpisodes: 0 })

  vi.spyOn(api, 'saveEpisode').mockResolvedValue({ ok: true, data: {
    episode: { episodeIndex: 21, frames: 30, durationS: 1 },
    status: { episodeIndex: 22, savedEpisodes: 21, sessionSavedEpisodes: 1 },
  } })
  store.getState().saveRecordEpisode()
  await vi.waitFor(() => expect(store.getState().recordSession.phase).toBe('reviewing'))
  expect(store.getState().recordSession).toMatchObject({ currentEpisode: 22, savedEpisodes: 21, sessionSavedEpisodes: 1 })

  vi.spyOn(api, 'discardEpisode').mockResolvedValue({ ok: true, data: {
    episodeIndex: 22, savedEpisodes: 20, sessionSavedEpisodes: 0,
  }, ts: Date.now() })
  store.getState().discardRecordEpisode()
  await vi.waitFor(() => expect(store.getState().recordSession.phase).toBe('resetting'))
  expect(store.getState().recordSession).toMatchObject({ currentEpisode: 22, savedEpisodes: 20, sessionSavedEpisodes: 0 })
})

it('丢弃未保存片段不扣除历史数量', async () => {
  store.setState({ recordSession: { ...initial.recordSession, phase: 'recording',
    currentEpisode: 31, savedEpisodes: 29, sessionSavedEpisodes: 9 } })
  vi.spyOn(api, 'discardEpisode').mockResolvedValue({ ok: true, data: {
    episodeIndex: 31, savedEpisodes: 29, sessionSavedEpisodes: 9,
  }, ts: Date.now() })
  store.getState().discardRecordEpisode()
  await vi.waitFor(() => expect(store.getState().recordSession.phase).toBe('resetting'))
  expect(store.getState().recordSession).toMatchObject({ currentEpisode: 31, savedEpisodes: 29, sessionSavedEpisodes: 9 })
})
