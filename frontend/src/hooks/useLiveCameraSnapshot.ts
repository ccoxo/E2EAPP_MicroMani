import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiBase, mockMode } from '../api'
import type { CameraTelemetry } from '../types'

const CAMERA_RETRY_DELAY_MS = 2500

export function useLiveCameraSnapshot(cameraKey: CameraTelemetry['key'], health?: CameraTelemetry['health']) {
  const [streamNonce, setStreamNonce] = useState(() => Date.now())
  const [failedCamera, setFailedCamera] = useState<CameraTelemetry['key'] | null>(null)
  const refreshTimer = useRef<number | null>(null)
  const liveImageEnabled = !mockMode && health === 'ok' && failedCamera !== cameraKey

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimer.current === null) return
    window.clearTimeout(refreshTimer.current)
    refreshTimer.current = null
  }, [])

  useEffect(() => {
    return clearRefreshTimer
  }, [clearRefreshTimer])

  
  const snapshotUrl = useMemo(
    () => `${apiBase}/api/cameras/${cameraKey}/stream?t=${streamNonce}`,
    [cameraKey, streamNonce],
  )

  const handleLoad = useCallback(() => {
    clearRefreshTimer()
    setFailedCamera(null)
  }, [clearRefreshTimer])

  const handleError = useCallback(() => {
    setFailedCamera(cameraKey)
    clearRefreshTimer()
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null
      setFailedCamera(null)
      setStreamNonce(Date.now())
    }, CAMERA_RETRY_DELAY_MS)
  }, [cameraKey, clearRefreshTimer])

  return {
    liveImageEnabled,
    snapshotUrl,
    handleLoad,
    handleError,
  }
}
