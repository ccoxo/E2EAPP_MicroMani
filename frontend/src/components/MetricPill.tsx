/*
 * 阅读导航 01｜入口与界面
 * 职责：以紧凑标签展示单项指标及其颜色状态。
 * 先看：MetricPillProps → MetricPill。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import type { ReactNode } from 'react'
import type { ConnectionState } from '../types'
import { UiTag, UiTooltip } from './ui'

const toneByState: Record<ConnectionState, 'success' | 'warning' | 'error' | 'processing' | 'muted'> = {
  ok: 'success',
  warn: 'warning',
  error: 'error',
  checking: 'processing',
  pending: 'muted',
}

interface MetricPillProps {
  state: ConnectionState
  label: ReactNode
  tip?: string
}
/** 渲染当前界面单元，并连接所需数据。 */
export function MetricPill({ state, label, tip }: MetricPillProps) {
  const tag = (
    <UiTag className="metric-pill" tone={toneByState[state]}>
      <span className={`status-dot status-dot-${state}`} />
      {label}
    </UiTag>
  )
  return tip ? <UiTooltip title={tip}>{tag}</UiTooltip> : tag
}
