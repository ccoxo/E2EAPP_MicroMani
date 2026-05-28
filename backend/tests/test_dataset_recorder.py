from __future__ import annotations

import asyncio
import json
import queue
import time
from concurrent.futures import Future
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from backend.core.defaults import default_config
from backend.services.dataset_recorder import (
    DatasetRecorderService,
    FrameAssemblyJob,
    LeRobotWriterThread,
    TimedRingBuffer,
    TimedSample,
    WriterCommand,
)


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


def test_dataset_recorder_persists_episode_origin_and_config_snapshot() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert '"motionOrigin": self._episode_motion_origin_snapshot(config_snapshot)' in source
    assert '"configHash": stable_config_hash(config_snapshot)' in source
    assert '"positionSource": motion.get("positionSource", "")' in source


def test_dataset_recorder_appstation_info_writes_session_origin(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._dataset_name = "unit"
    recorder._native_use_videos = True
    recorder._record_fps_hz = 30
    recorder._force_sample_hz = 200.0
    dataset_dir = tmp_path / "dataset"
    config = default_config()
    config["motion"]["origin"]["leftPulse"] = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    config["motion"]["origin"]["rightPulse"] = [70.0, 80.0, 90.0, 100.0, 110.0, 120.0]

    recorder._write_appstation_info(dataset_dir, config)

    payload = json.loads((dataset_dir / "meta" / "appstation_info.json").read_text(encoding="utf-8"))
    assert payload["sessionOrigin"]["leftPulse"] == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert payload["sessionOrigin"]["rightPulse"] == [70.0, 80.0, 90.0, 100.0, 110.0, 120.0]


def test_dataset_recorder_episode_origin_uses_recording_config_snapshot(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text('{"format":"lerobot-v3-native"}', encoding="utf-8")
    session_config = default_config()
    session_config["motion"]["origin"]["leftPulse"] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    session_config["motion"]["origin"]["rightPulse"] = [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    current_config = default_config()
    current_config["motion"]["origin"]["leftPulse"] = [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    current_config["motion"]["origin"]["rightPulse"] = [107.0, 108.0, 109.0, 110.0, 111.0, 112.0]
    recorder.settings = SimpleNamespace(get_config=lambda: current_config)
    recorder._recording_config_snapshot = session_config
    recorder._dataset_dir = dataset_dir
    recorder._dataset_name = "unit"
    recorder._task = "task"
    recorder._episode_index = 0
    recorder._episode_frames = 1
    recorder._episode_started_at = time.monotonic()
    recorder._native_dataset_from_index = 0
    recorder._record_fps_hz = 30
    recorder._force_sample_hz = 200.0
    recorder._native_use_videos = False
    recorder._episode_late_frames = 0
    recorder._camera_drops = {"global": 0, "wrist_left": 0, "wrist_right": 0}
    recorder._drop_counts = {}
    recorder._stale_counts = {}
    recorder._cache_counts = {}
    recorder._source_warnings = []
    recorder._tick_skews_ms = []
    recorder._source_skews_ms = {}
    recorder._max_force_left = 0.0
    recorder._max_force_right = 0.0
    recorder._native_writer_active = lambda: True

    episode = recorder._finalize_episode_locked(status="review", deleted=False)

    assert episode["motionOrigin"]["leftPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert episode["motionOrigin"]["rightPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    app_info = json.loads((dataset_dir / "meta" / "appstation_info.json").read_text(encoding="utf-8"))
    assert app_info["sessionOrigin"]["leftPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


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
    assert features["action"]["names"] == features["observation.state"]["names"]
    assert features["observation.images.global"]["dtype"] == "video"
    assert features["observation.images.global"]["shape"] == (480, 640, 3)
    assert "observation.gripper" not in features


def test_dataset_recorder_requires_streaming_video_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = object.__new__(DatasetRecorderService)
    monkeypatch.setenv("APPSTATION_LEROBOT_USE_VIDEOS", "0")
    monkeypatch.setenv("APPSTATION_LEROBOT_VCODEC", "h264")
    monkeypatch.setenv("APPSTATION_LEROBOT_ENCODER_QUEUE_MAXSIZE", "90")
    monkeypatch.setenv("APPSTATION_LEROBOT_ENCODER_THREADS", "2")

    assert recorder._native_use_videos_requested() is True
    assert recorder._native_writer_kwargs() == {
        "batch_encoding_size": 1,
        "vcodec": "h264",
        "streaming_encoding": True,
        "encoder_queue_maxsize": 90,
        "encoder_threads": 2,
    }


def test_dataset_recorder_saves_episode_with_parallel_encoding() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "save_episode(parallel_encoding=True)" in source


def test_lerobot_writer_keeps_dataset_open_after_saved_episode() -> None:
    class FakeDataset:
        def __init__(self) -> None:
            self.meta = SimpleNamespace(total_frames=3)
            self.save_calls = 0
            self.finalize_calls = 0

        def save_episode(self, *, parallel_encoding: bool) -> None:
            assert parallel_encoding is True
            self.save_calls += 1

        def finalize(self) -> None:
            self.finalize_calls += 1

    fake_dataset = FakeDataset()
    recorder = SimpleNamespace(
        _episode_index=0,
        _resume_native_dataset_locked=lambda: pytest.fail("save_episode should not resume the dataset"),
        logs=SimpleNamespace(error=lambda *_args: None),
    )
    writer = LeRobotWriterThread(recorder, queue.SimpleQueue())  # type: ignore[arg-type]
    writer._dataset = fake_dataset
    future: Future[object] = Future()

    writer._handle_command(WriterCommand("save_episode", future))

    assert future.result() == 3
    assert fake_dataset.save_calls == 1
    assert fake_dataset.finalize_calls == 0
    assert writer._dataset is fake_dataset


def test_dataset_recorder_configures_native_chunk_settings_for_single_session_files() -> None:
    recorder = object.__new__(DatasetRecorderService)
    calls: list[dict[str, int]] = []
    dataset = SimpleNamespace(
        meta=SimpleNamespace(update_chunk_settings=lambda **kwargs: calls.append(kwargs)),
    )

    recorder._configure_native_chunk_settings(dataset)

    assert calls == [
        {
            "data_files_size_in_mb": 10240,
            "video_files_size_in_mb": 20480,
        }
    ]


def test_dataset_recorder_skip_reset_requires_saved_episode_waiting() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._session_active = True
    recorder._recording = True
    recorder._last_saved_episode = None
    recorder._lock = asyncio.Lock()
    recorder.telemetry = SimpleNamespace(recording=True)

    with pytest.raises(RuntimeError, match="record reset is not ready"):
        asyncio.run(recorder.skip_reset())


def test_timed_ring_buffer_nearest_respects_skew_and_prunes() -> None:
    buffer = TimedRingBuffer(retention_s=1.0, maxlen=3)
    buffer.append(TimedSample("hal", 10.0, {"value": 10}))
    buffer.append(TimedSample("hal", 10.5, {"value": 11}))
    buffer.append(TimedSample("hal", 11.4, {"value": 12}))
    buffer.append(TimedSample("hal", 12.0, {"value": 13}))

    assert len(buffer) == 2
    assert buffer.nearest(11.95, 0.1).value == {"value": 13}
    assert buffer.nearest(11.0, 0.1) is None


def test_timed_ring_buffer_reports_settled_sample() -> None:
    buffer = TimedRingBuffer(retention_s=1.0, maxlen=4)
    buffer.append(TimedSample("hal", 10.0, {"value": 10}))
    buffer.append(TimedSample("hal", 10.5, {"value": 11}))

    assert buffer.has_at_or_after(10.25) is True
    assert buffer.has_at_or_after(10.75) is False


def test_dataset_recorder_collect_frame_uses_timed_buffers() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "class TimedRingBuffer" in source
    assert "self._sampler_threads" in source
    assert "Thread(" in source
    assert "self._start_sampler_tasks_locked()" in source
    assert 'recorder._aligned_sample("hal", target_monotonic_s)' in source
    assert 'recorder._aligned_sample("force", target_monotonic_s)' in source
    assert "CAMERA_SOURCE_KEYS" in source


def test_dataset_sampler_pauses_hardware_sampling_between_episodes() -> None:
    recorder = object.__new__(DatasetRecorderService)
    sampled = Event()
    recorder._session_active = True
    recorder._recording = False
    recorder._samplers_paused = True
    recorder._sampler_stop_event = Event()
    recorder._sampler_start_monotonic_s = time.monotonic() - 0.1
    recorder._recording_config_snapshot = {"force": {"sampleHz": 100}}
    recorder._source_sample_indices = {"force": 0}
    recorder._sample_buffers = {"force": TimedRingBuffer()}
    recorder.logs = SimpleNamespace(warning=lambda *_args: None)
    recorder._source_sample_rate_hz = lambda _source, _config: 100.0

    def sample_once(source: str, _config: dict[str, object], target_s: float) -> TimedSample:
        sampled.set()
        return TimedSample(source, target_s, {"ok": True})

    recorder._sample_source_once_sync = sample_once

    thread = Thread(target=recorder._sample_source_loop, args=("force",), daemon=True)
    thread.start()
    try:
        assert sampled.wait(0.05) is False
        recorder._samplers_paused = False
        assert sampled.wait(0.3) is True
    finally:
        recorder._sampler_stop_event.set()
        recorder._session_active = False
        thread.join(1.0)


def test_dataset_recorder_record_loop_delegates_frame_assembly() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")
    record_loop_start = source.index("    async def _record_loop")
    assembler_loop_start = source.index("    async def _frame_assembler_loop", record_loop_start)
    collect_frame_start = source.index("    async def _collect_frame", assembler_loop_start)
    record_loop_source = source[record_loop_start:assembler_loop_start]
    assembler_loop_source = source[assembler_loop_start:collect_frame_start]

    assert "FrameAssemblyJob(" in record_loop_source
    assert "_enqueue_assembly_job(job)" in record_loop_source
    assert "_collect_frame(" not in record_loop_source
    assert "_collect_frame(" in assembler_loop_source
    assert "_enqueue_pending_frame(pending, job)" in assembler_loop_source


def test_dataset_recorder_enqueue_assembly_job_guards_episode_generation() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        recorder._lock = asyncio.Lock()
        recorder._recording = True
        recorder._episode_index = 2
        recorder._episode_generation = 3
        recorder._queued_episode_frames = 0
        recorder._last_telemetry_frame_update_s = 0.0
        recorder._assembly_queue = asyncio.Queue(maxsize=2)
        recorder.telemetry = SimpleNamespace(frame_count=0, recording=True)
        recorder._episode_late_frames = 0
        recorder._source_warnings = []
        recorder._native_error = ""

        stale = FrameAssemblyJob(2, 2, 0, 10.0, 1 / 30)
        current = FrameAssemblyJob(2, 3, 0, 10.0, 1 / 30)

        assert await recorder._enqueue_assembly_job(stale) is False
        assert recorder._assembly_queue.qsize() == 0
        assert await recorder._enqueue_assembly_job(current) is True
        assert recorder._queued_episode_frames == 1
        assert recorder._assembly_queue.get_nowait() == current

    asyncio.run(run_case())


def test_dataset_recorder_alignment_time_uses_warmup_plus_dataset_timestamp() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._record_fps_hz = 30
    recorder._sampler_start_monotonic_s = 100.0
    recorder._episode_start_monotonic_s = 100.5

    assert recorder._record_target_timestamp_s(0) == pytest.approx(100.5)
    assert recorder._record_target_timestamp_s(15) == pytest.approx(101.0)


def test_dataset_recorder_frame_assembly_uses_default_alignment_delay() -> None:
    recorder = object.__new__(DatasetRecorderService)

    assert recorder._alignment_delay_s({"storage": {}}) == pytest.approx(0.1)
    assert recorder._frame_assembly_due_s(10.0, {"storage": {}}) == pytest.approx(10.1)


def test_dataset_recorder_waits_for_critical_source_settle_until_timeout() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._sample_buffers = {"hal": TimedRingBuffer()}
    recorder._source_warnings = []

    missing = asyncio.run(
        recorder._wait_for_critical_sources(
            {"storage": {"settleTimeoutMs": 0}},
            10.0,
            sources=("hal",),
        )
    )

    assert missing == {"hal"}
    assert recorder._source_warnings == ["hal settle timeout"]


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
    assert recorder._source_sample_timestamp_s("hal", 100, {"motion": {"motionThreadHz": 1000}}) == pytest.approx(0.1)
    assert recorder._source_sample_timestamp_s(
        "omega",
        10,
        {"teleop": {"omegaSampleHz": 100}},
    ) == pytest.approx(0.1)


def test_dataset_recorder_uses_50ms_hal_timeout() -> None:
    recorder = object.__new__(DatasetRecorderService)

    assert recorder._source_timeout_s("hal") == pytest.approx(0.050)


def test_dataset_recorder_timed_source_records_timeout_drop() -> None:
    async def slow_source() -> dict[str, object]:
        await asyncio.sleep(0.08)
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
    monkeypatch.setattr(recorder, "_coerce_rgb_frame", lambda frame, _camera: frame)

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

    sample = recorder._gripper_source_sync(config, 3.5)

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

    result = recorder._sample_force_source_sync({"hal": {"mode": "real"}}, 1.0)

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


def test_dataset_recorder_uses_native_gripper_targets_for_action() -> None:
    class FakeTeleop:
        def status(self) -> dict[str, object]:
            return {
                "lastAction": {
                    "ts": int(time.time() * 1000),
                    "deltaVector": [0.0] * 12,
                },
                "nativeStatus": {
                    "gripperTargets": [8.0, 9.0],
                },
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()

    assert recorder._latest_action_vector(
        [0.0] * 14,
        {"gripper": {"targetLeftMm": 1.0, "targetRightMm": 2.0}},
    )[6::7] == [8.0, 9.0]


def test_dataset_recorder_uses_native_gripper_positions_for_observation() -> None:
    class FakeTeleop:
        def status(self) -> dict[str, object]:
            return {
                "nativeStatus": {
                    "grippers": {
                        "left": {"positionMm": 3.25, "targetMm": 8.0},
                        "right": {"positionMm": 4.5, "targetMm": 9.0},
                    },
                },
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()
    recorder._drop_counts = {"gripper": 0}
    recorder._late_source_frames = {}
    recorder._source_skews_ms = {"gripper": []}
    recorder._source_elapsed_ms = {"gripper": []}
    recorder._source_fail_streaks = {"gripper": 0}
    recorder._source_warnings = []

    sample = recorder._gripper_source_sync(
        {"hal": {"mode": "real"}, "teleop": {"engine": "hal_native"}},
        10.0,
    )

    assert sample.ok is True
    assert sample.value == [3.25, 4.5]


def test_dataset_recorder_action_vector_uses_last_action_before_target() -> None:
    class FakeTeleop:
        def status(self) -> dict[str, object]:
            return {
                "lastAction": {
                    "ts": int(time.time() * 1000),
                    "monotonic_s": 10.2,
                    "deltaVector": [99.0] * 12,
                },
                "actionHistory": [
                    {
                        "ts": int(time.time() * 1000),
                        "monotonic_s": 9.9,
                        "deltaVector": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    },
                    {
                        "ts": int(time.time() * 1000),
                        "monotonic_s": 10.2,
                        "deltaVector": [99.0] * 12,
                    },
                ],
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()

    assert recorder._latest_action_vector([0.0] * 14, {}, 10.0) == [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]


def test_dataset_recorder_action_vector_combines_latest_action_per_side() -> None:
    class FakeTeleop:
        def status(self) -> dict[str, object]:
            return {
                "lastAction": {
                    "ts": int(time.time() * 1000),
                    "monotonic_s": 10.2,
                    "side": "left",
                    "deltaVector": [99.0] * 12,
                },
                "actionHistory": [
                    {
                        "ts": int(time.time() * 1000),
                        "monotonic_s": 9.90,
                        "side": "right",
                        "deltaVector": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.25, 0.0, 0.0],
                    },
                    {
                        "ts": int(time.time() * 1000),
                        "monotonic_s": 9.95,
                        "side": "left",
                        "deltaVector": [5.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    },
                ],
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()

    assert recorder._latest_action_vector([0.0] * 14, {}, 10.0) == [
        5.0,
        0.0,
        0.0,
        500.0,
        0.0,
        0.0,
        0.0,
        10.0,
        0.0,
        0.0,
        250.0,
        0.0,
        0.0,
        0.0,
    ]


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

    assert relative[:4] == [1800.0, -1000.0, 1000.0, 1.0]
    assert relative[6] == 42.0
