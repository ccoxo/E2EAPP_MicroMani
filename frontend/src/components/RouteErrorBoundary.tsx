import { Component, type ReactNode } from 'react'
import { UiButton, UiText, UiTitle } from './ui'
import { useTelemetryStore } from '../stores/telemetry'

/** 隔离业务页加载和渲染失败，让导航、遥测与全局急停继续挂载。 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error) {
    useTelemetryStore.getState().revokeControlLease(`页面显示异常，控制已暂停：${error.message}`)
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <section className="panel-surface view-stack" role="alert">
        <UiTitle level={2}>页面暂时无法显示</UiTitle>
        <UiText secondary>控制租约已撤销。可从侧栏查看其他页面；重新加载后需要重新核验并显式解除硬件安全锁定。</UiText>
        <div>
          <UiButton variant="primary" onClick={() => {
            this.setState({ failed: false })
            const store = useTelemetryStore.getState()
            store.stopBackend()
            store.startBackend()
          }}>
            重试页面并重新核验
          </UiButton>
        </div>
      </section>
    )
  }
}
