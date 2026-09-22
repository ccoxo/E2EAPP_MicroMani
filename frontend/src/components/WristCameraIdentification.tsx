import { useEffect, useRef, useState } from 'react'
import { bindWristCameras, identifyWristCameras, type WristCameraCandidate } from '../api'
import { refreshCameraStream } from '../hooks/useLiveCameraSnapshot'
import type { AppConfig } from '../types'
import { UiButton, UiField, UiSelect, UiSpace, UiText } from './ui'

export function WristCameraIdentification({ onSaved }: { onSaved: (cameras: AppConfig['cameras']) => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<'scan' | 'save' | null>(null)
  const [devices, setDevices] = useState<WristCameraCandidate[]>([])
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)
  const pending = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const scan = async () => {
    if (pending.current) return
    pending.current = true
    setOpen(true)
    setBusy('scan')
    setDevices([])
    setLeft('')
    setRight('')
    setError('')
    setNotice('')
    try {
      const result = await identifyWristCameras()
      if (mounted.current) setDevices(result.data.devices)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(null)
    }
  }

  const selectable = devices.filter((device) => device.preview && !device.error)
  const leftDevice = selectable.find((device) => device.devicePath === left)
  const rightDevice = selectable.find((device) => device.devicePath === right)
  const canSave = Boolean(leftDevice && rightDevice && left !== right && leftDevice.identity !== rightDevice.identity)
  const save = async () => {
    if (pending.current || !canSave) return
    pending.current = true
    setBusy('save')
    setError('')
    try {
      const result = await bindWristCameras(left, right)
      // 接口已落盘；这里只同步已保存状态，避免再次 PUT 整份旧配置。
      onSaved(result.data.cameras)
      refreshCameraStream('wrist_left')
      refreshCameraStream('wrist_right')
      if (mounted.current) {
        setNotice(result.data.connected ? '腕部相机绑定已保存并重连' : `绑定已保存，重连未完成：${result.data.message}`)
        setOpen(false)
      }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(null)
    }
  }
  const options = [
    { value: '', label: '请选择相机' },
    ...selectable.map((device) => ({ value: device.devicePath, label: `相机 ${device.index} · ${device.name}` })),
  ]

  return (
    <div style={{ marginBottom: 12 }}>
      <UiSpace wrap>
        <UiButton onClick={() => void scan()} disabled={busy !== null}>识别左右腕相机</UiButton>
        <UiText secondary>查看候选画面后绑定左右腕；全局相机保持原绑定。</UiText>
      </UiSpace>
      {notice && <p role="status">{notice}</p>}
      {open && (
        <div className="ui-modal-mask" role="presentation">
          <div className="ui-modal" role="dialog" aria-modal="true" aria-label="识别左右腕相机" style={{ width: 860, maxWidth: '95vw' }}>
            <header className="ui-modal-head"><strong>识别左右腕相机</strong></header>
            <div className="ui-modal-body">
              <p>请根据画面选择实际安装在左腕、右腕的两台相机。识别期间请勿拔插设备。</p>
              {busy === 'scan' && <p role="status">正在扫描并获取候选画面…</p>}
              {error && <div className="ui-alert ui-alert-error" role="alert">{error}</div>}
              {!busy && !error && devices.length === 0 && <p>未找到可识别的腕部相机，请检查连接和全局相机身份配置。</p>}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
                {devices.map((device) => (
                  <section key={device.devicePath}>
                    <strong>相机 {device.index} · {device.name}</strong>
                    {device.preview && !device.error ? (
                      <img src={device.preview} alt={`候选相机 ${device.index}`} style={{ display: 'block', width: '100%' }} />
                    ) : <p role="status">{device.error || '未取得画面'}</p>}
                  </section>
                ))}
              </div>
              <UiSpace wrap align="start" style={{ marginTop: 16 }}>
                <UiField label="左腕相机"><UiSelect value={left} options={options} disabled={busy !== null} onChange={setLeft} /></UiField>
                <UiField label="右腕相机"><UiSelect value={right} options={options} disabled={busy !== null} onChange={setRight} /></UiField>
              </UiSpace>
              {left && right && !canSave && <p role="alert">左右腕必须选择两台不同的相机。</p>}
            </div>
            <div className="ui-modal-actions">
              <UiButton disabled={busy !== null} onClick={() => setOpen(false)}>取消</UiButton>
              <UiButton loading={busy === 'scan'} disabled={busy !== null} onClick={() => void scan()}>重新扫描</UiButton>
              <UiButton variant="primary" loading={busy === 'save'} disabled={busy !== null || !canSave} onClick={() => void save()}>保存绑定</UiButton>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
