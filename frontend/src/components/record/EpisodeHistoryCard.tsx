import { Button, Card, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useTelemetryStore } from '../../stores/telemetry'

export default function EpisodeHistoryCard() {
  const history = useTelemetryStore((s) => s.recordSession.episodeHistory)
  const navigate = useNavigate()
  const recent = history.slice(0, 6)

  return (
    <Card
      title="录制历史"
      size="small"
      extra={
        <Button type="link" size="small" onClick={() => void navigate('/dataset')}>
          查看全部 -&gt;
        </Button>
      }
    >
      {recent.length === 0 ? (
        <div style={{ color: '#8c8c8c', fontSize: 11, textAlign: 'center', padding: '8px 0' }}>
          暂无录制记录
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {recent.map((ep) => (
            <div
              key={ep.index}
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto minmax(0, 1fr) auto',
                alignItems: 'center',
                gap: 6,
                padding: '4px 6px',
                borderRadius: 4,
                background: '#f8fafc',
              }}
            >
              <span style={{ fontWeight: 500, fontSize: 12 }}>
                #{String(ep.index).padStart(3, '0')}
              </span>
              <span style={{ overflow: 'hidden', fontSize: 11, color: '#8c8c8c', fontFamily: 'monospace', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ep.frameCount} 帧 / {ep.durationS.toFixed(1)}s
              </span>
              <Tag
                color={
                  ep.status === 'ok'
                    ? 'success'
                    : ep.status === 'emergency'
                      ? 'error'
                      : 'default'
                }
                style={{ fontSize: 10, padding: '0 4px', margin: 0 }}
              >
                {ep.status === 'ok' ? 'OK' : ep.status === 'emergency' ? '急停' : '丢弃'}
              </Tag>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
