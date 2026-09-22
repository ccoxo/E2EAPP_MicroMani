/*
 * 阅读导航 01｜入口与界面
 * 职责：把诊断结果汇总为准备程度与状态提示。
 * 先看：scoreStatus → ReadinessSummary。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { UiProgress, UiSpace, UiTag, UiText, UiTitle } from '../ui'
import { ShieldCheck } from 'lucide-react'
import type { ConnectionState } from '../../types'
/** 计算对应的业务值或展示值。 */
function scoreStatus(score: number): ConnectionState {
  if (score >= 85) return 'ok'
  if (score >= 65) return 'warn'
  return 'error'
}
/** 渲染当前界面单元，并连接所需数据。 */
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
          <UiTitle level={2}>平台健康总览</UiTitle>
          <UiText secondary>启动后优先确认软硬件可用性、实时数据和安全链路。</UiText>
        </div>
      </div>
      <div className="readiness-score">
        <div className="readiness-score"><small>整体就绪度</small><b>{score}%</b></div>
        <UiProgress percent={score} status={status === 'error' ? 'exception' : status === 'warn' ? 'active' : 'success'} />
      </div>
      <UiSpace className="readiness-metrics" wrap size={8}>
        <UiTag tone={warningCount > 0 ? 'warning' : 'success'}>
          注意 {warningCount}
        </UiTag>
        <UiTag tone={pendingCount > 0 ? 'default' : 'success'}>待确认 {pendingCount}</UiTag>
        <UiTag tone={dangerIndex > 0.7 ? 'error' : 'success'}>Safety {dangerIndex.toFixed(2)}</UiTag>
        <UiTag tone="processing">
          WS {wsHz}Hz / UI {uiFps.toFixed(1)}FPS
        </UiTag>
      </UiSpace>
    </section>
  )
}
