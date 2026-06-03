import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiBase, mockMode } from '../api'
import type { CameraTelemetry } from '../types'

const CAMERA_RETRY_DELAY_MS = 2500
const CAMERA_REFRESH_EVENT = 'appstation-camera-refresh'

interface CameraRefreshEventDetail {
  camera?: CameraTelemetry['key']
}

export function useLiveCameraSnapshot(cameraKey: CameraTelemetry['key'], health?: CameraTelemetry['health']) {
  const [failedStreamUrl, setFailedStreamUrl] = useState<string | null>(null)
  const [loadedStreamUrl, setLoadedStreamUrl] = useState<string | null>(null)
  const [streamNonce, setStreamNonce] = useState(0)
  const [manualRefreshCamera, setManualRefreshCamera] = useState<CameraTelemetry['key'] | null>(null)
  const retryTimer = useRef<number | null>(null)

  const streamEnabled = !mockMode && (health !== 'error' || manualRefreshCamera === cameraKey)
  const liveImageEnabled = streamEnabled
  const snapshotUrl = useMemo(
    () => (streamEnabled ? `${apiBase}/api/cameras/${cameraKey}/stream?t=${streamNonce}` : null),
    [cameraKey, streamEnabled, streamNonce],
  )
  const streamFailed = Boolean(snapshotUrl && failedStreamUrl === snapshotUrl)
  const streamLoaded = Boolean(snapshotUrl && loadedStreamUrl === snapshotUrl)
  const previewHealth: CameraTelemetry['health'] = !streamEnabled
    ? health === 'error'
      ? 'error'
      : 'pending'
    : streamFailed
      ? 'warn'
      : streamLoaded
        ? 'ok'
        : 'checking'

  const clearRetryTimer = useCallback(() => {
    if (retryTimer.current === null) return
    window.clearTimeout(retryTimer.current)
    retryTimer.current = null
  }, [])

  const refreshStream = useCallback(() => {
    clearRetryTimer()
    setFailedStreamUrl(null)
    setLoadedStreamUrl(null)
    setManualRefreshCamera(cameraKey)
    setStreamNonce((nonce) => nonce + 1)
  }, [cameraKey, clearRetryTimer])

  useEffect(() => {
    return () => clearRetryTimer()
  }, [clearRetryTimer])

  useEffect(() => {
    const handleRefresh = (event: Event) => {
      const detail = (event as CustomEvent<CameraRefreshEventDetail>).detail
      if (detail?.camera && detail.camera !== cameraKey) return
      refreshStream()
    }
    window.addEventListener(CAMERA_REFRESH_EVENT, handleRefresh)
    return () => window.removeEventListener(CAMERA_REFRESH_EVENT, handleRefresh)
  }, [cameraKey, refreshStream])

  const handleLoad = useCallback(() => {
    clearRetryTimer()
    setFailedStreamUrl(null)
    setLoadedStreamUrl(snapshotUrl)
  }, [clearRetryTimer, snapshotUrl])

  const handleError = useCallback(() => {
    setFailedStreamUrl(snapshotUrl)
    setLoadedStreamUrl(null)
    clearRetryTimer()
    if (health !== 'error') {
      retryTimer.current = window.setTimeout(() => {
        setStreamNonce((nonce) => nonce + 1)
      }, CAMERA_RETRY_DELAY_MS)
    }
  }, [clearRetryTimer, health, snapshotUrl])

  return {
    liveImageEnabled,
    previewHealth,
    snapshotUrl,
    handleLoad,
    handleError,
  }
}

export function refreshCameraStream(camera?: CameraTelemetry['key']) {
  window.dispatchEvent(new CustomEvent<CameraRefreshEventDetail>(CAMERA_REFRESH_EVENT, { detail: { camera } }))
}
