import { Button, Input, Space, Table, Tag, Typography } from 'antd'
import { PauseCircle, PlayCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cancelFineTuneJobApi, fetchFineTuneJobs, startFineTuneJobApi, type FineTuneJobApi } from '../api'

export function FineTuneView() {
  const [datasetId, setDatasetId] = useState('micro_assembly_v1')
  const [baseModel, setBaseModel] = useState('act')
  const [jobs, setJobs] = useState<FineTuneJobApi[]>([])
  const [loading, setLoading] = useState(false)

  const refresh = () => {
    setLoading(true)
    void fetchFineTuneJobs().then(setJobs).finally(() => setLoading(false))
  }

  useEffect(() => {
    let cancelled = false
    void fetchFineTuneJobs().then((items) => {
      if (!cancelled) setJobs(items)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const startJob = () => {
    setLoading(true)
    void startFineTuneJobApi(datasetId, baseModel).then(refresh).finally(() => setLoading(false))
  }

  const cancelJob = (jobId: string) => {
    setLoading(true)
    void cancelFineTuneJobApi(jobId).then(refresh).finally(() => setLoading(false))
  }

  return (
    <div className="view-stack">
      <section className="page-header">
        <div>
          <Typography.Title level={2}>微调 Fine-tune</Typography.Title>
          <Typography.Text type="secondary">创建 LeRobot 数据集到策略模型的本地训练计划。</Typography.Text>
        </div>
        <Space>
          <Input value={datasetId} onChange={(event) => setDatasetId(event.target.value)} placeholder="dataset id" />
          <Input value={baseModel} onChange={(event) => setBaseModel(event.target.value)} placeholder="base model" />
          <Button type="primary" loading={loading} icon={<PlayCircle size={16} />} onClick={startJob}>创建任务</Button>
        </Space>
      </section>

      <section className="panel-surface">
        <Table
          rowKey="id"
          size="small"
          dataSource={jobs}
          pagination={false}
          columns={[
            { title: '任务', dataIndex: 'id' },
            { title: '数据集', dataIndex: 'datasetId' },
            { title: '基模型', dataIndex: 'baseModel' },
            { title: '状态', dataIndex: 'status', render: (status: string) => <Tag>{status}</Tag> },
            { title: '输出目录', dataIndex: 'outputDir' },
            {
              title: '操作',
              render: (_value, job) => (
                <Button size="small" icon={<PauseCircle size={14} />} onClick={() => cancelJob(job.id)}>
                  Cancel
                </Button>
              ),
            },
          ]}
        />
      </section>
    </div>
  )
}
