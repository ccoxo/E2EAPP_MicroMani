import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { mockMode } from './api'
import { AppLayout } from './components/AppLayout'
import { useTelemetryStore } from './stores/telemetry'
import { AutoView } from './views/AutoView'
import { DashboardView } from './views/DashboardView'
import { DatasetView } from './views/DatasetView'
import { FineTuneView } from './views/FineTuneView'
import { ModelView } from './views/ModelView'
import RecordPage from './views/RecordPage'
import { SettingsView } from './views/SettingsView'

export default function App() {
  const startMock = useTelemetryStore((state) => state.startMock)
  const stopMock = useTelemetryStore((state) => state.stopMock)
  const startBackend = useTelemetryStore((state) => state.startBackend)
  const stopBackend = useTelemetryStore((state) => state.stopBackend)

  useEffect(() => {
    if (mockMode) {
      startMock()
      return stopMock
    }
    startBackend()
    return stopBackend
  }, [startBackend, startMock, stopBackend, stopMock])

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#2f6fed',
          colorSuccess: '#0f9f6e',
          colorWarning: '#d98400',
          colorError: '#d83a52',
          colorInfo: '#0d7c8a',
          borderRadius: 6,
          fontFamily:
            'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        },
        components: {
          Button: { borderRadius: 6, controlHeight: 32 },
          Card: { borderRadiusLG: 8 },
          Modal: { borderRadiusLG: 8 },
          Tabs: { horizontalMargin: '0 0 12px 0' },
        },
      }}
    >
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<DashboardView />} />
            <Route path="record" element={<RecordPage />} />
            <Route path="auto" element={<AutoView />} />
            <Route path="dataset" element={<DatasetView />} />
            <Route path="model" element={<ModelView />} />
            <Route path="fine-tune" element={<FineTuneView />} />
            <Route path="settings" element={<SettingsView />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
