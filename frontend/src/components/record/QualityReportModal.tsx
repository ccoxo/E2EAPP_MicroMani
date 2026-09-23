/*
 * 阅读导航 01｜入口与界面
 * 职责：展示录制 episode 的质量结果与相关统计。
 * 先看：QualityReportModalProps → QualityReportModal。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import type { CSSProperties } from 'react'
import type { RecordQualityReport } from '../../types'
import { UiButton, UiText } from '../ui'

type Recommendation = 'accept' | 'review' | 'rerecord'
type MetricTone = 'good' | 'warn' | 'bad'

const toneStyle: Record<MetricTone, CSSProperties> = {
  good: { borderColor: '#9fdcc4', background: '#f1fbf5', color: '#16794c' },
  warn: { borderColor: '#f0d09a', background: '#fff9ed', color: '#98651b' },
  bad: { borderColor: '#f5b4bc', background: '#fff3f4', color: '#b42332' },
}

function metricTone(value: number, reviewAt: number, rerecordAt: number): MetricTone {
  if (value >= rerecordAt) return 'bad'
  if (value >= reviewAt) return 'warn'
  return 'good'
}

function feedbackLevel(warnings: string[]): MetricTone {
  if (warnings.some((warning) => /frame assembler|参与采集设备反馈无效|native gripper status unavailable|control lease|WebSocket|hal timeout|hal failed/i.test(warning))) return 'bad'
  if (warnings.some((warning) => /gripper stale|gripper settle|alignment exceeded|latency summary|hal stale|omega stale/i.test(warning))) return 'warn'
  return 'good'
}

function recommendationFor(report: RecordQualityReport): Recommendation {
  if (report.qualityAssessment?.recommendation) return report.qualityAssessment.recommendation
  const lateRate = report.frameCount > 0 ? report.lateFrames / report.frameCount : 1
  const cameraRate = Math.max(
    report.cameraDrops.global,
    report.cameraDrops.wristLeft,
    report.cameraDrops.wristRight,
  ) / Math.max(1, report.frameCount)
  const feedback = feedbackLevel(report.warnings)
  if (report.status === 'emergency' || feedback === 'bad' || lateRate >= 0.1 || cameraRate >= 0.05) return 'rerecord'
  if (feedback === 'warn' || lateRate >= 0.05 || cameraRate >= 0.02 || (report.maxSkewMs ?? 0) >= 300 || Math.max(report.maxForceLeft, report.maxForceRight) > 4) return 'review'
  return 'accept'
}

function recommendationText(recommendation: Recommendation) {
  if (recommendation === 'rerecord') return { label: '建议重录', tone: 'bad' as MetricTone, detail: '关键反馈、时序或相机质量未达到训练要求。' }
  if (recommendation === 'review') return { label: '建议复核', tone: 'warn' as MetricTone, detail: '数据可能可用，但请先检查异常原因和动作画面。' }
  return { label: '可以接受', tone: 'good' as MetricTone, detail: '当前关键指标未触发重录条件。' }
}

function QualityMetric({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: MetricTone }) {
  return (
    <div style={{ border: '1px solid', borderRadius: 6, padding: '9px 10px', minWidth: 0, ...toneStyle[tone] }}>
      <div style={{ fontSize: 11, opacity: 0.8 }}>{label}</div>
      <strong style={{ display: 'block', fontSize: 17, marginTop: 2 }}>{value}</strong>
      <div style={{ fontSize: 11, marginTop: 3, lineHeight: 1.4 }}>{detail}</div>
    </div>
  )
}

interface QualityReportModalProps {
  open: boolean
  report: RecordQualityReport | null
  onReRecord: () => void
  onAccept: () => void
}
/** 渲染当前界面单元，并连接所需数据。 */
export default function QualityReportModal({
  open,
  report,
  onReRecord,
  onAccept,
}: QualityReportModalProps) {
  if (!open || !report) return null
  const recommendation = recommendationFor(report)
  const recommendationInfo = recommendationText(recommendation)
  const lateRate = report.qualityAssessment?.lateRate ?? report.lateFrames / Math.max(1, report.frameCount)
  const cameraRates = report.qualityAssessment?.cameraDropRates ?? {
    global: report.cameraDrops.global / Math.max(1, report.frameCount),
    wrist_left: report.cameraDrops.wristLeft / Math.max(1, report.frameCount),
    wrist_right: report.cameraDrops.wristRight / Math.max(1, report.frameCount),
  }
  const maxCameraRate = Math.max(cameraRates.global ?? 0, cameraRates.wrist_left ?? 0, cameraRates.wrist_right ?? 0)
  const feedbackTone = feedbackLevel(report.warnings)
  const maxSkew = report.maxSkewMs ?? 0
  const maxFps = report.cameraMinFps ? Math.min(...Object.values(report.cameraMinFps).filter((value): value is number => typeof value === 'number')) : null
  const reasons = report.qualityAssessment?.reasons?.map((reason) => reason.message) ?? []
  const rawWarnings = report.warnings.filter((warning) => !reasons.includes(warning))
  return (
    <div className="ui-modal-mask" role="presentation">
      <div
        className="ui-modal"
        role="dialog"
        aria-label={`Episode #${String(report.index).padStart(3, '0')} 质量报告`}
      >
        <header className="ui-modal-head">
          <strong>Episode #{String(report.index).padStart(3, '0')} 质量报告</strong>
        </header>
        <div className="ui-modal-body">
          <div className="ui-alert" style={{ ...toneStyle[recommendationInfo.tone], marginBottom: 12 }}>
            <strong style={{ fontSize: 18 }}>{recommendationInfo.label}</strong>
            <div style={{ marginTop: 4 }}>{recommendationInfo.detail}</div>
            {reasons.length > 0 && (
              <ul style={{ margin: '7px 0 0', paddingLeft: 17 }}>
                {reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8, marginBottom: 12 }}>
            <QualityMetric
              label="迟帧率（最重要）"
              value={`${(lateRate * 100).toFixed(1)}%`}
              detail={`${report.lateFrames}/${report.frameCount}；≥10% 直接重录`}
              tone={metricTone(lateRate, 0.05, 0.1)}
            />
            <QualityMetric
              label="参与设备反馈"
              value={feedbackTone === 'bad' ? '异常' : feedbackTone === 'warn' ? '需复核' : '正常'}
              detail={feedbackTone === 'bad' ? '夹爪/运动反馈失效、组帧拒绝或控制断线' : feedbackTone === 'warn' ? '有采样对齐/新鲜度提醒，不等于设备完全失效' : '未发现关键反馈失效'}
              tone={feedbackTone}
            />
            <QualityMetric
              label="相机异常率"
              value={`${(maxCameraRate * 100).toFixed(1)}%`}
              detail={`全局 ${report.cameraDrops.global} / 左腕 ${report.cameraDrops.wristLeft} / 右腕 ${report.cameraDrops.wristRight}；≥5% 重录`}
              tone={metricTone(maxCameraRate, 0.02, 0.05)}
            />
            <QualityMetric
              label="时序偏差"
              value={`${maxSkew.toFixed(1)}ms`}
              detail={maxFps === null ? '≥300ms 复核；越小越好' : `最低相机 FPS ${maxFps.toFixed(1)}；低于25需复核`}
              tone={metricTone(maxSkew, 50, 300)}
            />
          </div>

          <table className="ui-table">
            <tbody>
              <tr><th>帧数</th><td>{report.frameCount}</td><th>时长</th><td>{report.durationS.toFixed(1)}s</td></tr>
              <tr><th>迟帧</th><td>{report.lateFrames}</td>
                <th>相机掉帧</th>
                <td>全局 {report.cameraDrops.global} / 左腕 {report.cameraDrops.wristLeft} / 右腕 {report.cameraDrops.wristRight}</td></tr>
              <tr><th>左臂峰值力</th><td>{report.maxForceLeft.toFixed(2)}N</td>
                <th>右臂峰值力</th><td>{report.maxForceRight.toFixed(2)}N</td></tr>
            </tbody>
          </table>

          {rawWarnings.length > 0 && (
            <details style={{ marginTop: 10 }}>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>查看底层诊断（{rawWarnings.length} 条）</summary>
              <div className="ui-alert ui-alert-warning" style={{ marginTop: 8 }}>
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {rawWarnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}
                </ul>
              </div>
            </details>
          )}
          <UiText secondary style={{ fontSize: 11 }}>选择「接受并继续」进入下一条，或「重录本条」丢弃本次采集。</UiText>
        </div>
        <div className="ui-modal-actions">
          <UiButton onClick={onReRecord}>重录本条</UiButton>
          <UiButton variant="primary" onClick={onAccept}>接受并继续</UiButton>
        </div>
      </div>
    </div>
  )
}
