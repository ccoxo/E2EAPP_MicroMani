import { Button, Progress, Space, Tag, Typography } from 'antd'
import { Database, Network, Settings, Wrench } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { CameraTelemetry, ConnectionState, DiagnosticItem, ProcessStatus, TelemetryFrame } from '../../types'
import { ModuleStatusGrid, type ModuleStatus } from './ModuleStatusGrid'

function processState(processes: ProcessStatus[], name: ProcessStatus['name']): ConnectionState {
  const item = processes.find((proc) => proc.name === name)
  if (!item) return 'pending'
  if (item.status === 'running') return 'ok'
  if (item.status === 'degraded') return 'warn'
  if (item.status === 'error') return 'error'
  return 'pending'
}

function diagnosticState(diagnostics: DiagnosticItem[], key: string): ConnectionState {
  return diagnostics.find((item) => item.key === key)?.status ?? 'pending'
}

function cameraByKey(cameras: CameraTelemetry[], key: CameraTelemetry['key']) {
  return cameras.find((camera) => camera.key === key)
}

export function PlatformOverview({
  frame,
  diagnostics,
}: {
  frame: TelemetryFrame
  diagnostics: DiagnosticItem[]
}) {
  const navigate = useNavigate()
  const globalCamera = cameraByKey(frame.cameras, 'global')
  const modules: ModuleStatus[] = [
    {
      key: 'hal',
      label: 'Windows HAL',
      state: frame.halOk ? processState(frame.processStatus, 'hal') : 'error',
      primary: 'HalServer.exe / LTDMC 控制链路',
      secondary: '轴控、夹爪、主手和急停状态由 HAL 汇聚',
      metric: 'localhost:8090',
      group: '控制',
    },
    {
      key: 'backend',
      label: 'Backend / WebSocket',
      state: frame.wsOk ? processState(frame.processStatus, 'backend') : 'error',
      primary: `Telemetry ${frame.resource.wsHz}Hz`,
      secondary: `UI ${frame.resource.uiFps.toFixed(1)} FPS，内存 ${frame.resource.memMb.toFixed(0)} MB`,
      metric: 'ws://127.0.0.1:18082/ws',
      group: '通信',
    },
    {
      key: 'wsl',
      label: 'WSL2 / LeRobot',
      state: processState(frame.processStatus, 'wsl'),
      primary: '推理与策略服务环境',
      secondary: 'M0 保留接口，Windows 端口映射待实机验证',
      metric: '待 Windows 验证',
      group: '策略',
    },
    {
      key: 'policy',
      label: 'PolicyServer',
      state: processState(frame.processStatus, 'policy'),
      primary: 'ACT / Diffusion / OpenVLA 推理入口',
      secondary: '未启动时不影响录制和人工质检',
      metric: 'ZMQ 8082 / 8083',
      group: '策略',
    },
    {
      key: 'recorder',
      label: 'DataRecorder',
      state: processState(frame.processStatus, 'recorder'),
      primary: `Episode #${frame.episodeCount} / Frame ${frame.frameCount}`,
      secondary: 'LeRobotDataset 写入链路',
      metric: frame.recording ? 'REC' : 'STANDBY',
      group: '数据',
    },
    {
      key: 'global-camera',
      label: '全局相机',
      state: globalCamera?.health ?? 'pending',
      primary: globalCamera ? `${globalCamera.fps.toFixed(1)} FPS / age ${globalCamera.frameAgeMs.toFixed(0)}ms` : '未发现全局相机',
      secondary: '用于场景观察和任务级视觉定位',
      metric: globalCamera ? `${globalCamera.timestampSkewMs.toFixed(1)}ms skew` : '待确认',
      group: '视觉',
    },
    {
      key: 'pico4',
      label: 'PICO-4 视觉推流',
      state: 'pending',
      primary: 'ADB 连接与 TCP H.264 视频链路',
      secondary: '用于把上位机相机画面推送到头显显示',
      metric: '10.90.132.174',
      group: '视觉',
    },
    {
      key: 'omega7',
      label: '双 Omega.7 主手',
      state: diagnosticState(diagnostics, 'omega7'),
      primary: '左右主手枚举与 SDK 通信',
      secondary: '用于遥操作和示教采集',
      metric: 'USB / SDK',
      group: '遥操作',
    },
    {
      key: 'safety',
      label: '安全联锁',
      state: frame.dangerIndex > 0.9 ? 'error' : frame.dangerIndex > 0.65 ? 'warn' : 'ok',
      primary: `danger_index ${frame.dangerIndex.toFixed(2)}`,
      secondary: '真实急停链路需由 Windows HAL 和硬件回报',
      metric: 'F12',
      group: '安全',
    },
  ]

  return (
    <section className="panel-surface platform-overview">
      <div className="section-title">
        <span><Network size={17} />全局软硬件状态</span>
        <Space size={8}>
          <Button size="small" icon={<Settings size={14} />} onClick={() => navigate('/settings')}>
            配置
          </Button>
        </Space>
      </div>
      <div className="platform-health-strip">
        <div>
          <Typography.Text type="secondary">系统资源</Typography.Text>
          <Progress percent={Math.round(frame.resource.cpuPct)} size="small" format={(v) => `CPU ${v}%`} />
        </div>
        <div>
          <Typography.Text type="secondary">动作队列</Typography.Text>
          <Progress percent={Math.max(frame.queueDepth.left, frame.queueDepth.right)} size="small" strokeColor="#d98400" format={() => `L ${frame.queueDepth.left}% / R ${frame.queueDepth.right}%`} />
        </div>
        <div className="platform-mode-tags">
          <Tag color={frame.halOk ? 'processing' : 'error'} icon={<Wrench size={13} />}>{frame.halOk ? 'Real HAL' : 'HAL offline'}</Tag>
          <Tag color="default" icon={<Database size={13} />}>Dataset local</Tag>
        </div>
      </div>
      <ModuleStatusGrid modules={modules} />
    </section>
  )
}
