from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.dataset_recorder import DatasetRecorderService, TimedRingBuffer, TimedSample


def hal_motion_fixture(timestamp_ms: int = 1234) -> dict[str, object]:
    return {
        "timestamp_ms": timestamp_ms,
        "received_monotonic_ms": timestamp_ms,
        "estop_active": False,
        "positions": [0.0] * 12,
        "pulses": [0.0] * 12,
        "enabled": [True] * 12,
    }


def omega_state_fixture(timestamp_ms: int = 1234) -> dict[str, object]:
    return {
        "timestamp_ms": timestamp_ms,
        "received_monotonic_ms": timestamp_ms,
        "hands": [
            {"side": "left", "connected": True, "openId": 0, "lastReadOk": True},
            {"side": "right", "connected": False, "openId": 1, "lastReadOk": False},
        ],
    }


def source_sample_fixture(source: str, timestamp_s: float = 1.0) -> dict[str, object]:
    return {
        "source": source,
        "monotonic_s": timestamp_s,
        "ok": True,
        "message": "",
        "timeout": False,
        "stale": False,
        "cacheUsed": False,
    }


def force_source_fixture(timestamp_s: float = 1.0) -> dict[str, object]:
    return {
        **source_sample_fixture("force", timestamp_s),
        "left": [0.0] * 6,
        "right": [0.0] * 6,
        "leftWindow": [[0.0] * 6],
        "rightWindow": [[0.0] * 6],
    }


def camera_source_fixture(timestamp_s: float = 1.0) -> dict[str, object]:
    return {
        **source_sample_fixture("camera", timestamp_s),
        "cacheUsedByCamera": {"global": False, "wrist_left": False, "wrist_right": False},
    }


def test_dataset_recorder_declares_and_writes_motion_pulses() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "PULSE_FEATURE_NAMES" in source
    assert '"observation.pulses": motion_pulses' in source
    assert '"observation.pulses": self._np_float32(frame["observation.pulses"])' in source
    assert '"observation.pulses": {"dtype": "float32", "shape": (12,)' in source


def test_dataset_recorder_declares_v3_state_and_action_shapes() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert '"observation.state": {"dtype": "float32", "shape": (14,)' in source
    assert '"action": {"dtype": "float32", "shape": (14,)' in source
    assert '"observation.gripper": {"dtype": "float32"' not in source


def test_dataset_recorder_native_features_follow_v3_contract() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._native_use_videos = True

    features = recorder._native_features({})

    assert features["observation.state"]["shape"] == (14,)
    assert features["action"]["shape"] == (14,)
    assert features["observation.images.global"]["dtype"] == "video"
    assert features["observation.images.global"]["shape"] == (480, 640, 3)
    assert "observation.gripper" not in features


def test_timed_ring_buffer_nearest_respects_skew_and_prunes() -> None:
    buffer = TimedRingBuffer(retention_s=1.0, maxlen=3)
    buffer.append(TimedSample("hal", 10.0, {"value": 10}))
    buffer.append(TimedSample("hal", 10.5, {"value": 11}))
    buffer.append(TimedSample("hal", 11.4, {"value": 12}))
    buffer.append(TimedSample("hal", 12.0, {"value": 13}))

    assert len(buffer) == 2
    assert buffer.nearest(11.95, 0.1).value == {"value": 13}
    assert buffer.nearest(11.0, 0.1) is None


def test_dataset_recorder_collect_frame_uses_timed_buffers() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "class TimedRingBuffer" in source
    assert "self._sampler_threads" in source
    assert "Thread(" in source
    assert "self._start_sampler_tasks_locked()" in source
    assert 'recorder._aligned_sample("hal", target_monotonic_s)' in source
    assert 'recorder._aligned_sample("force", target_monotonic_s)' in source
    assert "CAMERA_SOURCE_KEYS" in source


def test_dataset_recorder_source_sampler_uses_shared_source_epoch() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._sampler_start_monotonic_s = 100.0
    recorder._record_fps_hz = 30

    due = recorder._next_phase_aligned_sample_time("hal", {}, 100.005)

    assert due == pytest.approx(100.0 + (1.0 / 30.0), abs=0.001)


def test_dataset_recorder_alignment_time_uses_warmup_plus_dataset_timestamp() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._record_fps_hz = 30
    recorder._sampler_start_monotonic_s = 100.0
    recorder._episode_start_monotonic_s = 100.5

    assert recorder._record_target_timestamp_s(0) == pytest.approx(100.5)
    assert recorder._record_target_timestamp_s(15) == pytest.approx(101.0)


def test_dataset_recorder_sample_buffer_covers_warmup_delay_jitter_and_lookback() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._record_fps_hz = 30

    retention = recorder._sample_buffer_retention_s(
        {
            "storage": {
                "recordFps": 30,
                "maxConsumerLatencyS": 2.0,
                "maxSampleJitterS": 0.2,
                "sampleLookbackWindowS": 0.3,
            }
        }
    )

    assert retention == pytest.approx(3.0)


def test_dataset_recorder_source_sample_time_uses_source_frequency() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._record_fps_hz = 30
    recorder._force_sample_hz = 200.0

    assert recorder._source_sample_timestamp_s("force", 50, {"force": {"sampleHz": 200}}) == pytest.approx(0.25)
    assert recorder._source_sample_timestamp_s("camera_global", 10, {"cameras": {"fps": 25}}) == pytest.approx(0.4)


def test_dataset_recorder_timed_source_records_timeout_drop() -> None:
    async def slow_source() -> dict[str, object]:
        await asyncio.sleep(0.05)
        return {}

    recorder = object.__new__(DatasetRecorderService)
    recorder._drop_counts = {"hal": 0}
    recorder._source_skews_ms = {"hal": []}
    recorder._source_elapsed_ms = {"hal": []}
    recorder._source_fail_streaks = {"hal": 0}
    recorder._source_warnings = []

    sample = asyncio.run(recorder._timed_source("hal", slow_source(), 2.5))

    assert sample.ok is False
    assert sample.monotonic_s == pytest.approx(2.5)
    assert sample.message == "hal timeout"
    assert recorder._drop_counts["hal"] == 1
    assert recorder._source_fail_streaks["hal"] == 1


def test_dataset_recorder_source_timestamp_uses_assigned_sample_time() -> None:
    recorder = object.__new__(DatasetRecorderService)

    assert recorder._source_sample_monotonic({"received_monotonic_ms": 1, "monotonic_s": 2.0}, 42.0) == 2.0


def test_dataset_recorder_source_timestamp_uses_real_sample_time() -> None:
    recorder = object.__new__(DatasetRecorderService)

    assert recorder._source_sample_monotonic({"received_monotonic_ms": 100250}, 42.0) == pytest.approx(100.25)
    assert recorder._source_sample_monotonic(SimpleNamespace(sample_monotonic_s=100.5), 42.0) == pytest.approx(100.5)
    assert recorder._source_sample_monotonic({"monotonic_s": 2.0}, 42.0) == pytest.approx(2.0)


def test_dataset_recorder_camera_frame_returns_capture_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCameras:
        def snapshot_frame_with_timestamp(self, _config: dict[str, object], _camera: str) -> tuple[str, float]:
            return ("rgb-frame", 125.25)

    class FakeHardware:
        cameras = FakeCameras()

    recorder = object.__new__(DatasetRecorderService)
    recorder.hardware = FakeHardware()
    monkeypatch.setattr(recorder, "_coerce_rgb_frame", lambda frame, _config, _camera: frame)

    frame, sampled_at = recorder._camera_recording_frame_with_time({}, "global")

    assert frame == "rgb-frame"
    assert sampled_at == pytest.approx(125.25)


def test_dataset_recorder_gripper_source_uses_assigned_sample_time() -> None:
    class FakeTelemetry:
        def __init__(self) -> None:
            self.gripper_positions = [-1.0, -1.0]
            self.gripper_samples: dict[str, dict[str, object]] = {}
            self._last_gripper_sample_at = 0.0

        def refresh_gripper_positions(self, _config: dict[str, object], now: float) -> None:
            sample_at = now - 0.01
            sample_ms = int(sample_at * 1000)
            self.gripper_positions = [4.0, 5.0]
            self._last_gripper_sample_at = sample_at
            self.gripper_samples = {
                "left": {
                    "ok": True,
                    "positionMm": 4.0,
                    "readMs": 1.5,
                    "sampleHz": 30.0,
                    "tsMs": 1000,
                    "monotonicMs": sample_ms,
                    "message": "left ok",
                },
                "right": {
                    "ok": True,
                    "positionMm": 5.0,
                    "readMs": 1.7,
                    "sampleHz": 30.0,
                    "tsMs": 1001,
                    "monotonicMs": sample_ms,
                    "message": "right ok",
                },
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.telemetry = FakeTelemetry()
    recorder._drop_counts = {"gripper": 0}
    recorder._late_source_frames = {}
    recorder._source_skews_ms = {"gripper": []}
    recorder._source_elapsed_ms = {"gripper": []}
    recorder._source_fail_streaks = {"gripper": 0}
    recorder._source_warnings = []
    config = {"hal": {"mode": "real"}, "gripper": {"sampleHz": 30, "sampleStaleMs": 500}}

    sample = asyncio.run(recorder._gripper_source(config, 3.5))

    assert sample.ok is True
    assert sample.value == [4.0, 5.0]
    assert sample.monotonic_s > 3.5


def test_native_preflight_does_not_import_lerobot_record_script() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "lerobot.scripts.lerobot_record" not in source
    assert "lerobot.datasets.lerobot_dataset" in source


def test_dataset_recorder_falls_back_when_native_lerobot_unavailable(monkeypatch) -> None:
    recorder = object.__new__(DatasetRecorderService)
    monkeypatch.setattr(recorder, "_native_imports", lambda: None)

    assert recorder._native_preflight() == "lerobot[dataset] is not installed in backend runtime"


def test_dataset_recorder_samples_current_force_without_window(monkeypatch) -> None:
    class FakeForce:
        def __init__(self) -> None:
            self.sample_calls = 0
            self.window_calls = 0

        def sample(self, _config: dict[str, object]) -> object:
            self.sample_calls += 1
            return {"ok": True, "left": [1.0] * 6, "right": [2.0] * 6}

        def sample_window(self, _config: dict[str, object], _samples: int) -> object:
            self.window_calls += 1
            return {"ok": True, "left": [0.0] * 6, "right": [0.0] * 6}

    class FakeHardware:
        def __init__(self) -> None:
            self.force = FakeForce()

    recorder = object.__new__(DatasetRecorderService)
    recorder.hardware = FakeHardware()
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    result = asyncio.run(recorder._sample_force_source({"hal": {"mode": "real"}}, 1.0))

    assert result.value == {"ok": True, "left": [1.0] * 6, "right": [2.0] * 6}
    assert recorder.hardware.force.sample_calls == 1
    assert recorder.hardware.force.window_calls == 0


def test_dataset_recorder_composes_14d_state_and_absolute_action() -> None:
    class FakeTeleop:
        def status(self) -> dict[str, object]:
            return {
                "lastAction": {
                    "ts": int(time.time() * 1000),
                    "deltaVector": [10.0, 0.0, 0.0, 0.5, 0.0, 0.0, -20.0, 0.0, 0.0, 0.0, 0.0, -0.1],
                }
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()
    state = recorder._compose_observation_state(
        [1, 2, 3, 0.1, 0.2, 0.3, 7, 8, 9, 0.4, 0.5, 0.6],
        [4.5, 5.5],
    )

    assert state == [1, 2, 3, 100.0, 200.0, 300.0, 4.5, 7, 8, 9, 400.0, 500.0, 600.0, 5.5]
    assert recorder._latest_action_vector(
        state,
        {"gripper": {"targetLeftMm": 6.0, "targetRightMm": 7.0}},
    ) == [11, 2, 3, 600.0, 200.0, 300.0, 6.0, -13, 8, 9, 400.0, 500.0, 500.0, 7.0]


def test_dataset_recorder_applies_work_origin_pulse_conversion() -> None:
    recorder = object.__new__(DatasetRecorderService)
    origin = {
        "leftValid": True,
        "rightValid": False,
        "leftPulse": [100.0, -200.0, 300.0, 0.0, 0.0, 0.0],
        "rightPulse": [0.0] * 6,
    }
    pulses = [
        -8900.0,
        -10200.0,
        -9700.0,
        1666.666667,
        2500.0,
        3333.333333,
        10.0,
        20.0,
        30.0,
        40.0,
        50.0,
        60.0,
    ]

    relative = recorder._recording_motion_positions({"motion": {"origin": origin}}, [42.0] * 12, pulses)

    assert relative[:4] == [1000.0, 1000.0, 1000.0, 1.0]
    assert relative[6] == 42.0
