import { Component, type ReactNode } from 'react'
import { useTelemetryStore } from '../stores/telemetry'

/** 壳层各区域独立降级，诊断显示异常不得卸载导航和急停入口。 */
export class ModuleErrorBoundary extends Component<{
  name: string
  children: ReactNode
  fallback?: ReactNode
}, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error) {
    useTelemetryStore.getState().revokeControlLease(`${this.props.name}显示异常，控制已暂停：${error.message}`)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return this.props.fallback ?? (
      <div className="ui-alert ui-alert-warning" role="alert">
        {this.props.name}暂时不可用
        <button type="button" className="ui-btn ui-btn-text" onClick={() => this.setState({ failed: false })}>
          重试此模块
        </button>
      </div>
    )
  }
}
