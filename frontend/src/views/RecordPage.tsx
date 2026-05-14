import { useCallback, useEffect, useState } from 'react'
import CameraPanel from '../components/record/CameraPanel'
import EpisodeControlPanel from '../components/record/EpisodeControlPanel'
import EpisodeHistoryCard from '../components/record/EpisodeHistoryCard'
import HardwareStatusCard from '../components/record/HardwareStatusCard'
import PreCheckModal from '../components/record/PreCheckModal'
import QualityReportModal from '../components/record/QualityReportModal'
import RecordTelemetryPanel from '../components/record/RecordTelemetryPanel'
import SafetyMonitorCard from '../components/record/SafetyMonitorCard'
import { useTelemetryStore } from '../stores/telemetry'

export default function RecordPage() {
  const [preCheckVisible, setPreCheckVisible] = useState(false)

  const latestReport = useTelemetryStore((s) => s.recordSession.latestQualityReport)
  const datasetName = useTelemetryStore((s) => s.recordSession.datasetName)
  const task = useTelemetryStore((s) => s.recordSession.task)
  const startRecordSession = useTelemetryStore((s) => s.startRecordSession)
  const saveRecordEpisode = useTelemetryStore((s) => s.saveRecordEpisode)
  const discardRecordEpisode = useTelemetryStore((s) => s.discardRecordEpisode)
  const finishRecordSession = useTelemetryStore((s) => s.finishRecordSession)
  const skipRecordReset = useTelemetryStore((s) => s.skipRecordReset)
  const tareRecordForceSensors = useTelemetryStore((s) => s.tareRecordForceSensors)
  const toggleRecordClutch = useTelemetryStore((s) => s.toggleRecordClutch)
  const setRecordSpeedMode = useTelemetryStore((s) => s.setRecordSpeedMode)
  const homeRecordArms = useTelemetryStore((s) => s.homeRecordArms)
  const acceptRecordQualityReport = useTelemetryStore((s) => s.acceptRecordQualityReport)
  const rejectRecordQualityReport = useTelemetryStore((s) => s.rejectRecordQualityReport)

  const handleCreateSession = useCallback(() => startRecordSession(datasetName, task), [datasetName, task, startRecordSession])
  const handleSave = useCallback(() => saveRecordEpisode(), [saveRecordEpisode])
  const handleDiscard = useCallback(() => discardRecordEpisode(), [discardRecordEpisode])
  const handleStopSession = useCallback(() => finishRecordSession(), [finishRecordSession])
  const handleSkipReset = useCallback(() => skipRecordReset(), [skipRecordReset])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const tag = target.tagName
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || target.isContentEditable) return
      if (preCheckVisible || latestReport) return

      const phase = useTelemetryStore.getState().recordSession.phase

      switch (e.key) {
        case ' ':
        case 'ArrowRight':
          e.preventDefault()
          if (phase === 'recording') handleSave()
          if (phase === 'resetting') handleSkipReset()
          break

        case 'ArrowLeft':
          e.preventDefault()
          if (phase === 'recording') handleDiscard()
          break

        case 'Escape':
          e.preventDefault()
          if (phase !== 'idle' && phase !== 'finishing') handleStopSession()
          break

        case 'Control':
          toggleRecordClutch()
          break

        case '1':
          setRecordSpeedMode('coarse')
          break
        case '2':
          setRecordSpeedMode('medium')
          break
        case '3':
          setRecordSpeedMode('fine')
          break

        case 't':
        case 'T':
          if (phase === 'idle' || phase === 'resetting') tareRecordForceSensors()
          break

        case 'r':
        case 'R':
          if (phase === 'idle' || phase === 'resetting') homeRecordArms()
          break

        default:
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [
    preCheckVisible,
    latestReport,
    handleSave,
    handleDiscard,
    handleStopSession,
    handleSkipReset,
    homeRecordArms,
    setRecordSpeedMode,
    tareRecordForceSensors,
    toggleRecordClutch,
  ])

  return (
    <div className="record-page">
      <div className="record-page-layout">
        <div className="record-page-main">
          <CameraPanel />
          <RecordTelemetryPanel />
          <EpisodeControlPanel onStartSession={() => setPreCheckVisible(true)} />
        </div>

        <div className="record-page-side">
          <SafetyMonitorCard />
          <HardwareStatusCard />
          <EpisodeHistoryCard />
        </div>
      </div>

      <PreCheckModal
        open={preCheckVisible}
        onConfirm={() => {
          setPreCheckVisible(false)
          handleCreateSession()
        }}
        onCancel={() => setPreCheckVisible(false)}
      />
      <QualityReportModal
        open={Boolean(latestReport)}
        report={latestReport}
        onReRecord={rejectRecordQualityReport}
        onAccept={acceptRecordQualityReport}
      />
    </div>
  )
}
