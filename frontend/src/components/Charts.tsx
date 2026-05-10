import ReactECharts from 'echarts-for-react'
import { memo, useMemo } from 'react'
import { axisNames, forceChannels } from '../data'
import type { TelemetrySample } from '../types'

const chartText = '#334155'
const axisText = '#64748b'
const palette = ['#2f6fed', '#0d7c8a', '#d98400', '#d83a52', '#7353ba', '#168a4a', '#c86a12', '#667085']
const semanticAxes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']

interface HistoryProps {
  history: TelemetrySample[]
  height?: number
}

// All charts share these constants; pulling them out of the inline render keeps
// the option objects shallowly comparable between ticks and lets React.memo
// do its job — the previous code rebuilt 5+ ECharts options every WS frame and
// passed them into ECharts with notMerge, which is the dominant frontend cost.

export const JointChart = memo(function JointChart({ history, height = 210 }: HistoryProps) {
  const option = useMemo(
    () => ({
      color: palette,
      animation: false,
      grid: { left: 38, right: 12, top: 24, bottom: 26 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: history.map((sample) => sample.time.toFixed(1)),
        axisLabel: { color: axisText, fontSize: 10 },
        axisLine: { lineStyle: { color: '#cbd5e1' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: axisText, fontSize: 10 },
        splitLine: { lineStyle: { color: '#e5e7eb' } },
      },
      legend: {
        type: 'scroll',
        top: 0,
        textStyle: { color: chartText, fontSize: 10 },
        data: axisNames,
      },
      series: axisNames.map((name, index) => ({
        name,
        type: 'line',
        symbol: 'none',
        sampling: 'lttb',
        lineStyle: { width: index % 6 < 3 ? 1.3 : 1 },
        data: history.map((sample) => sample.joints[index]),
      })),
    }),
    [history],
  )
  return <ReactECharts option={option} style={{ height }} lazyUpdate />
})

export const AxisGroupChart = memo(function AxisGroupChart({
  history,
  side,
  group,
  height = 118,
}: HistoryProps & {
  side: 'left' | 'right'
  group: 'translation' | 'rotation'
}) {
  const option = useMemo(() => {
    const offset = side === 'left' ? 0 : 6
    const indexes = group === 'translation' ? [0, 1, 2] : [3, 4, 5]
    const labels = indexes.map((index) => `${side === 'left' ? 'L' : 'R'}-${semanticAxes[index]}`)
    return {
      color: group === 'translation' ? ['#2f6fed', '#0d7c8a', '#d98400'] : ['#d83a52', '#7353ba', '#168a4a'],
      animation: false,
      grid: { left: 34, right: 8, top: 20, bottom: 18 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: history.map((sample) => sample.time.toFixed(1)),
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#cbd5e1' } },
      },
      yAxis: {
        type: 'value',
        name: group === 'translation' ? 'µm' : '°',
        nameTextStyle: { color: axisText, fontSize: 10, padding: [0, 0, 0, 4] },
        axisLabel: { color: axisText, fontSize: 9 },
        splitLine: { lineStyle: { color: '#e5e7eb' } },
      },
      legend: {
        top: 0,
        right: 0,
        textStyle: { color: chartText, fontSize: 10 },
        itemWidth: 14,
        itemHeight: 8,
        data: labels,
      },
      series: labels.map((name, labelIndex) => ({
        name,
        type: 'line',
        symbol: 'none',
        sampling: 'lttb',
        lineStyle: { width: 1.5 },
        data: history.map((sample) => sample.joints[offset + indexes[labelIndex]]),
      })),
    }
  }, [history, side, group])
  return <ReactECharts option={option} style={{ height }} lazyUpdate />
})

export const ForceChart = memo(function ForceChart({
  history,
  side,
  height = 220,
}: HistoryProps & { side: 'left' | 'right' }) {
  const option = useMemo(() => {
    const scale = 1000
    return {
      color: palette.slice(0, 6),
      animation: false,
      grid: { left: 38, right: 12, top: 26, bottom: 26 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: history.map((sample) => sample.time.toFixed(1)),
        axisLabel: { color: axisText, fontSize: 10 },
        axisLine: { lineStyle: { color: '#cbd5e1' } },
      },
      yAxis: {
        type: 'value',
        name: 'mN',
        nameTextStyle: { color: axisText, fontSize: 10, padding: [0, 0, 0, 4] },
        axisLabel: { color: axisText, fontSize: 10 },
        splitLine: { lineStyle: { color: '#e5e7eb' } },
      },
      legend: { top: 0, textStyle: { color: chartText, fontSize: 10 }, data: forceChannels },
      series: forceChannels.map((name, index) => ({
        name,
        type: 'line',
        symbol: 'none',
        sampling: 'lttb',
        lineStyle: { width: 1.4 },
        data: history.map((sample) => (side === 'left' ? sample.forceLeft[index] : sample.forceRight[index]) * scale),
      })),
    }
  }, [history, side])
  return <ReactECharts option={option} style={{ height }} lazyUpdate />
})

export const QueueChart = memo(function QueueChart({ history, height = 160 }: HistoryProps) {
  const option = useMemo(
    () => ({
      color: ['#2f6fed', '#d98400'],
      animation: false,
      grid: { left: 34, right: 12, top: 24, bottom: 24 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: history.map((sample) => sample.time.toFixed(1)), axisLabel: { show: false } },
      yAxis: { type: 'value', min: 0, max: 100, splitLine: { lineStyle: { color: '#e5e7eb' } } },
      legend: { top: 0, textStyle: { color: chartText, fontSize: 10 }, data: ['左臂队列', '右臂队列'] },
      series: [
        { name: '左臂队列', type: 'line', symbol: 'none', sampling: 'lttb', data: history.map((sample) => sample.queueLeft) },
        { name: '右臂队列', type: 'line', symbol: 'none', sampling: 'lttb', data: history.map((sample) => sample.queueRight) },
      ],
    }),
    [history],
  )
  return <ReactECharts option={option} style={{ height }} lazyUpdate />
})
