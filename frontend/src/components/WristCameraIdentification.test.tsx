import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { defaultConfig } from '../data'
import { WristCameraIdentification } from './WristCameraIdentification'

const devices: api.WristCameraCandidate[] = [
  { index: 2, devicePath: 'left-path', identity: 'left-serial', name: 'USB Camera', preview: 'data:image/jpeg;base64,bGVmdA==' },
  { index: 0, devicePath: 'right-path', identity: 'right-serial', name: 'USB Camera', preview: 'data:image/jpeg;base64,cmlnaHQ=' },
  { index: 3, devicePath: 'failed-path', identity: 'failed-serial', name: 'USB Camera', error: 'camera unavailable' },
]
const saved = { ...defaultConfig.cameras, wristLeftIdentity: 'left-serial', wristRightIdentity: 'right-serial' }

beforeEach(() => {
  vi.spyOn(api, 'identifyWristCameras').mockResolvedValue({ ok: true, data: { devices } })
  vi.spyOn(api, 'bindWristCameras').mockResolvedValue({ ok: true, data: { cameras: saved, connected: true, message: 'connected' } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function scan(onSaved = vi.fn()) {
  render(<WristCameraIdentification onSaved={onSaved} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '识别左右腕相机' })) })
  return onSaved
}

function choose() {
  fireEvent.change(screen.getByLabelText('左腕相机'), { target: { value: 'left-path' } })
  fireEvent.change(screen.getByLabelText('右腕相机'), { target: { value: 'right-path' } })
}

describe('腕部相机识别', () => {
  it('仅允许选择有画面的两台不同相机，保存后同步并刷新两个腕部预览', async () => {
    const refresh = vi.spyOn(window, 'dispatchEvent')
    const onSaved = await scan()
    expect(screen.getAllByRole('img')).toHaveLength(2)
    expect(screen.getByText('camera unavailable')).toBeInTheDocument()
    expect(within(screen.getByLabelText('左腕相机')).queryByRole('option', { name: '相机 3 · USB Camera' })).toBeNull()
    const save = screen.getByRole('button', { name: '保存绑定' })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('左腕相机'), { target: { value: 'left-path' } })
    fireEvent.change(screen.getByLabelText('右腕相机'), { target: { value: 'left-path' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('两台不同')
    choose()
    await act(async () => { fireEvent.click(save) })
    expect(api.bindWristCameras).toHaveBeenCalledExactlyOnceWith('left-path', 'right-path')
    expect(onSaved).toHaveBeenCalledExactlyOnceWith(saved)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('绑定已保存并重连')
    expect(refresh.mock.calls.map(([event]) => (event as CustomEvent).detail)).toEqual([
      { camera: 'wrist_left' }, { camera: 'wrist_right' },
    ])
  })

  it('后端已保存但重连失败时保留保存结果并提示，不重复保存', async () => {
    vi.mocked(api.bindWristCameras).mockResolvedValue({ ok: true, data: { cameras: saved, connected: false, message: 'offline' } })
    const onSaved = await scan()
    choose()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '保存绑定' })) })
    expect(onSaved).toHaveBeenCalledExactlyOnceWith(saved)
    expect(screen.getByRole('status')).toHaveTextContent('绑定已保存，重连未完成：offline')
  })

  it('保存拒绝时保留选择并允许重试，等待期间防重复请求', async () => {
    let reject!: (error: Error) => void
    vi.mocked(api.bindWristCameras).mockReturnValueOnce(new Promise((_, fail) => { reject = fail }))
    const onSaved = await scan()
    choose()
    const save = screen.getByRole('button', { name: '保存绑定' })
    fireEvent.click(save)
    fireEvent.click(save)
    expect(api.bindWristCameras).toHaveBeenCalledTimes(1)
    expect(save).toBeDisabled()
    await act(async () => { reject(new Error('请先结束录制会话')) })
    expect(screen.getByRole('alert')).toHaveTextContent('请先结束录制会话')
    expect(screen.getByLabelText('左腕相机')).toHaveValue('left-path')
    expect(save).toBeEnabled()
    expect(onSaved).not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(save) })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('扫描失败后能重试，重新扫描清除旧选择', async () => {
    vi.mocked(api.identifyWristCameras).mockRejectedValueOnce(new Error('无法唯一确认全局相机'))
    await scan()
    expect(screen.getByRole('alert')).toHaveTextContent('无法唯一确认全局相机')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新扫描' })) })
    choose()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新扫描' })) })
    expect(screen.getByLabelText('左腕相机')).toHaveValue('')
    expect(screen.getByRole('button', { name: '保存绑定' })).toBeDisabled()
  })
})
