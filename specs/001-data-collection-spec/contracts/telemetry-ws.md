# Contract: WebSocket Telemetry Compatibility

## /ws TelemetryFrame

Existing fields remain compatible:

- `timestamp`
- `elapsedSec`
- `jointPositions`
- `gripperPositions`
- `motionEnabled`
- `forceLeft`
- `forceRight`
- `dangerIndex`
- `recording`
- `episodeCount`
- `frameCount`
- `halOk`
- `wsOk`
- `cameras`
- `teleopHands`
- `queueDepth`
- `resource`
- `processStatus`

## Compatibility Rules

- Do not remove existing telemetry fields.
- Do not change `jointPositions` display units: translation in um, rotation in degree.
- Do not expose physical axis ids to frontend consumers.
- New fields must be additive and safe for older frontend versions to ignore.
