/*
 * 阅读导航 01｜入口与界面
 * 职责：显示安全锁定覆盖层，并提供安全确认相关交互。
 * 先看：SafetyOverlay。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { ShieldAlert, X } from 'lucide-react'
import { useState } from 'react'
import { mockMode } from '../api'
import { useTelemetryStore } from '../stores/telemetry'
import { UiButton, UiTag, UiText } from './ui'
/** 渲染当前界面单元，并连接所需数据。 */
export function SafetyOverlay() {
  const dangerIndex = useTelemetryStore((state) => state.frame.dangerIndex)
  const setDangerOverride = useTelemetryStore((state) => state.setDangerOverride)
  const locked = useTelemetryStore((state) => state.controlSafety.emergencyRequested || Boolean(state.frame.forceStatus?.safety?.latched))
  const [testerOpen, setTesterOpen] = useState(false)

  return (
    <>
      <div aria-hidden="true" className="safety-overlay" style={{ borderColor: locked ? 'var(--color-danger, #d92d20)' : 'transparent', animationDuration: '1s' }} />
      {mockMode && <UiButton className="safety-test-button" icon={<ShieldAlert size={15} />} onClick={() => setTesterOpen(true)}>
        安全显示测试
      </UiButton>}
      {mockMode && testerOpen && (
        <div className="ui-modal-mask" role="presentation" onClick={() => setTesterOpen(false)}>
          <div className="ui-modal" role="dialog" aria-label="SafetyOverlay 测试" onClick={(event) => event.stopPropagation()}>
            <header className="ui-modal-head">
              <strong>SafetyOverlay 测试</strong>
              <button type="button" aria-label="关闭" onClick={() => setTesterOpen(false)}>
                <X size={16} />
              </button>
            </header>
            <div className="ui-modal-body">
              <UiTag tone="warning">仅测试模式</UiTag>
              <UiText secondary>
                模拟数值仅用于显示测试，不能解除急停保护或硬件安全锁存。
              </UiText>
              <input
                type="range"
                min={0}
                max={1.2}
                step={0.01}
                value={dangerIndex}
                onChange={(event) => setDangerOverride(Number(event.target.value))}
              />
              <div className="ui-modal-actions">
                <UiButton onClick={() => setDangerOverride(null)}>恢复实时数值</UiButton>
                <UiButton danger onClick={() => setDangerOverride(1.08)}>
                  模拟数值
                </UiButton>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
