/*
 * 阅读导航 01｜入口与界面
 * 职责：定义全局主题和页面路由；按 mockMode 启停模拟数据或后端遥测连接。
 * 先看：App。
 * 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。
 */
import { Suspense, lazy, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { mockMode } from './api'
import { AppLayout } from './components/AppLayout'
import { UiSpin } from './components/ui'
import { useTelemetryStore } from './stores/telemetry'
import { installControlFaultHandlers } from './utils/controlFaults'

// 路由级代码分割：首包只保留壳层，各业务页按需加载，降低切换与首屏成本。
const DashboardView = lazy(() => import('./views/DashboardView').then((module) => ({ default: module.DashboardView })))
const RecordPage = lazy(() => import('./views/RecordPage'))
const AutoView = lazy(() => import('./views/AutoView').then((module) => ({ default: module.AutoView })))
const DatasetView = lazy(() => import('./views/DatasetView').then((module) => ({ default: module.DatasetView })))
const ModelView = lazy(() => import('./views/ModelView').then((module) => ({ default: module.ModelView })))
const FineTuneView = lazy(() => import('./views/FineTuneView').then((module) => ({ default: module.FineTuneView })))
const SettingsView = lazy(() => import('./views/SettingsView').then((module) => ({ default: module.SettingsView })))

/** 路由切换时的轻量占位，避免整页空白。 */
function RouteFallback() {
  return (
    <div
      style={{
        display: 'grid',
        placeItems: 'center',
        minHeight: 240,
        color: '#7a8b9c',
        fontSize: 13,
      }}
    >
      <UiSpin tip="页面加载中…" />
    </div>
  )
}
/** 渲染当前界面单元，并连接所需数据。 */
export default function App() {
  const startMock = useTelemetryStore((state) => state.startMock)
  const stopMock = useTelemetryStore((state) => state.stopMock)
  const startBackend = useTelemetryStore((state) => state.startBackend)
  const stopBackend = useTelemetryStore((state) => state.stopBackend)

  // 数据连接由应用根组件统一持有；返回对应停止函数，使卸载与模式切换能够释放订阅。
  useEffect(() => {
    if (mockMode) {
      startMock()
      return stopMock
    }
    const removeFaultHandlers = installControlFaultHandlers((reason) => useTelemetryStore.getState().revokeControlLease(reason))
    startBackend()
    return () => {
      removeFaultHandlers()
      stopBackend()
    }
  }, [startBackend, startMock, stopBackend, stopMock])

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route
            index
            element={
              <Suspense fallback={<RouteFallback />}>
                <DashboardView />
              </Suspense>
            }
          />
          <Route
            path="record"
            element={
              <Suspense fallback={<RouteFallback />}>
                <RecordPage />
              </Suspense>
            }
          />
          <Route
            path="auto"
            element={
              <Suspense fallback={<RouteFallback />}>
                <AutoView />
              </Suspense>
            }
          />
          <Route
            path="dataset"
            element={
              <Suspense fallback={<RouteFallback />}>
                <DatasetView />
              </Suspense>
            }
          />
          <Route
            path="model"
            element={
              <Suspense fallback={<RouteFallback />}>
                <ModelView />
              </Suspense>
            }
          />
          <Route
            path="fine-tune"
            element={
              <Suspense fallback={<RouteFallback />}>
                <FineTuneView />
              </Suspense>
            }
          />
          <Route
            path="settings"
            element={
              <Suspense fallback={<RouteFallback />}>
                <SettingsView />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
