import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { defaultConfig } from '../data'
import { WristCameraIdentification } from './WristCameraIdentification'

describe('wrist camera identification', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks() })
  it('requires distinct choices and saves the selected devices', async () => {
    vi.spyOn(api, 'identifyWristCameras').mockResolvedValue({ data: { devices: [
      { index: 2, name: 'Camera', devicePath: 'left-device', preview: 'data:image/jpeg;base64,YQ==' },
      { index: 0, name: 'Camera', devicePath: 'right-device', preview: 'data:image/jpeg;base64,Yg==' },
    ] } })
    const bind = vi.spyOn(api, 'bindWristCameras').mockResolvedValue({
      data: { cameras: defaultConfig.cameras, connected: true, message: 'ok' },
    })
    const onSaved = vi.fn()
    render(<WristCameraIdentification onSaved={onSaved} />)
    fireEvent.click(screen.getByRole('button', { name: '重新识别腕部相机' }))
    await screen.findByAltText('候选相机 1')
    expect(screen.getByRole('button', { name: /保存绑定并重连/ })).toBeDisabled()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择左腕相机' }))
    fireEvent.click(document.querySelectorAll('.ant-select-item-option-content')[0])
    expect(screen.getByRole('button', { name: /保存绑定并重连/ })).toBeDisabled()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择右腕相机' }))
    const options = document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content')
    fireEvent.click(options[options.length - 1])
    await waitFor(() => expect(screen.getByRole('button', { name: /保存绑定并重连/ })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /保存绑定并重连/ }))
    await waitFor(() => expect(bind).toHaveBeenCalledWith('left-device', 'right-device'))
    expect(onSaved).toHaveBeenCalledWith(defaultConfig.cameras)
  })

  it('shows scan failures and permits retry', async () => {
    vi.spyOn(api, 'identifyWristCameras').mockRejectedValue(new Error('设备扫描失败'))
    render(<WristCameraIdentification onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '重新识别腕部相机' }))
    expect(await screen.findByText('设备扫描失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存绑定并重连/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新扫描 / 刷新画面' })).toBeEnabled()
  })
})
