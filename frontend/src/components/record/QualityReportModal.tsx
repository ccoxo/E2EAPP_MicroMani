import { Alert, Button, Descriptions, Modal } from 'antd'
import type { RecordQualityReport } from '../../types'

interface QualityReportModalProps {
  open: boolean
  report: RecordQualityReport | null
  onReRecord: () => void
  onAccept: () => void
}

export default function QualityReportModal({
  open,
  report,
  onReRecord,
  onAccept,
}: QualityReportModalProps) {
  return (
    <Modal
      title={report ? `Episode #${String(report.index).padStart(3, '0')} 质量报告` : '质量报告'}
      open={open}
      closable={false}
      maskClosable={false}
      footer={[
        <Button key="rerecord" onClick={onReRecord}>
          重录本条
        </Button>,
        <Button key="accept" type="primary" onClick={onAccept}>
          接受并继续
        </Button>,
      ]}
    >
      {report && (
        <>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="帧数">{report.frameCount}</Descriptions.Item>
            <Descriptions.Item label="时长">{report.durationS.toFixed(1)}s</Descriptions.Item>
            <Descriptions.Item label="迟帧">{report.lateFrames}</Descriptions.Item>
            <Descriptions.Item label="相机掉帧">
              全局 {report.cameraDrops.global} / 左腕 {report.cameraDrops.wristLeft} / 右腕 {report.cameraDrops.wristRight}
            </Descriptions.Item>
            <Descriptions.Item label="左臂峰值力">
              {report.maxForceLeft.toFixed(2)}N
            </Descriptions.Item>
            <Descriptions.Item label="右臂峰值力">
              {report.maxForceRight.toFixed(2)}N
            </Descriptions.Item>
          </Descriptions>

          {report.warnings.length > 0 && (
            <Alert
              type="warning"
              message="检测到以下问题"
              description={
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {report.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              }
              style={{ marginTop: 12 }}
            />
          )}

          {report.warnings.length === 0 && report.passed && (
            <Alert type="success" message="数据质量良好，可以继续。" style={{ marginTop: 12 }} />
          )}
        </>
      )}
    </Modal>
  )
}
