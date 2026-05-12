import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBase, mockMode } from '../api'
import type { CameraTelemetry } from '../types'

const CAMERA_REFRESH_DELAY_MS = 120
const CAMERA_RETRY_DELAY_MS = 2500

function cameraSnapshotUrl(cameraKey: CameraTelemetry['key']) {
  return `${apiBase}/api/cameras/${cameraKey}/snapshot?t=${Date.now()}`
}

export function useLiveCameraSnapshot(cameraKey: CameraTelemetry['key'], health?: CameraTelemetry['health']) {
  const [snapshotUrl, setSnapshotUrl] = useState<string | null>(null)
  const refreshTimer = useRef<number | null>(null)
  const canRequestSnapshot = health === 'ok' || health === 'checking'
  const liveImageEnabled = !mockMode && canRequestSnapshot && snapshotUrl !== null
  const handleLoad = useCallback(() => undefined, [])
  const handleError = useCallback(() => undefined, [])

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimer.current === null) return
    window.clearTimeout(refreshTimer.current)
    refreshTimer.current = null
  }, [])

  useEffect(() => {
    clearRefreshTimer()
    setSnapshotUrl(null)
    if (mockMode || !canRequestSnapshot) return clearRefreshTimer

    let cancelled = false

    const schedule = (delayMs: number) => {
      refreshTimer.current = window.setTimeout(loadNextSnapshot, delayMs)
    }

    const loadNextSnapshot = () => {
      refreshTimer.current = null
      const nextUrl = cameraSnapshotUrl(cameraKey)
      const image = new Image()
      image.onload = () => {
        if (cancelled) return
        setSnapshotUrl(nextUrl)
        schedule(CAMERA_REFRESH_DELAY_MS)
      }
      image.onerror = () => {
        if (cancelled) return
        schedule(CAMERA_RETRY_DELAY_MS)
      }
      image.src = nextUrl
    }

    loadNextSnapshot()

    return () => {
      cancelled = true
      clearRefreshTimer()
    }
  }, [cameraKey, canRequestSnapshot, clearRefreshTimer])

  return {
    liveImageEnabled,
    snapshotUrl: snapshotUrl ?? '',
    handleLoad,
    handleError,
  }
}
