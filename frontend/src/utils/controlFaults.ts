/** 未捕获脚本错误不能继续替执行侧续租；监听与根组件连接生命周期一起释放。 */
export function installControlFaultHandlers(revoke: (reason: string) => void) {
  const onError = (event: ErrorEvent) => revoke(`页面脚本异常，控制已暂停：${event.message || '未知错误'}`)
  const onRejection = (event: PromiseRejectionEvent) => revoke(`页面异步异常，控制已暂停：${String(event.reason)}`)
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onRejection)
  return () => {
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onRejection)
  }
}
