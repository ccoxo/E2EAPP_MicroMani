import { Button, Modal, Slider, Space, Tag, Typography } from 'antd'
import { ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { useTelemetryStore } from '../stores/telemetry'

export function SafetyOverlay() {
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const setDangerOverride = useTelemetryStore((state) => state.setDangerOverride)
  const [testerOpen, setTesterOpen] = useState(false)

  return (
    <>
      <div aria-hidden="true" className="safety-overlay" style={{ borderColor: 'transparent', animationDuration: '1s' }} />
      <Button className="safety-test-button" size="small" icon={<ShieldAlert size={15} />} onClick={() => setTesterOpen(true)}>
        Safety Off
      </Button>
      <Modal title="SafetyOverlay 测试" open={testerOpen} onCancel={() => setTesterOpen(false)} footer={null} width={420}>
        <Space direction="vertical" className="full-width">
          <Tag color="warning">danger_index 联锁已临时屏蔽</Tag>
          <Typography.Text type="secondary">
            当前仅保留 danger_index 数值观察，不再显示高危恢复弹窗，也不阻止操作。
          </Typography.Text>
          <Slider min={0} max={1.2} step={0.01} value={dangerIndex} onChange={setDangerOverride} />
          <Space>
            <Button onClick={() => setDangerOverride(null)}>恢复实时数值</Button>
            <Button danger onClick={() => setDangerOverride(1.08)}>
              模拟数值
            </Button>
          </Space>
        </Space>
      </Modal>
    </>
  )
}
