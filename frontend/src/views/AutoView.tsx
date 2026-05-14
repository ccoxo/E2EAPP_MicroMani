import { Button, Input, Progress, Radio, Segmented, Space, Tag, Typography } from 'antd'
import { Pause, Play, Send, ShieldAlert, Square } from 'lucide-react'
import { dispatchNextAutoAction, queueAutoAction } from '../api'
import { CameraPreview } from '../components/CameraPreview'
import { QueueChart } from '../components/Charts'
import { useTelemetryStore } from '../stores/telemetry'

export function AutoView() {
  const frame = useTelemetryStore((state) => state.frame)
  const history = useTelemetryStore((state) => state.history)
  const autoRunning = useTelemetryStore((state) => state.autoRunning)
  const setAutoRunning = useTelemetryStore((state) => state.setAutoRunning)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const injectLog = useTelemetryStore((state) => state.injectLog)

  return (
    <div className="view-stack">
      <section className="page-header">
        <div>
          <Typography.Title level={2}>自动执行 Auto</Typography.Title>
          <Typography.Text type="secondary">后端 PolicyServer、动作队列、安全限幅和 HAL 下发入口。</Typography.Text>
        </div>
        <Space wrap>
          <Tag color={autoRunning ? 'green' : 'default'}>PolicyServer {autoRunning ? '运行' : '未启动'}</Tag>
          <Tag color="processing">HAL gate</Tag>
        </Space>
      </section>

      <section className="split-grid split-grid-auto">
        <div className="panel-surface">
          <div className="section-title">
            <span>实时视觉</span>
            <Segmented size="small" defaultValue="all" options={[{ label: '全部', value: 'all' }, { label: '全局', value: 'global' }, { label: '腕部', value: 'wrist' }]} />
          </div>
          <div className="camera-grid-layout">
            {frame.cameras.map((camera) => (
              <CameraPreview key={camera.key} camera={camera} compact />
            ))}
          </div>
          <QueueChart history={history} height={220} />
        </div>

        <div className="panel-surface auto-control">
          <div className="section-title">
            <span>执行控制</span>
            <Tag color="processing">ZMQ 8082 / 8083</Tag>
          </div>
          <label>
            <span>算法</span>
            <Radio.Group defaultValue="OpenVLA" optionType="button" buttonStyle="solid">
              <Radio.Button value="ACT">ACT</Radio.Button>
              <Radio.Button value="Diffusion">Diffusion</Radio.Button>
              <Radio.Button value="OpenVLA">OpenVLA</Radio.Button>
            </Radio.Group>
          </label>
          <label>
            <span>推理模式</span>
            <Radio.Group defaultValue="async" optionType="button">
              <Radio.Button value="sync">同步</Radio.Button>
              <Radio.Button value="async">异步 + RTC</Radio.Button>
            </Radio.Group>
          </label>
          <label>
            <span>任务指令 VLA</span>
            <Input.TextArea rows={3} defaultValue="Assemble ICF target component with force-limited contact." />
          </label>
          <div className="queue-meters">
            <Progress percent={frame.queueDepth.left} strokeColor="#2f6fed" format={(v) => `左臂 ${v}%`} />
            <Progress percent={frame.queueDepth.right} strokeColor="#d98400" format={(v) => `右臂 ${v}%`} />
          </div>
          <div className="policy-metrics">
            <span>推理延迟 <b>{(88 + Math.sin(frame.elapsedSec) * 9).toFixed(0)}ms</b></span>
            <span>控制频率 <b>30Hz</b></span>
            <span>VRAM <b>{frame.processStatus.find((item) => item.name === 'policy')?.vramGb?.toFixed(1) ?? '0.0'}GB</b></span>
          </div>
          <Space wrap>
            <Button type="primary" icon={<Play size={16} />} onClick={() => setAutoRunning(true)}>
              启动
            </Button>
            <Button icon={<Pause size={16} />} onClick={() => setAutoRunning(false)}>
              暂停
            </Button>
            <Button icon={<Square size={16} />} onClick={() => setAutoRunning(false)}>
              停止
            </Button>
            <Button danger icon={<ShieldAlert size={16} />} onClick={triggerEmergencyStop}>
              急停 F12
            </Button>
            <Button
              icon={<Send size={16} />}
              onClick={() => {
                void queueAutoAction({ side: 'left', axis: 'X', direction: 1, step: 50, speedMode: 'fine', maxVelocityUiPerSec: 50 })
                  .then(() => dispatchNextAutoAction())
                injectLog('INFO', 'Manual JSON action queued for policy bridge', '[ZMQ]')
              }}
            >
              注入动作
            </Button>
          </Space>
        </div>
      </section>
    </div>
  )
}
