import { Progress, Space, Statistic, Tag, Typography } from 'antd'
import { Activity, AlertTriangle, ShieldCheck } from 'lucide-react'
import type { ConnectionState } from '../../types'

function scoreStatus(score: number): ConnectionState {
  if (score >= 85) return 'ok'
  if (score >= 65) return 'warn'
  return 'error'
}

export function ReadinessSummary({
  score,
  warningCount,
  pendingCount,
  dangerIndex,
  wsHz,
  uiFps,
}: {
  score: number
  warningCount: number
  pendingCount: number
  dangerIndex: number
  wsHz: number
  uiFps: number
}) {
  const status = scoreStatus(score)
  return (
    <section className={`readiness-summary readiness-${status}`}>
      <div className="readiness-primary">
        <div className="readiness-icon">
          <ShieldCheck size={22} />
        </div>
        <div>
          <Typography.Title level={2}>平台健康总览</Typography.Title>
          <Typography.Text type="secondary">启动后优先确认软硬件可用性、实时数据和安全链路。</Typography.Text>
        </div>
      </div>
      <div className="readiness-score">
        <Statistic title="整体就绪度" value={score} suffix="%" />
        <Progress percent={score} size="small" status={status === 'error' ? 'exception' : status === 'warn' ? 'active' : 'success'} />
      </div>
      <Space className="readiness-metrics" wrap size={8}>
        <Tag color={warningCount > 0 ? 'warning' : 'success'} icon={<AlertTriangle size={13} />}>
          注意 {warningCount}
        </Tag>
        <Tag color={pendingCount > 0 ? 'default' : 'success'}>待确认 {pendingCount}</Tag>
        <Tag color={dangerIndex > 0.7 ? 'error' : 'success'}>Safety {dangerIndex.toFixed(2)}</Tag>
        <Tag color="processing" icon={<Activity size={13} />}>
          WS {wsHz}Hz / UI {uiFps.toFixed(1)}FPS
        </Tag>
      </Space>
    </section>
  )
}
