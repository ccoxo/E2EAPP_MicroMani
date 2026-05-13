import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBase, mockMode } from '../api'
import type { CameraTelemetry } from '../types'

const CAMERA_RETRY_DELAY_MS = 2500
const CAMERA_SNAPSHOT_INTERVAL_MS = 250
const CAMERA_FETCH_TIMEOUT_MS = 1800
const CAMERA_REFRESH_EVENT = 'appstation-camera-refresh'

interface CameraRefreshEventDetail {
  camera?: CameraTelemetry['key']
}

export function useLiveCameraSnapshot(cameraKey: CameraTelemetry['key'], health?: CameraTelemetry['health']) {
  const [snapshotUrl, setSnapshotUrl] = useState<string | null>(null)
  const [failedCamera, setFailedCamera] = useState<CameraTelemetry['key'] | null>(null)
  const [imageLoaded, setImageLoaded] = useState(false)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const frameTimer = useRef<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const objectUrls = useRef<Set<string>>(new Set())
  const liveImageEnabled = !mockMode && health !== 'pending' && health !== 'error'
  const previewHealth: CameraTelemetry['health'] = !liveImageEnabled
    ? health === 'error'
      ? 'error'
      : 'pending'
    : failedCamera === cameraKey
      ? 'error'
      : imageLoaded
        ? 'ok'
        : 'checking'

  const clearFrameTimer = useCallback(() => {
    if (frameTimer.current === null) return
    window.clearTimeout(frameTimer.current)
    frameTimer.current = null
  }, [])

  const clearAbort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const revokeObjectUrlSoon = useCallback((url: string) => {
    window.setTimeout(() => {
      URL.revokeObjectURL(url)
      objectUrls.current.delete(url)
    }, 1000)
  }, [])

  const clearObjectUrls = useCallback(() => {
    objectUrls.current.forEach((url) => URL.revokeObjectURL(url))
    objectUrls.current.clear()
  }, [])

  const clearSnapshotUrl = useCallback(() => {
    setSnapshotUrl((previous) => {
      if (previous) revokeObjectUrlSoon(previous)
      return null
    })
  }, [revokeObjectUrlSoon])

  const refreshStream = useCallback(() => {
    clearFrameTimer()
    clearAbort()
    clearSnapshotUrl()
    setFailedCamera(null)
    setImageLoaded(false)
    setRefreshNonce((nonce) => nonce + 1)
  }, [clearAbort, clearFrameTimer, clearSnapshotUrl])

  useEffect(() => {
    return () => {
      clearFrameTimer()
      clearAbort()
      clearObjectUrls()
    }
  }, [clearAbort, clearFrameTimer, clearObjectUrls])

  useEffect(() => {
    const handleRefresh = (event: Event) => {
      const detail = (event as CustomEvent<CameraRefreshEventDetail>).detail
      if (detail?.camera && detail.camera !== cameraKey) return
      refreshStream()
    }
    window.addEventListener(CAMERA_REFRESH_EVENT, handleRefresh)
    return () => window.removeEventListener(CAMERA_REFRESH_EVENT, handleRefresh)
  }, [cameraKey, refreshStream])

  useEffect(() => {
    clearFrameTimer()
    clearAbort()
    if (!liveImageEnabled) {
      return undefined
    }

    let cancelled = false
    const requestSnapshot = async () => {
      clearFrameTimer()
      clearAbort()
      const controller = new AbortController()
      abortRef.current = controller
      const timeout = window.setTimeout(() => controller.abort(), CAMERA_FETCH_TIMEOUT_MS)
      try {
        const response = await fetch(`${apiBase}/api/cameras/${cameraKey}/snapshot?t=${Date.now()}`, {
          cache: 'no-store',
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`camera snapshot failed: ${response.status}`)
        const blob = await response.blob()
        if (cancelled) return
        const nextUrl = URL.createObjectURL(blob)
        objectUrls.current.add(nextUrl)
        setSnapshotUrl((previous) => {
          if (previous) revokeObjectUrlSoon(previous)
          return nextUrl
        })
        setFailedCamera(null)
        setImageLoaded(true)
        frameTimer.current = window.setTimeout(requestSnapshot, CAMERA_SNAPSHOT_INTERVAL_MS)
      } catch {
        if (cancelled) return
        setFailedCamera(cameraKey)
        setImageLoaded(false)
        frameTimer.current = window.setTimeout(requestSnapshot, CAMERA_RETRY_DELAY_MS)
      } finally {
        window.clearTimeout(timeout)
        if (abortRef.current === controller) {
          abortRef.current = null
        }
      }
    }

    void requestSnapshot()
    return () => {
      cancelled = true
      clearFrameTimer()
      clearAbort()
    }
  }, [cameraKey, clearAbort, clearFrameTimer, liveImageEnabled, refreshNonce, revokeObjectUrlSoon])

  const handleLoad = useCallback(() => {
    setFailedCamera(null)
    setImageLoaded(true)
  }, [])

  const handleError = useCallback(() => {
    setFailedCamera(cameraKey)
    setImageLoaded(false)
  }, [cameraKey])

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
