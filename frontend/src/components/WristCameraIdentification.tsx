import { Alert, Button, Modal, Select, Space, Typography } from 'antd'
import { useState } from 'react'
import { bindWristCameras, identifyWristCameras, type WristCameraCandidate } from '../api'
import { refreshCameraStream } from '../hooks/useLiveCameraSnapshot'
import type { AppConfig } from '../types'

export function WristCameraIdentification({ onSaved }: {
  onSaved: (cameras: AppConfig['cameras']) => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [devices, setDevices] = useState<WristCameraCandidate[]>([])
  const [left, setLeft] = useState<string>()
  const [right, setRight] = useState<string>()
  const [error, setError] = useState('')
  const [result, setResult] = useState('')
  const scan = async () => {
    setOpen(true)
    setBusy(true)
    setError('')
    setDevices([])
    setLeft(undefined)
    setRight(undefined)
    try {
      const response = await identifyWristCameras()
      setDevices(response.data.devices)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '扫描失败，请重试')
    } finally {
      setBusy(false)
    }
  }
  const save = async () => {
    if (!left || !right || left === right) return
    setBusy(true)
    setError('')
    try {
      const response = await bindWristCameras(left, right)
      onSaved(response.data.cameras)
      refreshCameraStream('wrist_left')
      refreshCameraStream('wrist_right')
      setResult(response.data.connected ? '腕部相机绑定已保存，重连成功' : '绑定已保存，部分相机未连接，请检查采集状态')
      setOpen(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败，请重试')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div style={{ gridColumn: '1 / -1' }}>
      <Space wrap>
        <Button onClick={() => void scan()}>重新识别腕部相机</Button>
        <Typography.Text type="secondary">{result || '换 USB 插口后，通过画面重新指定左右腕相机。'}</Typography.Text>
      </Space>
      <Modal title="重新识别腕部相机" open={open} width={850}
        onCancel={() => { if (!busy) setOpen(false) }}
        closable={!busy} maskClosable={!busy}
        footer={[
          <Button key="scan" disabled={busy} onClick={() => void scan()}>重新扫描 / 刷新画面</Button>,
          <Button key="cancel" disabled={busy} onClick={() => setOpen(false)}>取消</Button>,
          <Button key="save" type="primary" loading={busy}
            disabled={busy || !left || !right || left === right} onClick={() => void save()}>保存绑定并重连</Button>,
        ]}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Text>请根据快照指定左腕和右腕。全局相机已排除；扫描期间请保持 USB 连接。</Typography.Text>
          {error && <Alert type="error" message={error} />}
          {busy && <Typography.Text role="status">正在处理相机，请稍候…</Typography.Text>}
          {!busy && !error && devices.length < 2 && <Alert type="warning" message="可识别相机不足两台，请检查连接后重新扫描。" />}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
            {devices.map((device, index) => (
              <div key={device.devicePath}>
                <Typography.Title level={5}>候选相机 {index + 1}</Typography.Title>
                {device.preview
                  ? <img src={device.preview} alt={'候选相机 ' + (index + 1)} style={{ width: '100%', aspectRatio: '4 / 3', objectFit: 'contain', background: '#111' }} />
                  : <Alert type="warning" message="无法获取画面，请重新扫描" description={device.error} />}
              </div>
            ))}
          </div>
          <Space wrap>
            {(['left', 'right'] as const).map((side) => (
              <Select key={side} aria-label={side === 'left' ? '选择左腕相机' : '选择右腕相机'}
                placeholder={side === 'left' ? '选择左腕相机' : '选择右腕相机'}
                style={{ width: 220 }} disabled={busy}
                value={side === 'left' ? left : right}
                onChange={side === 'left' ? setLeft : setRight}
                options={devices.map((device, index) => ({
                  label: '候选相机 ' + (index + 1), value: device.devicePath,
                  disabled: !device.preview || device.devicePath === (side === 'left' ? right : left),
                }))} />
            ))}
          </Space>
        </Space>
      </Modal>
    </div>
  )
}
