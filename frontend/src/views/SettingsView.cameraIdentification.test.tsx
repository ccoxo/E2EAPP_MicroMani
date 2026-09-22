import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import { defaultConfig } from '../data'
import { useTelemetryStore } from '../stores/telemetry'
import { SettingsView } from './SettingsView'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('后端绑定结果更新设置状态，不发送第二次全配置保存；离开视觉页卸载识别界面', async () => {
  vi.spyOn(api, 'fetchMotionOrigin').mockResolvedValue({ ok: true, data: { origin: defaultConfig.motion.origin } })
  const put = vi.spyOn(api, 'putConfig')
  const config = structuredClone(defaultConfig)
  config.storage.datasetRoot = 'D:/keep-current-setting'
  useTelemetryStore.setState({ config })
  const cameras = { ...config.cameras, wristLeft: 'IMX335 / index 4', wristLeftIdentity: 'new-left', wristRightIdentity: 'new-right' }
  vi.spyOn(api, 'identifyWristCameras').mockResolvedValue({ ok: true, data: { devices: [
    { index: 4, devicePath: 'left', identity: 'new-left', name: 'USB Camera', preview: 'data:image/jpeg;base64,YQ==' },
    { index: 2, devicePath: 'right', identity: 'new-right', name: 'USB Camera', preview: 'data:image/jpeg;base64,Yg==' },
  ] } })
  vi.spyOn(api, 'bindWristCameras').mockResolvedValue({ ok: true, data: { cameras, connected: true, message: 'ok' } })
  await act(async () => { render(<MemoryRouter initialEntries={['/settings#camera-left']}><SettingsView /></MemoryRouter>) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '识别左右腕相机' })) })
  fireEvent.change(screen.getByLabelText('左腕相机'), { target: { value: 'left' } })
  fireEvent.change(screen.getByLabelText('右腕相机'), { target: { value: 'right' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '保存绑定' })) })
  expect(useTelemetryStore.getState().config.cameras).toEqual(cameras)
  expect(useTelemetryStore.getState().config.storage.datasetRoot).toBe('D:/keep-current-setting')
  expect(put).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('tab', { name: '系统连接' }))
  expect(screen.queryByRole('button', { name: '识别左右腕相机' })).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
})
