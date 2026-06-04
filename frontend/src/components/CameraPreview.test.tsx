import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CameraPreview } from './CameraPreview'
import type { CameraTelemetry } from '../types'

vi.mock('../api', () => ({
  apiBase: 'http://preview.test',
  mockMode: false,
}))

const camera: CameraTelemetry = {
  key: 'global',
  label: 'Global Camera',
  fps: 30,
  timestampSkewMs: 0,
  frameAgeMs: 8,
  health: 'ok',
}

describe('CameraPreview', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('uses the MJPEG stream endpoint instead of polling snapshots', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={camera} compact />)

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByTestId('camera-image-global')).toHaveAttribute(
      'src',
      'http://preview.test/api/cameras/global/stream?t=0',
    )
    expect(screen.queryByText('No signal')).not.toBeInTheDocument()
  })

  it('refreshes the MJPEG stream URL without showing the placeholder', async () => {
    render(<CameraPreview camera={camera} compact />)

    expect(screen.getByTestId('camera-image-global')).toHaveAttribute(
      'src',
      'http://preview.test/api/cameras/global/stream?t=0',
    )

    act(() => {
      window.dispatchEvent(new CustomEvent('appstation-camera-refresh', { detail: { camera: 'global' } }))
    })

    await waitFor(() =>
      expect(screen.getByTestId('camera-image-global')).toHaveAttribute(
        'src',
        'http://preview.test/api/cameras/global/stream?t=1',
      ),
    )
    expect(screen.queryByText('No signal')).not.toBeInTheDocument()
  })

  it('requests a stream while camera telemetry is pending', () => {
    render(<CameraPreview camera={{ ...camera, health: 'pending' }} compact />)

    expect(screen.getByTestId('camera-image-global')).toHaveAttribute(
      'src',
      'http://preview.test/api/cameras/global/stream?t=0',
    )
  })

  it('allows a manual refresh to probe an errored camera', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={{ ...camera, health: 'error' }} compact />)

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId('camera-image-global')).not.toBeInTheDocument()
    expect(screen.getByText('No signal')).toBeInTheDocument()

    act(() => {
      window.dispatchEvent(new CustomEvent('appstation-camera-refresh', { detail: { camera: 'global' } }))
    })

    await waitFor(() =>
      expect(screen.getByTestId('camera-image-global')).toHaveAttribute(
        'src',
        'http://preview.test/api/cameras/global/stream?t=1',
      ),
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reports ok only after the stream image element loads', async () => {
    const handlePreviewHealthChange = vi.fn()

    render(<CameraPreview camera={camera} compact onPreviewHealthChange={handlePreviewHealthChange} />)

    const image = screen.getByTestId('camera-image-global')
    expect(handlePreviewHealthChange).not.toHaveBeenCalledWith('ok')

    fireEvent.load(image)

    await waitFor(() => expect(handlePreviewHealthChange).toHaveBeenCalledWith('ok'))
  })
})
