import { Progress, Space, Tag } from 'antd'
import { Bot, Camera, Gamepad2, Hand, RadioTower } from 'lucide-react'
import { axisNames, forceChannels } from '../../data'
import type { CameraTelemetry, ConnectionState, DiagnosticItem, TelemetryFrame, TelemetrySample } from '../../types'
import { CameraPreview } from '../CameraPreview'
import { ForceChart } from '../Charts'
import { MetricPill } from '../MetricPill'
import { ModuleStatusGrid, type ModuleStatus } from './ModuleStatusGrid'

function diagnosticState(diagnostics: DiagnosticItem[], key: string): ConnectionState {
  return diagnostics.find((item) => item.key === key)?.status ?? 'pending'
}

function cameraByKey(cameras: CameraTelemetry[], key: CameraTelemetry['key']) {
  return cameras.find((camera) => camera.key === key)
}

function forceMagnitude(values: number[]) {
  return Math.sqrt(values.slice(0, 3).reduce((sum, value) => sum + value * value, 0))
}

function formatGripperPosition(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)} mm` : '不可用'
}

export function ArmOverviewPanel({
  side,
  frame,
  history,
  diagnostics,
}: {
  side: 'left' | 'right'
  frame: TelemetryFrame
  history: TelemetrySample[]
  diagnostics: DiagnosticItem[]
}) {
  const isLeft = side === 'left'
  const sideLabel = isLeft ? '左机械臂' : '右机械臂'
  const axisOffset = isLeft ? 0 : 6
  const camera = cameraByKey(frame.cameras, isLeft ? 'wrist_left' : 'wrist_right')
  const forces = isLeft ? frame.forceLeft : frame.forceRight
  const forceDiag = diagnosticState(diagnostics, isLeft ? 'ati-left' : 'ati-right')
  const gripState = diagnosticState(diagnostics, 'gripper')
  const teleopState = diagnosticState(diagnostics, 'omega7')
  const forceNorm = forceMagnitude(forces)
  const gripperText = formatGripperPosition(frame.gripperPositions[isLeft ? 0 : 1])
  const riskState: ConnectionState = forceNorm > 2.4 || frame.dangerIndex > 0.65 ? 'warn' : 'ok'
  const modules: ModuleStatus[] = [
    {
      key: `${side}-servo`,
      label: '6 轴伺服',
      state: frame.halOk ? 'ok' : 'error',
      primary: '已连接 / 使能状态可读',
      secondary: '软限位和伺服报警由 HAL 汇总',
      metric: 'LTDMC',
      group: '运动',
    },
    {
      key: `${side}-gripper`,
      label: '夹爪',
      state: gripState,
      primary: gripperText,
      secondary: 'RS485 端口和波特率待 Windows 实机确认',
      metric: 'RS485',
      group: '末端',
    },
    {
      key: `${side}-camera`,
      label: '腕部相机',
      state: camera?.health ?? 'pending',
      primary: camera ? `${camera.fps.toFixed(1)} FPS / age ${camera.frameAgeMs.toFixed(0)}ms` : '未发现相机',
      secondary: '用于末端近场视觉和接触前定位',
      metric: camera ? `${camera.timestampSkewMs.toFixed(1)}ms skew` : '待确认',
      group: '视觉',
    },
    {
      key: `${side}-force`,
      label: 'Nano-17',
      state: forceDiag,
      primary: `|F| ${forceNorm.toFixed(2)} N`,
      secondary: '单位、采样率和标定证书需实机复核',
      metric: '单位待确认',
      group: '力觉',
    },
    {
      key: `${side}-teleop`,
      label: '遥操作主手',
      state: teleopState,
      primary: isLeft ? '左 Omega.7 主手' : '右 Omega.7 主手',
      secondary: '主从映射和离合器状态由 HAL 回报',
      metric: 'USB / SDK',
      group: '遥操作',
    },
    {
      key: `${side}-risk`,
      label: '安全包络',
      state: riskState,
      primary: riskState === 'ok' ? '力/队列处于观察范围' : '接触力或 danger_index 偏高',
      secondary: '这里只显示风险，不在主页调整阈值',
      metric: `D ${frame.dangerIndex.toFixed(2)}`,
      group: '安全',
    },
  ]

  return (
    <section className="panel-surface arm-overview-panel">
      <div className="section-title">
        <span><Bot size={17} />{sideLabel}</span>
        <Space size={6} wrap>
          <MetricPill state={frame.halOk ? 'ok' : 'error'} label="HAL" />
          <MetricPill state={riskState} label="Safety" />
        </Space>
      </div>

      <div className="arm-data-layout">
        <div className="arm-visual-stack">
          {camera && <CameraPreview camera={camera} compact />}
          <div className="arm-inline-status">
            <Tag icon={<Camera size={13} />} color={camera?.health === 'ok' ? 'success' : 'default'}>腕相机</Tag>
            <Tag icon={<Hand size={13} />} color={gripState === 'ok' ? 'success' : 'default'}>夹爪 {gripperText}</Tag>
            <Tag icon={<Gamepad2 size={13} />} color={teleopState === 'ok' ? 'success' : 'default'}>主手</Tag>
          </div>
        </div>

        <div className="arm-motion-stack">
          <div className="panel-title">
            <span><RadioTower size={15} />运动轴 / 夹爪</span>
            <Tag color="default">只读监控</Tag>
          </div>
          <div className="joint-strip arm-joint-strip">
            {axisNames.slice(axisOffset, axisOffset + 6).map((name, index) => (
              <span key={name}>
                <b>{name.replace(isLeft ? 'L-' : 'R-', '')}</b>
                {frame.jointPositions[axisOffset + index].toFixed(1)}
              </span>
            ))}
            <span>
              <b>Grip</b>
              {gripperText}
            </span>
          </div>
          <div className="force-current-grid">
            {forceChannels.map((channel, index) => (
              <span key={channel}>
                <b>{channel}</b>
                {forces[index].toFixed(index < 3 ? 2 : 3)}
              </span>
            ))}
          </div>
          <Progress percent={Math.min(100, forceNorm * 28)} size="small" status={forceNorm > 2.4 ? 'exception' : 'active'} format={() => `|F| ${forceNorm.toFixed(2)} N`} />
        </div>
      </div>

      <ForceChart history={history} side={side} height={150} />
      <ModuleStatusGrid modules={modules} compact />
    </section>
  )
}
