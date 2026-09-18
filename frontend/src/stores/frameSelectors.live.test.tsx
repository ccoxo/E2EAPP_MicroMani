import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { CameraPreview } from '../components/CameraPreview'
import { camerasEqual, useFrameField } from './frameSelectors'
import { useTelemetryStore } from './telemetry'

const initialState = useTelemetryStore.getState()
afterEach(() => { cleanup(); useTelemetryStore.setState(initialState, true) })

function LiveCamera() {
  const cameras = useFrameField(frame => frame.cameras, camerasEqual)
  return <CameraPreview camera={cameras[0]} compact />
}

it('只有时钟偏差或相机名称变化时，轻量订阅仍更新预览告警和标签', () => {
  const camera = { ...initialState.frame.cameras[0], timestampSkewMs: 0 }
  useTelemetryStore.setState({ frame: { ...initialState.frame, cameras: [camera] } })
  render(<LiveCamera />)
  expect(screen.getByText('0.0 ms')).toBeInTheDocument()
  act(() => useTelemetryStore.setState({ frame: { ...initialState.frame, cameras: [{ ...camera, timestampSkewMs: 99, label: '更新后的相机' }] } }))
  expect(screen.getByText('99.0 ms')).toBeInTheDocument()
  expect(screen.getByText('更新后的相机')).toBeInTheDocument()
})
