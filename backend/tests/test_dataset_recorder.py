from __future__ import annotations

import asyncio
import time
from pathlib import Path

from backend.services.dataset_recorder import DatasetRecorderService

EXPECTED_V3_FEATURES = {
    "observation.state": {
        "dtype": "float32",
        "shape": [14],
        "names": [
            "left_x_um",
            "left_y_um",
            "left_z_um",
            "left_roll_mdeg",
            "left_pitch_mdeg",
            "left_yaw_mdeg",
            "left_gripper_gap_mm",
            "right_x_um",
            "right_y_um",
            "right_z_um",
            "right_roll_mdeg",
            "right_pitch_mdeg",
            "right_yaw_mdeg",
            "right_gripper_gap_mm",
        ],
    },
    "action": {
        "dtype": "float32",
        "shape": [14],
        "names": [
            "left_dx_um",
            "left_dy_um",
            "left_dz_um",
            "left_droll_mdeg",
            "left_dpitch_mdeg",
            "left_dyaw_mdeg",
            "left_gripper_target_mm",
            "right_dx_um",
            "right_dy_um",
            "right_dz_um",
            "right_droll_mdeg",
            "right_dpitch_mdeg",
            "right_dyaw_mdeg",
            "right_gripper_target_mm",
        ],
    },
}


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
    }


def test_dataset_recorder_declares_and_writes_motion_pulses() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "PULSE_FEATURE_NAMES" in source
    assert '"observation.pulses": motion_pulses' in source
    assert '"observation.pulses": pulses' in source
    assert '"observation.pulses": self._np_float32(frame["observation.pulses"])' in source
    assert '"observation.pulses": {"dtype": "float32", "shape": [12]' in source
    assert '"observation.pulses": {"dtype": "float32", "shape": (12,)' in source


def test_dataset_recorder_declares_v3_state_and_action_shapes() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert '"observation.state": {"dtype": "float32", "shape": [14]' in source
    assert '"observation.state": {"dtype": "float32", "shape": (14,)' in source
    assert '"action": {"dtype": "float32", "shape": [14]' in source
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


def test_dataset_recorder_collect_frame_uses_parallel_sources() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "await asyncio.gather(" in source
    assert 'self._timed_source("hal", self.hal.motion_state()' in source
    assert 'self._timed_source("force", self._force_window_sample(config)' in source
    assert 'self._timed_source("camera", self._capture_cameras(config)' in source
    assert 'self._timed_source("omega", self.hal.omega_state()' in source


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

    sample = asyncio.run(recorder._timed_source("hal", slow_source(), time.monotonic()))

    assert sample.ok is False
    assert sample.message == "hal timeout"
    assert recorder._drop_counts["hal"] == 1
    assert recorder._source_fail_streaks["hal"] == 1


def test_dataset_recorder_gripper_source_uses_worker_sample_timestamp() -> None:
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

    sample = asyncio.run(recorder._gripper_source(config, time.monotonic()))
    metadata = recorder._source_frame_metadata(sample)["gripper"]

    assert sample.ok is True
    assert sample.value == [4.0, 5.0]
    assert sample.monotonic_s == recorder.telemetry._last_gripper_sample_at
    assert metadata["targetSampleHz"] == 30.0
    assert metadata["sides"]["left"]["sampleHz"] == 30.0
    assert metadata["sides"]["right"]["monotonicMs"] == metadata["sides"]["left"]["monotonicMs"]


def test_dataset_recorder_uses_cached_camera_frame_on_snapshot_failure(tmp_path: Path, monkeypatch) -> None:
    class FakeCameras:
        def __init__(self) -> None:
            self.calls = 0

        def snapshot(self, _config: dict[str, object], key: str) -> bytes:
            self.calls += 1
            if self.calls <= 3:
                return f"{key}-first".encode()
            raise RuntimeError(f"{key} offline")

    class FakeHardware:
        def __init__(self) -> None:
            self.cameras = FakeCameras()

    class FakeLogs:
        def warning(self, _channel: str, _message: str) -> None:
            return None

    recorder = object.__new__(DatasetRecorderService)
    recorder._native_dataset = None
    recorder._dataset_dir = tmp_path
    recorder._episode_index = 0
    recorder._episode_frames = 0
    recorder._current_episode_paths = []
    recorder._camera_drops = {"global": 0, "wrist_left": 0, "wrist_right": 0}
    recorder._drop_counts = {"global": 0, "wrist_left": 0, "wrist_right": 0}
    recorder._last_camera_frames = {}
    recorder._source_warnings = []
    recorder.hardware = FakeHardware()
    recorder.logs = FakeLogs()

    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    config = {"hal": {"mode": "real"}}
    first = asyncio.run(recorder._capture_cameras(config))
    recorder._episode_frames = 1
    second = asyncio.run(recorder._capture_cameras(config))

    assert set(first) == {
        "observation.images.global",
        "observation.images.wrist_left",
        "observation.images.wrist_right",
    }
    assert set(second) == set(first)
    cached_path = tmp_path / second["observation.images.global"]
    assert cached_path.read_bytes() == b"global-first"
    assert recorder._camera_drops["global"] == 1
    assert "global camera cache used" in recorder._source_warnings


def test_native_preflight_does_not_import_lerobot_record_script() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "lerobot.scripts.lerobot_record" not in source
    assert "lerobot.datasets.lerobot_dataset" in source


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
