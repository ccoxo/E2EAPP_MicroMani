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
    DatasetSaveError,
    FrameAssembler,
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


def test_dataset_recorder_hal_fallback_reuses_last_valid_motion_pulses() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder.telemetry = SimpleNamespace(motion_positions=[100.0] * 12)
    recorder._last_motion_pulses = [float(value) for value in range(1, 13)]

    sample = recorder._fallback_sample("hal", 12.0, "hal missing")

    assert sample.value == {
        "positions": [100.0] * 12,
        "pulses": [float(value) for value in range(1, 13)],
    }


def test_frame_assembler_uses_cached_motion_pulses_when_hal_sample_lacks_pulses() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder.telemetry = SimpleNamespace(
        motion_positions=[0.0] * 12,
        force_left=[0.0] * 6,
        force_right=[0.0] * 6,
        gripper_positions=[1.0, 2.0],
    )
    recorder._last_motion_pulses = [float(value) for value in range(10, 22)]
    recorder._record_fps_hz = 30
    recorder._episode_index = 0
    recorder._force_values_from_sample = lambda _sample: None
    recorder._recording_motion_positions = lambda _config, positions, _pulses: list(positions)
    recorder._compose_observation_state = lambda positions, grippers: list(positions) + list(grippers)
    recorder._camera_placeholder_value = lambda _feature_key: None
    recorder._latest_action_vector = lambda observation_state, _config, _target: list(observation_state)

    def aligned_sample(source: str, target_s: float) -> TimedSample:
        if source == "hal":
            return TimedSample(source, target_s, {"positions": [5.0] * 12})
        if source == "gripper":
            return TimedSample(source, target_s, [1.0, 2.0])
        return TimedSample(source, target_s, None)

    recorder._aligned_sample = aligned_sample

    frame = FrameAssembler(recorder).assemble(default_config(), 1.0, 0)

    assert frame["observation.pulses"] == [float(value) for value in range(10, 22)]


def test_dataset_recorder_persists_episode_origin_and_config_snapshot() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert '"motionOrigin": self._episode_motion_origin_snapshot(config_snapshot)' in source
    assert '"motionCalibration": self._motion_calibration_snapshot(config_snapshot)' in source
    assert '"configHash": stable_config_hash(config_snapshot)' in source
    assert '"positionSource": motion.get("positionSource", "")' in source


def test_dataset_recorder_motion_calibration_snapshot_records_current_pulse_equivalents() -> None:
    recorder = object.__new__(DatasetRecorderService)
    config = default_config()

    snapshot = recorder._motion_calibration_snapshot(config)
    kinematics = snapshot["kinematics"]

    assert kinematics["axisOrder"] == ["x", "y", "z", "roll", "pitch", "yaw"]
    assert kinematics["axisUnitSpec"] == ["mm", "mm", "mm", "deg", "deg", "deg"]
    assert kinematics["leftPulsePerUnit"] == [5000.0, 5000.0, 10000.0, 1666.666667, 2500.0, 3333.333]
    assert kinematics["rightPulsePerUnit"] == [5000.0, 10000.0, 5000.0, 1666.666667, 2500.0, 333.3333]
    assert kinematics["leftSignedPulsePerUnit"] == [-5000.0, 5000.0, -10000.0, 1666.666667, -2500.0, -3333.333]
    assert kinematics["rightSignedPulsePerUnit"] == [-5000.0, -10000.0, -5000.0, 1666.666667, 2500.0, 333.3333]
    assert snapshot["teleop"]["leftImpulseCoeff"] == [-5000000.0, -5000000.0, -10000000.0, 1667.0, 2500.0, -333.3333]
    assert snapshot["teleop"]["rightImpulseCoeff"] == [-5000000.0, 10000000.0, -5000000.0, 1667.0, -2500.0, 3333.333]
    assert snapshot["stateUnitSpec"] == ["um", "um", "um", "mdeg", "mdeg", "mdeg"]
    assert isinstance(snapshot["configHash"], str)


def test_dataset_recorder_appstation_info_writes_motion_calibration(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._dataset_name = "unit"
    recorder._native_use_videos = True
    recorder._record_fps_hz = 30
    recorder._force_sample_hz = 200.0
    dataset_dir = tmp_path / "dataset"
    config = default_config()

    recorder._write_appstation_info(dataset_dir, config)

    payload = json.loads((dataset_dir / "meta" / "appstation_info.json").read_text(encoding="utf-8"))
    motion = payload["hardware"]["motion"]
    assert motion["kinematics"]["rightSignedPulsePerUnit"][5] == 333.3333
    assert motion["teleop"]["rightImpulseCoeff"][5] == 3333.333


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


def test_native_placeholder_episode_samples_keep_preview_rows() -> None:
    recorder = object.__new__(DatasetRecorderService)

    samples = recorder._native_placeholder_episode_samples(
        "unit_dataset",
        {"id": "episode_000000", "frames": 3},
        max_samples=300,
    )

    assert [sample["frame"] for sample in samples] == [0, 1, 2]
    assert samples[0]["leftJoints"] == [0.0] * 6
    assert samples[0]["images"]["global"].endswith("episode_id=episode_000000&camera=global&frame=0")


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


def test_dataset_recorder_configures_native_chunk_settings_for_independent_episode_files() -> None:
    recorder = object.__new__(DatasetRecorderService)
    calls: list[dict[str, float]] = []
    dataset = SimpleNamespace(
        meta=SimpleNamespace(update_chunk_settings=lambda **kwargs: calls.append(kwargs)),
    )

    recorder._configure_native_chunk_settings(dataset)

    assert calls == [
        {
            "data_files_size_in_mb": 0.000001,
            "video_files_size_in_mb": 0.000001,
        }
    ]


def test_dataset_recorder_skip_reset_requires_saved_episode_waiting() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._session_active = True
    recorder._recording = True
    recorder._reset_pending = False
    recorder._last_saved_episode = None
    recorder._lock = asyncio.Lock()
    recorder.telemetry = SimpleNamespace(recording=True)

    with pytest.raises(RuntimeError, match="record reset is not ready"):
        asyncio.run(recorder.skip_reset())


def test_dataset_recorder_skip_reset_requires_required_work_origin_side() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        calls: list[str] = []
        recorder._session_active = True
        recorder._recording = False
        recorder._reset_pending = True
        recorder._reset_required_sides = {"left"}
        recorder._reset_returned_sides = set()
        recorder._last_saved_episode = {"id": "episode_000000"}
        recorder._episode_index = 1
        recorder._lock = asyncio.Lock()
        recorder.telemetry = SimpleNamespace(recording=False, frame_count=12)
        recorder.logs = SimpleNamespace(info=lambda *_args: calls.append("log"))
        recorder.status = lambda: {"recording": recorder._recording, "resetReady": recorder._reset_ready_locked()}

        def begin_episode() -> None:
            calls.append("begin")
            recorder._recording = True

        async def start(source: str, *, pre_home: bool = True) -> None:
            calls.append(f"start:{source}:{pre_home}")

        async def warmup() -> None:
            calls.append("warmup")

        recorder._begin_episode_locked = begin_episode
        recorder.teleop = SimpleNamespace(start=start)
        recorder._wait_for_episode_warmup = warmup

        with pytest.raises(RuntimeError, match="record reset work origin is not ready"):
            await recorder.skip_reset()

        recorder.mark_reset_origin_returned("right")
        with pytest.raises(RuntimeError, match="record reset work origin is not ready"):
            await recorder.skip_reset()

        recorder.mark_reset_origin_returned("left")
        result = await recorder.skip_reset()

        assert result["recording"] is True
        assert recorder._reset_pending is False
        assert recorder._reset_returned_sides == set()
        assert recorder.telemetry.recording is True
        assert recorder.telemetry.frame_count == 0
        assert calls == ["begin", "start:recording:False", "warmup", "log"]

    asyncio.run(run_case())


def test_dataset_recorder_discard_pauses_until_reset() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        calls: list[str] = []
        recorder._session_active = True
        recorder._recording = True
        recorder._reset_pending = False
        recorder._samplers_paused = False
        recorder._last_saved_episode = None
        recorder._episode_index = 0
        recorder._lock = asyncio.Lock()
        recorder.telemetry = SimpleNamespace(recording=True, frame_count=8)
        recorder._native_writer_active = lambda: False
        recorder._begin_episode_locked = lambda: pytest.fail("discard should wait for reset before beginning")
        recorder.status = lambda: {"recording": recorder._recording}
        recorder.logs = SimpleNamespace(warning=lambda *_args: calls.append("log"))

        async def drain() -> None:
            calls.append("drain")

        async def start(_source: str) -> None:
            pytest.fail("discard should not restart teleop")

        async def stop(source: str) -> None:
            calls.append(f"stop:{source}")

        recorder._drain_recording_queues = drain
        recorder.teleop = SimpleNamespace(start=start, stop=stop)

        result = await recorder.discard_episode()

        assert result["recording"] is False
        assert recorder._recording is False
        assert recorder._reset_pending is True
        assert recorder._samplers_paused is True
        assert recorder.telemetry.recording is False
        assert calls == ["drain", "stop:recording", "log"]

    asyncio.run(run_case())


def test_dataset_recorder_save_drains_queued_assembly_before_closing_episode() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        calls: list[str] = []
        recorder._session_active = True
        recorder._recording = True
        recorder._episode_index = 2
        recorder._episode_generation = 3
        recorder._queued_episode_frames = 4
        recorder._reset_pending = False
        recorder._samplers_paused = False
        recorder._last_saved_episode = None
        recorder._lock = asyncio.Lock()
        recorder.telemetry = SimpleNamespace(recording=True, episode_count=2)
        recorder._native_writer_active = lambda: False
        recorder.status = lambda: {"recording": recorder._recording}
        recorder.logs = SimpleNamespace(info=lambda *_args: calls.append("log"))

        queued_job = FrameAssemblyJob(2, 3, 3, 10.0, 1 / 30)
        next_job = FrameAssemblyJob(2, 3, 4, 10.0 + 1 / 30, 1 / 30)

        async def drain() -> None:
            calls.append("drain")
            assert recorder._frame_job_belongs_to_current_episode_locked(queued_job) is True
            assert recorder._is_current_frame_job_locked(next_job) is False

        def finalize(*, status: str, deleted: bool) -> dict[str, object]:
            calls.append(f"finalize:{status}:{deleted}")
            return {"id": "episode_000002", "episodeIndex": 2}

        async def stop(source: str) -> None:
            calls.append(f"stop:{source}")

        recorder._drain_recording_queues = drain
        recorder._finalize_episode_locked = finalize
        recorder.teleop = SimpleNamespace(stop=stop)

        result = await recorder.save_episode()

        assert result["episode"]["id"] == "episode_000002"
        assert recorder._recording is False
        assert recorder._reset_pending is True
        assert recorder._samplers_paused is True
        assert recorder.telemetry.recording is False
        assert calls == ["drain", "finalize:review:False", "stop:recording", "log"]

    asyncio.run(run_case())


def test_dataset_recorder_save_failure_stops_recording_source() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        calls: list[str] = []
        recorder._session_active = True
        recorder._recording = True
        recorder._accepting_frame_jobs = True
        recorder._reset_pending = False
        recorder._samplers_paused = False
        recorder._lock = asyncio.Lock()
        recorder.telemetry = SimpleNamespace(recording=True, episode_count=0)
        recorder._native_writer_active = lambda: True

        async def drain() -> None:
            calls.append("drain")

        async def save_native() -> None:
            calls.append("save_native")
            raise DatasetSaveError("native LeRobot save_episode failed: invalid mp4")

        async def stop(source: str) -> None:
            calls.append(f"stop:{source}")

        async def clear_native() -> None:
            calls.append("clear_native")

        recorder._drain_recording_queues = drain
        recorder._save_native_episode = save_native
        recorder._clear_native_episode_buffer = clear_native
        recorder.teleop = SimpleNamespace(stop=stop)

        with pytest.raises(DatasetSaveError, match="invalid mp4"):
            await recorder.save_episode()

        assert recorder._recording is False
        assert recorder._accepting_frame_jobs is False
        assert recorder._reset_pending is True
        assert recorder._samplers_paused is True
        assert recorder.telemetry.recording is False
        assert calls == ["drain", "save_native", "clear_native", "stop:recording"]

    asyncio.run(run_case())


def test_save_native_episode_reports_dataset_save_error() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        recorder._native_error = None

        async def writer_command(kind: str) -> None:
            assert kind == "save_episode"
            raise RuntimeError("invalid video container")

        recorder._native_writer_command = writer_command

        with pytest.raises(DatasetSaveError, match="native LeRobot save_episode failed: invalid video container"):
            await recorder._save_native_episode()

        assert recorder._native_error == "invalid video container"

    asyncio.run(run_case())


def test_cleanup_native_tmp_dirs_removes_orphan_streaming_videos(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    dataset_dir = tmp_path / "dataset"
    orphan_dir = dataset_dir / "tmpabc123"
    keep_dir = dataset_dir / "tmp-not-lerobot"
    orphan_dir.mkdir(parents=True)
    keep_dir.mkdir(parents=True)
    (orphan_dir / "observation.images.global_streaming.mp4").write_bytes(b"partial")
    (keep_dir / "note.txt").write_text("keep", encoding="utf-8")

    recorder._cleanup_native_tmp_dirs(dataset_dir)

    assert not orphan_dir.exists()
    assert keep_dir.exists()


def test_dataset_recorder_skip_reset_starts_after_discarded_episode_waiting() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        calls: list[str] = []
        recorder._session_active = True
        recorder._recording = False
        recorder._reset_pending = True
        recorder._reset_required_sides = {"left"}
        recorder._reset_returned_sides = {"left"}
        recorder._last_saved_episode = None
        recorder._episode_index = 0
        recorder._lock = asyncio.Lock()
        recorder.telemetry = SimpleNamespace(recording=False, frame_count=8)
        recorder.logs = SimpleNamespace(info=lambda *_args: calls.append("log"))
        recorder.status = lambda: {"recording": recorder._recording}

        def begin_episode() -> None:
            calls.append("begin")
            recorder._recording = True

        async def start(source: str, *, pre_home: bool = True) -> None:
            calls.append(f"start:{source}:{pre_home}")

        async def warmup() -> None:
            calls.append("warmup")

        recorder._begin_episode_locked = begin_episode
        recorder.teleop = SimpleNamespace(start=start)
        recorder._wait_for_episode_warmup = warmup

        result = await recorder.skip_reset()

        assert result["recording"] is True
        assert recorder._reset_pending is False
        assert recorder.telemetry.recording is True
        assert recorder.telemetry.frame_count == 0
        assert calls == ["begin", "start:recording:False", "warmup", "log"]

    asyncio.run(run_case())


def test_dataset_recorder_skip_reset_transition_uses_single_lock() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")
    body = source.split("async def skip_reset", 1)[1].split("async def finish_session", 1)[0]
    before_teleop_start = body.split('await self.teleop.start("recording", pre_home=False)', 1)[0]

    assert before_teleop_start.count("async with self._lock:") == 1
    assert body.index("if not self._reset_ready_locked():") < body.index("self._begin_episode_locked()")
    assert body.index("self._begin_episode_locked()") < body.index(
        'await self.teleop.start("recording", pre_home=False)'
    )


def test_dataset_recorder_skip_reset_rolls_back_when_teleop_start_fails() -> None:
    async def run_case() -> None:
        recorder = object.__new__(DatasetRecorderService)
        calls: list[str] = []
        saved_episode = {"id": "episode_000000"}
        recorder._session_active = True
        recorder._recording = False
        recorder._reset_pending = True
        recorder._reset_required_sides = {"left"}
        recorder._reset_returned_sides = {"left"}
        recorder._last_saved_episode = saved_episode
        recorder._episode_index = 1
        recorder._lock = asyncio.Lock()
        recorder._samplers_paused = True
        recorder.telemetry = SimpleNamespace(recording=False, frame_count=12)
        recorder.logs = SimpleNamespace(
            info=lambda *_args: calls.append("log"),
            warning=lambda *_args: calls.append("warn"),
        )

        def begin_episode() -> None:
            calls.append("begin")
            recorder._recording = True
            recorder._samplers_paused = False

        async def start(source: str, *, pre_home: bool = True) -> None:
            calls.append(f"start:{source}:{pre_home}")
            raise RuntimeError("teleop start failed")

        async def stop(source: str) -> None:
            calls.append(f"stop:{source}")

        recorder._begin_episode_locked = begin_episode
        recorder.teleop = SimpleNamespace(start=start, stop=stop)
        recorder._wait_for_episode_warmup = lambda: pytest.fail("warmup should not run after teleop start failure")
        recorder.status = lambda: {
            "recording": recorder._recording,
            "resetPending": recorder._reset_pending,
            "resetReady": recorder._reset_ready_locked(),
        }

        with pytest.raises(RuntimeError, match="teleop start failed"):
            await recorder.skip_reset()

        assert recorder._recording is False
        assert recorder._reset_pending is True
        assert recorder._reset_required_sides == {"left"}
        assert recorder._reset_returned_sides == {"left"}
        assert recorder._last_saved_episode is saved_episode
        assert recorder._samplers_paused is True
        assert recorder.telemetry.recording is False
        assert recorder.telemetry.frame_count == 0
        assert calls == ["begin", "start:recording:False", "stop:recording"]

    asyncio.run(run_case())


def test_dataset_recorder_rolls_back_when_start_session_fails_after_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_case() -> None:
        config = default_config()
        config["hal"]["mode"] = "mock"
        config["storage"]["datasetRoot"] = str(tmp_path / "datasets")

        class FailingTeleop:
            def __init__(self) -> None:
                self.stop_calls: list[str] = []

            async def start(self, source: str, *, pre_home: bool = True) -> None:
                assert source == "recording"
                assert pre_home is False
                raise RuntimeError("teleop start failed")

            async def stop(self, source: str) -> None:
                self.stop_calls.append(source)

            def status(self) -> dict[str, object]:
                return {}

        teleop = FailingTeleop()
        recorder = DatasetRecorderService(
            SimpleNamespace(get_config=lambda: config),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(recording=False, episode_count=0, frame_count=0),
            SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None, error=lambda *_args: None),
            teleop,
        )
        monkeypatch.setattr(recorder, "_try_begin_native_dataset", lambda _config: asyncio.sleep(0, result=True))
        monkeypatch.setattr(recorder, "_write_appstation_info", lambda *_args: None)
        monkeypatch.setattr(recorder, "_next_episode_index", lambda _dataset_dir: 0)
        monkeypatch.setattr(recorder, "_native_writer_active", lambda: True)
        monkeypatch.setattr(recorder, "_start_sampler_tasks_locked", lambda: None)
        monkeypatch.setattr(recorder, "_record_loop", lambda: asyncio.sleep(0))
        monkeypatch.setattr(recorder, "_frame_assembler_loop", lambda: asyncio.sleep(0))

        try:
            with pytest.raises(RuntimeError, match="teleop start failed"):
                await recorder.start_session("unit", "task")

            assert recorder.status()["active"] is False
            assert recorder.status()["recording"] is False
            assert recorder.telemetry.recording is False
            assert teleop.stop_calls == ["recording"]
        finally:
            await recorder.finish_session()

    asyncio.run(run_case())


def test_dataset_recorder_starts_recording_teleop_without_pre_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_case() -> None:
        config = default_config()
        config["hal"]["mode"] = "mock"
        config["storage"]["datasetRoot"] = str(tmp_path / "datasets")

        class CapturingTeleop:
            def __init__(self) -> None:
                self.start_calls: list[tuple[str, bool]] = []
                self.stop_calls: list[str] = []

            async def start(
                self,
                source: str = "recording",
                home_side: str | None = None,
                *,
                pre_home: bool = True,
            ) -> dict[str, object]:
                assert home_side is None
                self.start_calls.append((source, pre_home))
                return {}

            async def stop(self, source: str) -> None:
                self.stop_calls.append(source)

            def status(self) -> dict[str, object]:
                return {}

        teleop = CapturingTeleop()
        recorder = DatasetRecorderService(
            SimpleNamespace(get_config=lambda: config),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(recording=False, episode_count=0, frame_count=0),
            SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None, error=lambda *_args: None),
            teleop,
        )
        monkeypatch.setattr(recorder, "_try_begin_native_dataset", lambda _config: asyncio.sleep(0, result=True))
        monkeypatch.setattr(recorder, "_write_appstation_info", lambda *_args: None)
        monkeypatch.setattr(recorder, "_next_episode_index", lambda _dataset_dir: 0)
        monkeypatch.setattr(recorder, "_native_writer_active", lambda: True)
        monkeypatch.setattr(recorder, "_start_sampler_tasks_locked", lambda: None)
        monkeypatch.setattr(recorder, "_record_loop", lambda: asyncio.sleep(0))
        monkeypatch.setattr(recorder, "_frame_assembler_loop", lambda: asyncio.sleep(0))
        monkeypatch.setattr(recorder, "_wait_for_episode_warmup", lambda: asyncio.sleep(0))

        try:
            await recorder.start_session("unit", "task")

            assert teleop.start_calls == [("recording", False)]
        finally:
            await recorder.finish_session()

    asyncio.run(run_case())


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


def test_dataset_recorder_quality_warnings_summarize_source_latency() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._episode_late_frames = 0
    recorder._tick_skews_ms = []
    recorder._source_warnings = [
        "force skew 10.868ms",
        "force skew 13.509ms",
        "camera_global skew 40.0ms",
        "camera_global skew 42.0ms",
        "force failed: USB transfer failed",
    ]
    recorder._late_source_frames = {"force": 2, "camera_global": 2}
    recorder._source_skews_ms = {
        "force": [5.0, 10.868, 13.509],
        "camera_global": [30.0, 40.0, 42.0],
    }
    recorder._stale_counts = {"force": 0, "camera_global": 0}
    recorder._cache_counts = {"force": 0, "camera_global": 0}
    recorder._camera_drops = {"global": 0, "wrist_left": 0, "wrist_right": 0}

    warnings = recorder._quality_warnings()

    assert "force latency summary: samples=3 warnings=2 avg=9.792ms max=13.509ms" in warnings
    assert "camera_global latency summary: samples=3 warnings=2 avg=37.333ms max=42.0ms" in warnings
    assert "force failed: USB transfer failed" in warnings
    assert all("force skew" not in warning for warning in warnings)
    assert all("camera_global skew" not in warning for warning in warnings)


def test_dataset_recorder_quality_warnings_include_camera_fps_and_worker_fallback() -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._episode_late_frames = 0
    recorder._tick_skews_ms = []
    recorder._source_warnings = []
    recorder._late_source_frames = {}
    recorder._source_skews_ms = {}
    recorder._stale_counts = {}
    recorder._cache_counts = {}
    recorder._camera_drops = {"global": 0, "wrist_left": 0, "wrist_right": 0}
    recorder._camera_min_fps = {"global": 29.8, "wrist_left": 24.1, "wrist_right": 19.7}
    recorder._camera_worker_fallbacks = {"wrist_right"}

    warnings = recorder._quality_warnings()

    assert "wrist_left camera low fps: 24.1Hz" in warnings
    assert "wrist_right camera low fps: 19.7Hz" in warnings
    assert "wrist_right camera worker fallback" in warnings


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


def test_dataset_recorder_uses_config_gripper_targets_when_workers_own_gripper() -> None:
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

    class FakeWorkers:
        def is_enabled(self, config: dict[str, object]) -> bool:
            gripper = config.get("gripper", {})
            return isinstance(gripper, dict) and gripper.get("sampleMode") == "dual_worker"

    class FakeTelemetry:
        def __init__(self) -> None:
            self.gripper_workers = FakeWorkers()

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()
    recorder.telemetry = FakeTelemetry()

    assert recorder._latest_action_vector(
        [0.0] * 14,
        {"gripper": {"sampleMode": "dual_worker", "targetLeftMm": 1.0, "targetRightMm": 2.0}},
    )[6::7] == [1.0, 2.0]


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


def test_dataset_recorder_prefers_worker_gripper_positions_when_workers_own_gripper() -> None:
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

    class FakeWorkers:
        def is_enabled(self, config: dict[str, object]) -> bool:
            gripper = config.get("gripper", {})
            return isinstance(gripper, dict) and gripper.get("sampleMode") == "dual_worker"

    class FakeTelemetry:
        def __init__(self) -> None:
            self.gripper_workers = FakeWorkers()
            self.gripper_positions = [-1.0, -1.0]
            self.gripper_samples: dict[str, dict[str, object]] = {}
            self._last_gripper_sample_at = 0.0

        def refresh_gripper_positions(self, _config: dict[str, object], now: float) -> None:
            self.gripper_positions = [6.0, 7.0]
            self._last_gripper_sample_at = now
            sample_ms = int(now * 1000)
            self.gripper_samples = {
                "left": {"positionMm": 6.0, "monotonicMs": sample_ms},
                "right": {"positionMm": 7.0, "monotonicMs": sample_ms},
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()
    recorder.telemetry = FakeTelemetry()
    recorder._drop_counts = {"gripper": 0}
    recorder._late_source_frames = {}
    recorder._source_skews_ms = {"gripper": []}
    recorder._source_elapsed_ms = {"gripper": []}
    recorder._source_fail_streaks = {"gripper": 0}
    recorder._source_warnings = []

    sample = recorder._gripper_source_sync(
        {"hal": {"mode": "real"}, "teleop": {"engine": "hal_native"}, "gripper": {"sampleMode": "dual_worker"}},
        10.0,
    )

    assert sample.ok is True
    assert sample.value == [6.0, 7.0]


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

    assert relative[:4] == [1800.0, -2000.0, 1000.0, 1.0]
    assert relative[6] == 42.0
