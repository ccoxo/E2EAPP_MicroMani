/*
 * 阅读导航 01｜入口与界面
 * 职责：展示自动执行状态，连接模型选择和自动运行控制。
 * 先看：AutoView。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { Pause, Play, Send, ShieldAlert, Square } from 'lucide-react'
import { useState } from 'react'
import { dispatchNextAutoAction, queueAutoAction } from '../api'
import { CameraPreview } from '../components/CameraPreview'
import { QueueChart } from '../components/Charts'
import { UiButton, UiProgress, UiSegmented, UiSpace, UiTag, UiText, UiTitle } from '../components/ui'
import { useTelemetryStore } from '../stores/telemetry'
import { controlSafetyBlockReason } from '../utils/controlSafety'
/** 渲染当前界面单元，并连接所需数据。 */
export function AutoView() {
  const cameras = useTelemetryStore((state) => state.frame.cameras)
  const queueLeft = useTelemetryStore((state) => state.frame.queueDepth.left)
  const queueRight = useTelemetryStore((state) => state.frame.queueDepth.right)
  const elapsedSec = useTelemetryStore((state) => state.frame.elapsedSec)
  const policyVram = useTelemetryStore(
    (state) => state.frame.processStatus.find((item) => item.name === 'policy')?.vramGb ?? 0,
  )
  const history = useTelemetryStore((state) => state.history)
  const autoRunning = useTelemetryStore((state) => state.autoRunning)
  const setAutoRunning = useTelemetryStore((state) => state.setAutoRunning)
  const triggerEmergencyStop = useTelemetryStore((state) => state.triggerEmergencyStop)
  const injectLog = useTelemetryStore((state) => state.injectLog)
  const controlBlockReason = useTelemetryStore((state) => controlSafetyBlockReason(state))
  const [cameraFilter, setCameraFilter] = useState<'all' | 'global' | 'wrist'>('all')
  const [algorithm, setAlgorithm] = useState<'ACT' | 'Diffusion' | 'OpenVLA'>('OpenVLA')
  const [inferenceMode, setInferenceMode] = useState<'sync' | 'async'>('async')
  const [taskPrompt, setTaskPrompt] = useState('Assemble ICF target component with force-limited contact.')

  const injectAction = async () => {
    const snapshot = useTelemetryStore.getState()
    const reason = controlSafetyBlockReason(snapshot)
    if (reason) {
      injectLog('WARNING', `动作注入受阻：${reason}`, '[ZMQ]')
      return
    }
    const generation = snapshot.controlSafety.generation
    try {
      await queueAutoAction({ side: 'left', axis: 'X', direction: 1, step: 50, speedMode: 'fine', maxVelocityUiPerSec: 50 })
      const current = useTelemetryStore.getState()
      const blocked = controlSafetyBlockReason(current)
      if (current.controlSafety.generation !== generation || blocked) {
        injectLog('WARNING', `动作注入已取消：${blocked ?? '期间发生安全事件，请重新操作'}`, '[ZMQ]')
        return
      }
      await dispatchNextAutoAction()
      injectLog('INFO', '动作注入请求已接受', '[ZMQ]')
    } catch (error) {
      injectLog('ERROR', `动作注入失败：${error instanceof Error ? error.message : String(error)}`, '[ZMQ]')
    }
  }

  const visibleCameras = cameras.filter((camera) => {
    if (cameraFilter === 'all') return true
    if (cameraFilter === 'global') return camera.key === 'global'
    return camera.key === 'wrist_left' || camera.key === 'wrist_right'
  })

  return (
    <div className="view-stack">
      <section className="page-header">
        <div>
          <UiTitle level={2}>自动执行 Auto</UiTitle>
          <UiText secondary>后端 PolicyServer、动作队列、安全限幅和 HAL 下发入口。</UiText>
        </div>
        <UiSpace wrap>
          <UiTag tone={autoRunning ? 'success' : 'muted'}>PolicyServer {autoRunning ? '运行' : '未启动'}</UiTag>
          <UiTag tone="processing">HAL gate</UiTag>
        </UiSpace>
      </section>

      <section className="split-grid split-grid-auto">
        <div className="panel-surface">
          <div className="section-title">
            <span>实时视觉</span>
            <UiSegmented
              value={cameraFilter}
              options={[
                { label: '全部', value: 'all' as const },
                { label: '全局', value: 'global' as const },
                { label: '腕部', value: 'wrist' as const },
              ]}
              onChange={setCameraFilter}
            />
          </div>
          <div className="camera-grid-layout">
            {visibleCameras.map((camera) => (
              <CameraPreview key={camera.key} camera={camera} compact />
            ))}
          </div>
          <QueueChart history={history} height={220} />
        </div>

        <div className="panel-surface auto-control">
          <div className="section-title">
            <span>执行控制</span>
            <UiTag tone="processing">ZMQ 8082 / 8083</UiTag>
          </div>
          <label>
            <span>算法</span>
            <UiSegmented
              value={algorithm}
              options={[
                { label: 'ACT', value: 'ACT' as const },
                { label: 'Diffusion', value: 'Diffusion' as const },
                { label: 'OpenVLA', value: 'OpenVLA' as const },
              ]}
              onChange={setAlgorithm}
            />
          </label>
          <label>
            <span>推理模式</span>
            <UiSegmented
              value={inferenceMode}
              options={[
                { label: '同步', value: 'sync' as const },
                { label: '异步 + RTC', value: 'async' as const },
              ]}
              onChange={setInferenceMode}
            />
          </label>
          <label>
            <span>任务指令 VLA</span>
            <textarea
              className="ui-textarea"
              rows={3}
              value={taskPrompt}
              onChange={(event) => setTaskPrompt(event.target.value)}
            />
          </label>
          <div className="queue-meters">
            <UiProgress percent={queueLeft} format={(v) => `左臂 ${v}%`} />
            <UiProgress percent={queueRight} status="exception" format={(v) => `右臂 ${v}%`} />
          </div>
          <div className="policy-metrics">
            <span>推理延迟 <b>{(88 + Math.sin(elapsedSec) * 9).toFixed(0)}ms</b></span>
            <span>控制频率 <b>30Hz</b></span>
            <span>VRAM <b>{policyVram.toFixed(1)}GB</b></span>
          </div>
          <UiSpace wrap>
            <UiButton variant="primary" icon={<Play size={16} />} disabled={Boolean(controlBlockReason)} title={controlBlockReason ?? undefined} onClick={() => setAutoRunning(true)}>
              启动
            </UiButton>
            <UiButton icon={<Pause size={16} />} onClick={() => setAutoRunning(false)}>
              暂停
            </UiButton>
            <UiButton icon={<Square size={16} />} onClick={() => setAutoRunning(false)}>
              停止
            </UiButton>
            <UiButton danger icon={<ShieldAlert size={16} />} onClick={triggerEmergencyStop}>
              急停
            </UiButton>
            <UiButton
              icon={<Send size={16} />}
              disabled={Boolean(controlBlockReason)}
              title={controlBlockReason ?? undefined}
              onClick={() => void injectAction()}
            >
              注入动作
            </UiButton>
          </UiSpace>
          {controlBlockReason && <div role="status"><UiText secondary>{controlBlockReason}</UiText></div>}
        </div>
      </section>
    </div>
  )
}
