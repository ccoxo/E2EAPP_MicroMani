import { Button, Card, Tag } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchDatasets } from '../../api'
import { useTelemetryStore } from '../../stores/telemetry'
import type { DatasetApi, DatasetEpisodeStatusApi } from '../../types'

interface RecentEpisode {
  key: string
  index: number | null
  label: string
  frameCount: number
  durationS: number
  statusLabel: string
  statusColor: string
}

function episodeIndex(id: string) {
  const match = id.match(/(\d+)$/)
  return match ? Number(match[1]) : null
}

function backendStatus(status: DatasetEpisodeStatusApi) {
  if (status === 'valid') return { label: '有效', color: 'success' }
  if (status === 'review') return { label: '待复核', color: 'warning' }
  return { label: '无效', color: 'error' }
}

/** 渲染当前界面单元，并连接所需数据。 */
export default function EpisodeHistoryCard() {
  const history = useTelemetryStore((s) => s.recordSession.episodeHistory)
  const datasetName = useTelemetryStore((s) => s.recordSession.datasetName)
  const navigate = useNavigate()
  const [serverDatasets, setServerDatasets] = useState<DatasetApi[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchDatasets()
      .then((datasets) => {
        if (cancelled) return
        setServerDatasets(datasets)
        setLoadFailed(false)
      })
      .catch(() => {
        if (cancelled) return
        setServerDatasets([])
        setLoadFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const recent = useMemo<RecentEpisode[]>(() => {
    const liveIndices = new Set(history.map((episode) => episode.index))
    const liveEpisodes = history.map((episode) => ({
      key: `live:${episode.index}`,
      index: episode.index,
      label: `#${String(episode.index).padStart(3, '0')}`,
      frameCount: episode.frameCount,
      durationS: episode.durationS,
      statusLabel: episode.status === 'ok' ? 'OK' : episode.status === 'emergency' ? '急停' : '丢弃',
      statusColor: episode.status === 'ok' ? 'success' : episode.status === 'emergency' ? 'error' : 'default',
    }))
    const dataset = serverDatasets?.find((item) => item.id === datasetName)
    const backendEpisodes = [...(dataset?.episodes ?? [])]
      .sort((left, right) => right.createdAt - left.createdAt)
      .map((episode) => {
        const index = episodeIndex(episode.id)
        const status = backendStatus(episode.status)
        return {
          key: `backend:${datasetName}:${episode.id}`,
          index,
          label: index === null ? episode.name || episode.id : `#${String(index).padStart(3, '0')}`,
          frameCount: episode.frames,
          durationS: episode.durationS,
          statusLabel: status.label,
          statusColor: status.color,
        }
      })
      .filter((episode) => episode.index === null || !liveIndices.has(episode.index))

    return [...liveEpisodes, ...backendEpisodes].slice(0, 6)
  }, [datasetName, history, serverDatasets])

  const emptyMessage = serverDatasets === null
    ? '正在读取录制历史…'
    : loadFailed
      ? '后端历史读取失败'
      : '暂无录制记录'

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
          {emptyMessage}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {recent.map((ep) => (
            <div
              key={ep.key}
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
              <span style={{ fontWeight: 500, fontSize: 12 }}>{ep.label}</span>
              <span style={{ overflow: 'hidden', fontSize: 11, color: '#8c8c8c', fontFamily: 'monospace', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ep.frameCount} 帧 / {ep.durationS.toFixed(1)}s
              </span>
              <Tag
                color={ep.statusColor}
                style={{ fontSize: 10, padding: '0 4px', margin: 0 }}
              >
                {ep.statusLabel}
              </Tag>
            </div>
          ))}
          {loadFailed && (
            <div style={{ color: '#cf1322', fontSize: 10, textAlign: 'center', paddingTop: 2 }}>
              后端历史读取失败，仅显示本次会话记录
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
