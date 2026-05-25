import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

function okSnapshotResponse() {
  return {
    ok: true,
    blob: async () => new Blob(['jpeg'], { type: 'image/jpeg' }),
  } as Response
}

describe('CameraPreview', () => {
  beforeEach(() => {
    let frame = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => {
        frame += 1
        return `blob:frame-${frame}`
      }),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps the last successful frame visible while a manual refresh is fetching', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okSnapshotResponse())
      .mockImplementationOnce(() => new Promise<Response>(() => undefined))
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={camera} compact />)

    const image = await screen.findByTestId('camera-image-global')
    expect(image).toHaveAttribute('src', 'blob:frame-1')

    act(() => {
      window.dispatchEvent(new CustomEvent('appstation-camera-refresh', { detail: { camera: 'global' } }))
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(screen.getByTestId('camera-image-global')).toHaveAttribute('src', 'blob:frame-1')
    expect(screen.queryByText('No signal')).not.toBeInTheDocument()
  })

  it('keeps the last successful frame visible when a refresh request fails', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(okSnapshotResponse()).mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={camera} compact />)

    const image = await screen.findByTestId('camera-image-global')
    expect(image).toHaveAttribute('src', 'blob:frame-1')

    act(() => {
      window.dispatchEvent(new CustomEvent('appstation-camera-refresh', { detail: { camera: 'global' } }))
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(screen.getByTestId('camera-image-global')).toHaveAttribute('src', 'blob:frame-1')
    expect(screen.queryByText('No signal')).not.toBeInTheDocument()
  })

  it('requests snapshots while camera telemetry is pending', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(okSnapshotResponse())
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={{ ...camera, health: 'pending' }} compact />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('camera-image-global')).toHaveAttribute('src', 'blob:frame-1')
  })

  it('allows a manual refresh to probe an errored camera', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(okSnapshotResponse())
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={{ ...camera, health: 'error' }} compact />)

    expect(fetchMock).not.toHaveBeenCalled()

    act(() => {
      window.dispatchEvent(new CustomEvent('appstation-camera-refresh', { detail: { camera: 'global' } }))
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('camera-image-global')).toHaveAttribute('src', 'blob:frame-1')
  })

  it('reports ok only after the image element loads', async () => {
    const handlePreviewHealthChange = vi.fn()
    const fetchMock = vi.fn().mockResolvedValueOnce(okSnapshotResponse())
    vi.stubGlobal('fetch', fetchMock)

    render(<CameraPreview camera={camera} compact onPreviewHealthChange={handlePreviewHealthChange} />)

    const image = await screen.findByTestId('camera-image-global')
    expect(handlePreviewHealthChange).not.toHaveBeenCalledWith('ok')

    fireEvent.load(image)

    await waitFor(() => expect(handlePreviewHealthChange).toHaveBeenCalledWith('ok'))
  })
})
