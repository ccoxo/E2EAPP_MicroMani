# Contract: Recording and Dataset APIs

## POST /api/record/session/create

**Request**

```json
{
  "dataset_name": "string",
  "task": "string"
}
```

**Response Data**

```json
{
  "sessionId": "string",
  "datasetId": "string",
  "datasetRoot": "string",
  "episodeIndex": 0,
  "recordFps": 30,
  "format": "lerobot-v3-native | fallback",
  "features": {}
}
```

## POST /api/record/episode/save

**Response Data**

```json
{
  "episode": {
    "episodeIndex": 0,
    "frames": 600,
    "durationSec": 20.0
  }
}
```

## POST /api/record/episode/discard

Discard current or last unacceptable episode without exposing it as available training data.

## POST /api/record/session/finish

Finish the active recording session. Unsaved active episode is discarded.

## GET /api/record/status

Must remain compatible with existing frontend recording state. New schema fields are additive.

## GET /api/datasets

Dataset summary must include:

- dataset id and display name
- root path
- format
- fps
- episode count
- visible episode list

## GET /api/datasets/{dataset_id}/episodes/{episode_id}

Episode detail must expose enough metadata to validate standard feature shape.

## GET /api/datasets/{dataset_id}/frame_image

Returns a sampled image for one of:

- `observation.images.global`
- `observation.images.wrist_left`
- `observation.images.wrist_right`
