/*
 * 阅读导航 01｜入口与界面
 * 职责：展示录制 episode 的质量结果与相关统计。
 * 先看：QualityReportModalProps → QualityReportModal。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import type { RecordQualityReport } from '../../types'
import { UiButton, UiText } from '../ui'

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

          {report.warnings.length > 0 && (
            <div className="ui-alert ui-alert-warning">
              <strong>检测到以下问题</strong>
              <ul style={{ margin: '6px 0 0', paddingLeft: 16 }}>
                {report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {report.warnings.length === 0 && report.passed && (
            <div className="ui-alert ui-alert-success">数据质量良好，可以继续。</div>
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
