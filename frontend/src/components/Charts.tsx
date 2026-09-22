/*
 * 阅读导航 01｜入口与界面
 * 职责：用 Canvas 绘制历史遥测曲线；输入来自状态仓库的采样历史。
 * 先看：HistoryProps → LiveLineChart → JointChart → AxisGroupChart → ForceChart。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { memo, useEffect, useMemo, useRef } from 'react'
import { axisNames, forceChannels } from '../data'
import type { TelemetrySample } from '../types'

const axisText = '#7a8b9c'
const gridColor = '#e5e9f0'
const lineColor = '#d9e0e8'
const palette = ['#1b6cf3', '#0b8a9b', '#d4870f', '#e03a4f', '#7353ba', '#0d9b6c', '#c86a12', '#7a8b9c']
const semanticAxes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']

interface HistoryProps {
  history: TelemetrySample[]
  height?: number
}

interface SeriesSpec {
  name: string
  color: string
  values: number[]
}

interface LiveLineChartProps {
  series: SeriesSpec[]
  height?: number
  yUnit?: string
  yMin?: number
  yMax?: number
  showXLabels?: boolean
  testId?: string
  dataAttrs?: Record<string, string>
}

/** 轻量多序列折线图：直接在 Canvas 上绘制，避免 ECharts 在高频更新时的重渲染成本。 */
function LiveLineChart({
  series,
  height = 120,
  yUnit,
  yMin,
  yMax,
  showXLabels = false,
  testId = 'live-chart',
  dataAttrs,
}: LiveLineChartProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  const bounds = useMemo(() => {
    let min = Number.POSITIVE_INFINITY
    let max = Number.NEGATIVE_INFINITY
    for (const item of series) {
      for (const value of item.values) {
        if (!Number.isFinite(value)) continue
        if (value < min) min = value
        if (value > max) max = value
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      min = -1
      max = 1
    }
    if (min === max) {
      const pad = Math.abs(min) * 0.08 || 1
      min -= pad
      max += pad
    } else {
      const pad = (max - min) * 0.08
      min -= pad
      max += pad
    }
    return {
      min: typeof yMin === 'number' ? yMin : min,
      max: typeof yMax === 'number' ? yMax : max,
    }
  }, [series, yMin, yMax])

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return

    const draw = () => {
      const width = wrap.clientWidth || 240
      const cssHeight = height
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.floor(width * dpr))
      canvas.height = Math.max(1, Math.floor(cssHeight * dpr))
      canvas.style.width = `${width}px`
      canvas.style.height = `${cssHeight}px`
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, cssHeight)

      const padLeft = yUnit || bounds.min < 0 ? 36 : 28
      const padRight = 8
      const padTop = 4
      const padBottom = showXLabels ? 18 : 6
      const plotW = Math.max(1, width - padLeft - padRight)
      const plotH = Math.max(1, cssHeight - padTop - padBottom)
      const { min, max } = bounds
      const range = max - min || 1

      // grid + y labels
      ctx.strokeStyle = gridColor
      ctx.lineWidth = 1
      ctx.fillStyle = axisText
      ctx.font = '10px ui-monospace, Consolas, monospace'
      ctx.textAlign = 'right'
      ctx.textBaseline = 'middle'
      const yTicks = 4
      for (let i = 0; i <= yTicks; i += 1) {
        const t = i / yTicks
        const y = padTop + plotH * (1 - t)
        const value = min + range * t
        ctx.beginPath()
        ctx.moveTo(padLeft, y)
        ctx.lineTo(padLeft + plotW, y)
        ctx.stroke()
        const label = Math.abs(value) >= 1000 ? value.toFixed(0) : Math.abs(value) >= 10 ? value.toFixed(0) : value.toFixed(1)
        ctx.fillText(label, padLeft - 4, y)
      }

      // x axis line
      ctx.strokeStyle = lineColor
      ctx.beginPath()
      ctx.moveTo(padLeft, padTop + plotH)
      ctx.lineTo(padLeft + plotW, padTop + plotH)
      ctx.stroke()

      // series
      for (const item of series) {
        const n = item.values.length
        if (n < 2) continue
        ctx.strokeStyle = item.color
        ctx.lineWidth = 1.4
        ctx.lineJoin = 'round'
        ctx.beginPath()
        for (let i = 0; i < n; i += 1) {
          const x = padLeft + (plotW * i) / (n - 1)
          const y = padTop + plotH * (1 - (item.values[i] - min) / range)
          if (i === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.stroke()
      }

      if (yUnit) {
        ctx.fillStyle = axisText
        ctx.font = '10px ui-monospace, Consolas, monospace'
        ctx.textAlign = 'left'
        ctx.textBaseline = 'top'
        ctx.fillText(yUnit, 2, 2)
      }
    }

    draw()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => draw()) : null
    ro?.observe(wrap)
    return () => ro?.disconnect()
  }, [series, height, bounds, yUnit, showXLabels])

  return (
    <div
      ref={wrapRef}
      data-testid={testId}
      style={{ width: '100%', height, position: 'relative' }}
      {...dataAttrs}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 2, minHeight: 14 }}>
        {series.map((item) => (
          <span
            key={item.name}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 10,
              color: '#41556b',
              fontWeight: 600,
            }}
          >
            <i style={{ width: 10, height: 2, background: item.color, display: 'inline-block' }} />
            {item.name}
          </span>
        ))}
      </div>
      <canvas ref={canvasRef} style={{ display: 'block', width: '100%' }} />
    </div>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
export const JointChart = memo(function JointChart({ history, height = 210 }: HistoryProps) {
  const series = useMemo(
    () =>
      axisNames.map((name, index) => ({
        name,
        color: palette[index % palette.length],
        values: history.map((sample) => sample.joints[index] ?? 0),
      })),
    [history],
  )
  return <LiveLineChart series={series} height={height} showXLabels />
})

/** 渲染当前界面单元，并连接所需数据。 */
export const AxisGroupChart = memo(function AxisGroupChart({
  history,
  side,
  group,
  height = 118,
}: HistoryProps & {
  side: 'left' | 'right'
  group: 'translation' | 'rotation'
}) {
  const series = useMemo(() => {
    const offset = side === 'left' ? 0 : 6
    const indexes = group === 'translation' ? [0, 1, 2] : [3, 4, 5]
    const colors = group === 'translation' ? ['#1b6cf3', '#0b8a9b', '#d4870f'] : ['#e03a4f', '#7353ba', '#0d9b6c']
    return indexes.map((axisIndex, i) => ({
      name: `${side === 'left' ? 'L' : 'R'}-${semanticAxes[axisIndex]}`,
      color: colors[i],
      values: history.map((sample) => sample.joints[offset + axisIndex] ?? 0),
    }))
  }, [history, side, group])
  return (
    <LiveLineChart
      series={series}
      height={height}
      yUnit={group === 'translation' ? 'µm' : '°'}
      dataAttrs={{ 'data-side': side, 'data-group': group }}
    />
  )
})

/** 渲染当前界面单元，并连接所需数据。 */
export const ForceChart = memo(function ForceChart({
  history,
  side,
  height = 220,
}: HistoryProps & { side: 'left' | 'right' }) {
  const series = useMemo(() => {
    const scale = 1000
    return forceChannels.map((name, index) => ({
      name,
      color: palette[index],
      values: history.map((sample) => (side === 'left' ? sample.forceLeft[index] : sample.forceRight[index]) * scale),
    }))
  }, [history, side])
  const fxJson = useMemo(() => JSON.stringify(series[0]?.values ?? []), [series])
  return (
    <LiveLineChart
      series={series}
      height={height}
      yUnit="mN"
      dataAttrs={{ 'data-side': side, 'data-fx': fxJson }}
    />
  )
})

/** 渲染当前界面单元，并连接所需数据。 */
export const QueueChart = memo(function QueueChart({ history, height = 160 }: HistoryProps) {
  const series = useMemo(
    () => [
      { name: '左臂队列', color: '#1b6cf3', values: history.map((sample) => sample.queueLeft) },
      { name: '右臂队列', color: '#d4870f', values: history.map((sample) => sample.queueRight) },
    ],
    [history],
  )
  return <LiveLineChart series={series} height={height} yMin={0} yMax={100} />
})
