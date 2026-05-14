from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
import queue
import re
import shutil
import time
from bisect import bisect_left
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.core.units import lerobot_to_ui_state, pulses_to_ui_state
from backend.hal_client.client import HalClient
from backend.services.hardware_service import HardwareService
from backend.services.telemetry_hub import TelemetryHub
from backend.services.teleop_mapping import TeleopMappingService

SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
CAMERA_KEYS: tuple[str, str, str] = ("global", "wrist_left", "wrist_right")
# 这些 feature 名称需要和 LeRobot 数据集字段保持稳定，前后端按它们读取图像。
CAMERA_FEATURE_KEYS: dict[str, str] = {
    "global": "observation.images.global",
    "wrist_left": "observation.images.wrist_left",
    "wrist_right": "observation.images.wrist_right",
}
CAMERA_CAPTURE_SIZES: dict[str, tuple[int, int]] = {
    "global": (640, 480),
    "wrist_left": (640, 480),
    "wrist_right": (640, 480),
}
STATE_FEATURE_NAMES: tuple[str, ...] = (
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
)
PULSE_FEATURE_NAMES: tuple[str, ...] = (
    "left_x_pulse",
    "left_y_pulse",
    "left_z_pulse",
    "left_roll_pulse",
    "left_pitch_pulse",
    "left_yaw_pulse",
    "right_x_pulse",
    "right_y_pulse",
    "right_z_pulse",
    "right_roll_pulse",
    "right_pitch_pulse",
    "right_yaw_pulse",
)
ACTION_FEATURE_NAMES: tuple[str, ...] = (
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
)
FORCE_FEATURE_NAMES: tuple[str, ...] = ("fx", "fy", "fz", "mx", "my", "mz")
GRIPPER_FEATURE_NAMES: tuple[str, str] = ("left_gap_mm", "right_gap_mm")
CAMERA_SOURCE_KEYS: dict[str, str] = {key: f"camera_{key}" for key in CAMERA_KEYS}
CAMERA_KEY_BY_SOURCE: dict[str, str] = {source: key for key, source in CAMERA_SOURCE_KEYS.items()}
SOURCE_KEYS: tuple[str, ...] = (
    "hal",
    "force",
    "gripper",
    *CAMERA_SOURCE_KEYS.values(),
    "omega",
)
SOURCE_TIMEOUT_S: dict[str, float] = {
    "hal": 0.020,
    "camera": 0.0333,
    "force": 0.020,
    "omega": 0.020,
    **{source: 0.035 for source in CAMERA_SOURCE_KEYS.values()},
}
# timeout 用 drop 边界，warning 用更严格的 skew 边界；这样慢但成功的源也会进入质量报告。
SOURCE_MAX_SKEW_S: dict[str, float] = {
    "hal": 0.020,
    "force": 0.010,
    "gripper": 0.050,
    "omega": 0.020,
    **{source: 0.035 for source in CAMERA_SOURCE_KEYS.values()},
}
SOURCE_WARNING_SKEW_MS: dict[str, float] = {
    source: max_skew_s * 1000.0 for source, max_skew_s in SOURCE_MAX_SKEW_S.items()
}
SOURCE_DROP_SKEW_MS: dict[str, float] = {source: 35.0 for source in CAMERA_SOURCE_KEYS.values()}
RING_BUFFER_RETENTION_S = 3.0
WRITE_QUEUE_MAX_FRAMES = 120
WRITE_QUEUE_PUT_TIMEOUT_S = 1.0
SAMPLER_START_LEAD_S = 0.05
RECORDER_HARDWARE_WARMUP_S = 0.5
MAX_SAMPLE_JITTER_S = 0.10
SAMPLE_LOOKBACK_WINDOW_S = 0.10


class DatasetSaveError(RuntimeError):
    """Raised when an episode cannot be persisted in the declared dataset format."""


@dataclass(frozen=True)
class TimedSample:
    # 记录每个硬件源相对录制 tick 的时序，用于质量统计而不写入训练帧。
    source: str
    monotonic_s: float
    value: Any
    ok: bool = True
    message: str = ""
    target_monotonic_s: float = 0.0
    started_monotonic_s: float = 0.0
    finished_monotonic_s: float = 0.0
    elapsed_ms: float = 0.0
    timed_out: bool = False
    stale: bool = False
    cache_used: bool = False


SourceSample = TimedSample


@dataclass(frozen=True)
class PendingFrame:
    # 写盘队列只传不可变帧快照，避免 episode 切换后旧帧写入新文件。
    episode_index: int
    frame_index: int
    timestamp: float
    frame: dict[str, Any]


# 存放采样数据的缓存区，按 source 分类，支持按时间查找最近样本。
@dataclass(frozen=True)
class WriterCommand:
    kind: str
    future: Future[Any]


class LeRobotWriterThread:
    """Serialize LeRobotDataset access on a dedicated thread."""

    def __init__(self, recorder: Any, work_queue: queue.Queue[PendingFrame | WriterCommand | None]) -> None:
        self._recorder = recorder
        self._queue = work_queue
        self._thread = Thread(target=self._run, name="lerobot-writer", daemon=True)
        self._dataset: Any | None = None
        self._error = ""

    def start(self) -> None:
        """Start the background writer thread."""
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the background writer thread to stop."""
        self._thread.join(timeout=timeout)

    def submit(self, kind: str) -> Future[Any]:
        """Queue a control command and return a Future for its result."""
        future: Future[Any] = Future()
        self._queue.put(WriterCommand(kind, future))
        return future

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if isinstance(item, WriterCommand):
                    self._handle_command(item)
                    continue
                self._write_frame(item.frame)
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)
                self._recorder.logs.error("[LEROBOT]", f"writer recovered: {exc}")
            finally:
                self._queue.task_done()

    def _handle_command(self, command: WriterCommand) -> None:
        try:
            if command.kind == "open":
                self._dataset = self._recorder._open_native_dataset_for_writer()
                self._error = ""
                command.future.set_result(self._native_total_frames())
            elif command.kind == "save_episode":
                if self._error:
                    raise RuntimeError(self._error)
                if self._dataset is not None:
                    self._dataset.save_episode(parallel_encoding=False)
                    self._dataset.finalize()
                    self._dataset = self._recorder._resume_native_dataset_locked()
                command.future.set_result(self._native_total_frames())
            elif command.kind == "clear_episode":
                if self._dataset is not None:
                    self._dataset.clear_episode_buffer()
                self._error = ""
                command.future.set_result(None)
            elif command.kind == "finalize":
                dataset = self._dataset
                self._dataset = None
                if dataset is not None:
                    dataset.finalize()
                command.future.set_result(None)
            else:
                raise RuntimeError(f"unknown writer command: {command.kind}")
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            command.future.set_exception(exc)

    def _write_frame(self, frame: dict[str, Any]) -> None:
        if int(frame.get("episode_index", self._recorder._episode_index)) != self._recorder._episode_index:
            return
        if self._dataset is None:
            raise RuntimeError("native LeRobot writer is not open")
        native_frame = self._recorder._native_frame_payload(frame)
        self._dataset.add_frame(native_frame)
        self._recorder._mark_frame_written(frame)

    def _native_total_frames(self) -> int:
        if self._dataset is None:
            return 0
        meta = getattr(self._dataset, "meta", None)
        total = getattr(meta, "total_frames", None)
        if total is None:
            total = getattr(self._dataset, "num_frames", 0)
        if total is None:
            return 0
        try:
            return int(total)
        except (TypeError, ValueError):
            return 0


class RecordingQualityTracker:
    """Keep per-episode timing counters out of the hot loop body."""

    def __init__(self, recorder: Any) -> None:
        self._recorder = recorder

    def record_tick_locked(
        self,
        target_tick: float,
        scheduled_tick: float,
        capture_tick: float,
        period_s: float,
    ) -> None:
        """Record tick skew and late-frame count while the recorder lock is held."""
        if capture_tick > scheduled_tick + period_s:
            self._recorder._episode_late_frames += 1
        self._recorder._tick_target_monotonic_s = target_tick
        self._recorder._tick_capture_monotonic_s = target_tick + (capture_tick - scheduled_tick)
        self._recorder._tick_skews_ms.append((capture_tick - scheduled_tick) * 1000.0)


class FrameAssembler:
    """Assemble one training frame from sampler snapshots."""

    def __init__(self, recorder: Any) -> None:
        self._recorder = recorder

    def assemble(self, config: dict[str, Any], target_monotonic_s: float, frame_index: int) -> dict[str, Any]:
        """Build a frame from already-sampled source buffers."""
        recorder = self._recorder
        motion_positions = list(recorder.telemetry.motion_positions)
        motion_pulses = [0.0] * 12
        force_left = list(recorder.telemetry.force_left)
        force_right = list(recorder.telemetry.force_right)
        hal_sample = recorder._aligned_sample("hal", target_monotonic_s)
        force_sample = recorder._aligned_sample("force", target_monotonic_s)
        gripper_sample = recorder._aligned_sample("gripper", target_monotonic_s)
        recorder._aligned_sample("omega", target_monotonic_s)
        motion_state = hal_sample.value if hal_sample.ok and isinstance(hal_sample.value, dict) else {}
        raw_positions = motion_state.get("positions") if isinstance(motion_state, dict) else None
        if isinstance(raw_positions, list) and len(raw_positions) == 12:
            motion_positions = [float(value) for value in raw_positions]
        raw_pulses = motion_state.get("pulses") if isinstance(motion_state, dict) else None
        if isinstance(raw_pulses, list) and len(raw_pulses) == 12:
            motion_pulses = [float(value) for value in raw_pulses]
        motion_positions = recorder._recording_motion_positions(config, motion_positions, motion_pulses)
        force_values = recorder._force_values_from_sample(force_sample.value)
        if force_values is not None:
            force_left, force_right = force_values
        gripper_positions = (
            list(gripper_sample.value)
            if isinstance(gripper_sample.value, list)
            else list(recorder.telemetry.gripper_positions)
        )
        observation_state = recorder._compose_observation_state(motion_positions, gripper_positions)
        image_payload: dict[str, Any] = {}
        for camera, source in CAMERA_SOURCE_KEYS.items():
            feature_key = CAMERA_FEATURE_KEYS[camera]
            camera_sample = recorder._aligned_sample(source, target_monotonic_s)
            image_payload[feature_key] = (
                camera_sample.value
                if camera_sample.value is not None
                else recorder._camera_placeholder_value(feature_key)
            )
        return {
            "timestamp": frame_index / max(1, recorder._record_fps_hz),
            "frame_index": frame_index,
            "episode_index": recorder._episode_index,
            "observation.state": observation_state,
            "observation.pulses": motion_pulses,
            "observation.force_left": force_left,
            "observation.force_right": force_right,
            "action": recorder._latest_action_vector(observation_state, config),
            "images": image_payload,
        }


class TimedRingBuffer:
    def __init__(self, *, retention_s: float = RING_BUFFER_RETENTION_S, maxlen: int = 300) -> None:
        """处理录制服务逻辑：__init__。"""
        self.retention_s = max(float(retention_s), 0.1)
        self.maxlen = max(int(maxlen), 1)
        self._samples: deque[TimedSample] = deque()
        self._lock = Lock()

    def append(self, sample: TimedSample) -> None:
        """处理录制服务逻辑：append。"""
        with self._lock:
            if not self._samples or sample.monotonic_s >= self._samples[-1].monotonic_s:
                self._samples.append(sample)
            else:
                samples = list(self._samples)
                index = bisect_left([item.monotonic_s for item in samples], sample.monotonic_s)
                samples.insert(index, sample)
                self._samples = deque(samples)
            latest_s = max(item.monotonic_s for item in self._samples)
            self._prune_locked(latest_s - self.retention_s)
            while len(self._samples) > self.maxlen:
                self._samples.popleft()

    def nearest(self, target_s: float, max_skew_s: float) -> TimedSample | None:
        """处理录制服务逻辑：nearest。"""
        with self._lock:
            if not self._samples:
                return None
            samples = list(self._samples)
        times = [sample.monotonic_s for sample in samples]
        index = bisect_left(times, target_s)
        candidates = []
        if index < len(samples):
            candidates.append(samples[index])
        if index > 0:
            candidates.append(samples[index - 1])
        if not candidates:
            return None
        nearest = min(candidates, key=lambda sample: abs(sample.monotonic_s - target_s))
        return nearest if abs(nearest.monotonic_s - target_s) <= max_skew_s else None

    def prune(self, before_s: float) -> None:
        """处理录制服务逻辑：prune。"""
        with self._lock:
            self._prune_locked(before_s)

    def _prune_locked(self, before_s: float) -> None:
        while self._samples and self._samples[0].monotonic_s < before_s:
            self._samples.popleft()

    def clear(self) -> None:
        """处理录制服务逻辑：clear。"""
        with self._lock:
            self._samples.clear()

    def __len__(self) -> int:
        """处理录制服务逻辑：__len__。"""
        with self._lock:
            return len(self._samples)


class DatasetRecorderService:
    """Recorder using the app hardware services with a native LeRobot path.

    Frames are written through `LeRobotDataset.create/resume/add_frame/save_episode`.
    AppStation owns hardware collection, time alignment, quality stats and UI
    metadata. LeRobot owns dataset layout, frame persistence and standard metadata.
    """

    def __init__(
        self,
        settings: SettingsService,
        hardware: HardwareService,
        hal: HalClient,
        telemetry: TelemetryHub,
        logs: LogService,
        teleop: TeleopMappingService,
    ) -> None:
        """处理录制服务逻辑：__init__。"""
        self.settings = settings
        self.hardware = hardware
        self.hal = hal
        self.telemetry = telemetry
        self.logs = logs
        self.teleop = teleop
        self._lock = asyncio.Lock()
        self._loop_task: asyncio.Task[None] | None = None
        self._writer_thread: LeRobotWriterThread | None = None
        self._sampler_threads: dict[str, Thread] = {}
        self._sampler_stop_event = Event()
        self._write_queue: queue.Queue[PendingFrame | WriterCommand | None] = queue.Queue(
            maxsize=WRITE_QUEUE_MAX_FRAMES
        )
        self._write_enqueue_pending = 0
        self._write_enqueue_idle = asyncio.Event()
        self._write_enqueue_idle.set()
        self._session_active = False
        self._recording = False
        self._session_id = ""
        self._dataset_id = ""
        self._dataset_name = ""
        self._task = ""
        self._dataset_dir: Path | None = None
        self._episode_index = 0
        self._episode_started_at = 0.0
        self._episode_start_monotonic_s = 0.0
        self._sampler_start_monotonic_s = 0.0
        self._record_loop_start_monotonic_s = 0.0
        self._episode_source_time0_s = 0.0
        self._session_started_at = 0.0
        self._episode_frames = 0
        self._queued_episode_frames = 0
        self._episode_late_frames = 0
        self._camera_drops = {key: 0 for key in CAMERA_KEYS}
        self._tick_target_monotonic_s = 0.0
        self._tick_capture_monotonic_s = 0.0
        self._tick_skews_ms: list[float] = []
        self._last_telemetry_frame_update_s = 0.0
        self._drop_counts: dict[str, int] = {key: 0 for key in (*CAMERA_KEYS, *SOURCE_KEYS)}
        self._stale_counts: dict[str, int] = {key: 0 for key in SOURCE_KEYS}
        self._cache_counts: dict[str, int] = {key: 0 for key in SOURCE_KEYS}
        self._late_source_frames: dict[str, int] = {}
        self._source_skews_ms: dict[str, list[float]] = {key: [] for key in SOURCE_KEYS}
        self._source_elapsed_ms: dict[str, list[float]] = {key: [] for key in SOURCE_KEYS}
        self._source_fail_streaks: dict[str, int] = {key: 0 for key in SOURCE_KEYS}
        self._source_warnings: list[str] = []
        self._max_force_left = 0.0
        self._max_force_right = 0.0
        self._native_dataset: Any | None = None
        self._native_error = ""
        self._native_use_videos = False
        self._native_dataset_from_index = 0
        self._native_total_frames_cached = 0
        self._last_native_camera_frames: dict[str, Any] = {}
        self._last_camera_cache_used: dict[str, bool] = {key: False for key in CAMERA_KEYS}
        self._record_fps_hz = 30
        self._force_sample_hz = 200.0
        self._recording_config_snapshot: dict[str, Any] = {}
        self._source_sample_indices: dict[str, int] = {key: 0 for key in SOURCE_KEYS}
        self._sample_buffers: dict[str, TimedRingBuffer] = self._new_sample_buffers({})
        self._frame_assembler = FrameAssembler(self)
        self._quality_tracker = RecordingQualityTracker(self)
        self._last_saved_episode: dict[str, Any] | None = None

    async def start_session(self, dataset_name: str, task: str) -> dict[str, Any]:
        """创建录制会话，初始化写入路径并启动采样任务。"""
        async with self._lock:
            if self._session_active:
                raise RuntimeError("record session already active")
            config = self.settings.get_config()
            self._recording_config_snapshot = dict(config)
            self._dataset_name = dataset_name.strip() or "micro_assembly_v1"
            self._dataset_id = self._safe_id(self._dataset_name)
            self._task = task.strip() or "unspecified task"
            self._session_id = f"session-{now_ms()}"
            self._last_saved_episode = None
            self._record_fps_hz = self._record_fps_from_config(config)
            self._force_sample_hz = self._force_sample_hz_from_config(config)
            dataset_root = self._dataset_root(config)
            dataset_root.mkdir(parents=True, exist_ok=True)
            self._dataset_dir = dataset_root / self._dataset_id
            self._native_dataset = None
            self._native_error = ""
            self._native_total_frames_cached = 0
            self._last_native_camera_frames = {}
            self._last_camera_cache_used = {key: False for key in CAMERA_KEYS}
            self._write_queue = queue.Queue(maxsize=WRITE_QUEUE_MAX_FRAMES)
            self._writer_thread = LeRobotWriterThread(self, self._write_queue)
            self._writer_thread.start()
        native_started = await self._try_begin_native_dataset(config)
        if not native_started:
            await self._stop_writer_task()
            raise RuntimeError(self._native_required_message())
        async with self._lock:
            self._write_appstation_info(self._require_dataset_dir(), config)
            self._episode_index = self._next_episode_index(self._dataset_dir)
            self._session_started_at = time.monotonic()
            self._session_active = True
            self._write_enqueue_pending = 0
            self._write_enqueue_idle.set()
            self._begin_episode_locked()
            self._start_sampler_tasks_locked()
            self._loop_task = asyncio.create_task(self._record_loop(), name="dataset-recorder")
            self.telemetry.episode_count = self._episode_index
            self.telemetry.frame_count = 0
            self.telemetry.recording = True
        if self._real_hardware_mode(config):
            await self._refresh_gripper_cache(config)
        await self.teleop.start("recording")
        await self._wait_for_episode_warmup()
        self.logs.info("[LEROBOT]", f"record session started: {self._dataset_name} episode={self._episode_index:06d}")
        return self.status()

    async def save_episode(self) -> dict[str, Any]:
        """停止当前 episode 采集，等待已排队数据落盘并返回保存结果。"""
        async with self._lock:
            if not self._session_active:
                raise RuntimeError("record session is not active")
            self._recording = False
            self.telemetry.recording = False
        await self._drain_recording_queues()
        if self._native_writer_active():
            await self._save_native_episode()
        async with self._lock:
            episode = self._finalize_episode_locked(status="review", deleted=False)
            self._last_saved_episode = episode
            self.telemetry.episode_count = self._episode_index + 1
        await self.teleop.stop("recording")
        self.logs.info("[LEROBOT]", f"record episode saved: {episode['id']}")
        return {"episode": episode, "status": self.status()}

    async def discard_episode(self) -> dict[str, Any]:
        """丢弃当前或最近保存的 episode，并用相同序号重新录制。"""
        async with self._lock:
            if not self._session_active:
                raise RuntimeError("record session is not active")
            was_recording = self._recording
            if was_recording:
                self._recording = False
                self.telemetry.recording = False
        if was_recording:
            await self._drain_recording_queues()
            if self._native_writer_active():
                await self._clear_native_episode_buffer()
        async with self._lock:
            if not was_recording and self._last_saved_episode is not None:
                self._mark_saved_episode_deleted_locked(self._last_saved_episode)
                self._last_saved_episode = None
            self._begin_episode_locked()
            self.telemetry.recording = True
            self.telemetry.frame_count = 0
        await self.teleop.start("recording")
        await self._wait_for_episode_warmup()
        self.logs.warning("[LEROBOT]", f"record episode discarded; rerecording episode={self._episode_index:06d}")
        return self.status()

    async def skip_reset(self) -> dict[str, Any]:
        """跳过复位确认，清理未保存数据并开始下一条 episode。"""
        async with self._lock:
            if not self._session_active:
                raise RuntimeError("record session is not active")
            was_recording = self._recording
            if was_recording:
                self._recording = False
                self.telemetry.recording = False
        if was_recording:
            await self._drain_recording_queues()
            if self._native_writer_active():
                await self._clear_native_episode_buffer()
        async with self._lock:
            self._last_saved_episode = None
            self._begin_episode_locked()
            self.telemetry.recording = True
            self.telemetry.frame_count = 0
        await self.teleop.start("recording")
        await self._wait_for_episode_warmup()
        self.logs.info("[LEROBOT]", f"reset skipped; recording episode={self._episode_index:06d}")
        return self.status()

    async def finish_session(self) -> dict[str, Any]:
        """结束录制会话，停止后台任务并释放 native 数据集资源。"""
        async with self._lock:
            was_recording = self._session_active and self._recording
            if was_recording:
                self._recording = False
                self.telemetry.recording = False
        if was_recording:
            await self._drain_recording_queues()
            if self._native_writer_active():
                await self._clear_native_episode_buffer()
        async with self._lock:
            self._recording = False
            self._session_active = False
            self._last_saved_episode = None
            self.telemetry.recording = False
            self.telemetry.frame_count = 0
        await self.teleop.stop("recording")
        task = self._loop_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._loop_task = None
        await self._stop_sampler_tasks()
        await self._finalize_native_dataset()
        await self._stop_writer_task()
        self.logs.info("[LEROBOT]", "record session finished")
        return self.status()

    def status(self) -> dict[str, Any]:
        """返回当前录制会话的状态、帧计数、写入格式和 native 错误。"""
        elapsed = max(0.0, time.monotonic() - self._episode_started_at) if self._recording else 0.0
        return {
            "session": self._session_id,
            "datasetId": self._dataset_id,
            "datasetName": self._dataset_name,
            "task": self._task,
            "active": self._session_active,
            "recording": self._recording,
            "episodeIndex": self._episode_index,
            "frameCount": max(self._queued_episode_frames, self._episode_frames),
            "lateFrames": self._episode_late_frames,
            "elapsedS": round(elapsed, 3),
            "fps": self._record_fps_hz,
            "forceSampleHz": self._force_sample_hz,
            "datasetRoot": str(self._dataset_root(self.settings.get_config())),
            "format": "lerobot-v3-native" if self._native_writer_active() else "lerobot-v3-native-required",
            "nativeError": self._native_error,
            "teleop": self.teleop.status(),
        }

    def list_datasets(self) -> list[dict[str, Any]]:
        """扫描本地数据集目录，汇总可见 episode 和展示信息。"""
        root = self._dataset_root(self.settings.get_config())
        if not root.exists():
            return []
        datasets: list[dict[str, Any]] = []
        for dataset_dir in (item for item in root.iterdir() if item.is_dir()):
            info = self._read_json(dataset_dir / "meta" / "info.json")
            if not info:
                continue
            app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
            native_format = self._is_native_dataset(dataset_dir, info)
            episodes = self._read_episodes(dataset_dir)
            if not episodes and native_format:
                episodes = self._native_episodes_from_meta(dataset_dir, info)
            visible_episodes = [
                episode
                for episode in episodes
                if not bool(episode.get("deleted", False)) and str(episode.get("status", "review")) != "invalid"
            ]
            datasets.append(
                {
                    "id": dataset_dir.name,
                    "name": str(app_info.get("name") or info.get("name") or dataset_dir.name),
                    "status": str(app_info.get("status") or info.get("status") or "local"),
                    "root": str(dataset_dir),
                    "fps": int(info.get("fps", 30)),
                    "createdAt": int(info.get("createdAt", 0)),
                    "updatedAt": int(app_info.get("updatedAt") or info.get("updatedAt", 0)),
                    "format": str(
                        app_info.get("format")
                        or info.get("format")
                        or ("lerobot-v3-native" if native_format else "lerobot-v3-native-required")
                    ),
                    "episodes": [
                        self._episode_for_api(dataset_dir, dataset_dir.name, episode) for episode in visible_episodes
                    ],
                }
            )
        return sorted(datasets, key=lambda item: int(item.get("updatedAt", 0)), reverse=True)

    def create_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
        """创建空数据集目录，并写入 native metadata。"""
        config = self.settings.get_config()
        name = str(payload.get("name") or f"dataset_{now_ms()}").strip()
        dataset_id = self._safe_id(name)
        dataset_dir = self._dataset_root(config) / dataset_id
        suffix = 1
        while dataset_dir.exists() and (dataset_dir / "meta" / "info.json").exists():
            dataset_id = self._safe_id(f"{name}_{suffix}")
            dataset_dir = self._dataset_root(config) / dataset_id
            suffix += 1
        previous_name = self._dataset_name
        try:
            self._dataset_name = name
            dataset_dir.mkdir(parents=True, exist_ok=True)
            if not self._create_native_dataset_metadata(dataset_dir, config):
                try:
                    if dataset_dir.exists() and not any(dataset_dir.iterdir()):
                        dataset_dir.rmdir()
                except OSError:
                    pass
                raise RuntimeError(self._native_required_message())
        finally:
            self._dataset_name = previous_name
        return {"dataset": self._episode_dataset_stub(dataset_dir)}

    def update_dataset(self, dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """更新数据集展示信息和本地元数据。"""
        dataset_dir = self._dataset_path(dataset_id)
        info_path = dataset_dir / "meta" / "info.json"
        info = self._read_json(info_path)
        if not info:
            raise FileNotFoundError(dataset_id)
        name = str(payload.get("name", "")).strip()
        if self._is_native_dataset(dataset_dir, info):
            app_info_path = dataset_dir / "meta" / "appstation_info.json"
            app_info = self._read_json(app_info_path)
            if name:
                app_info["name"] = name
            app_info.setdefault("name", dataset_dir.name)
            app_info.setdefault("status", "local")
            app_info.setdefault("format", "lerobot-v3-native")
            app_info["updatedAt"] = now_ms()
            self._write_json(app_info_path, app_info)
            return {"dataset": self._episode_dataset_stub(dataset_dir)}
        if name:
            info["name"] = name
        info["updatedAt"] = now_ms()
        self._write_json(info_path, info)
        return {"dataset": self._episode_dataset_stub(dataset_dir)}

    def delete_dataset(self, dataset_id: str) -> dict[str, Any]:
        """处理录制服务逻辑：delete_dataset。"""
        dataset_dir = self._dataset_path(dataset_id)
        root = self._dataset_root(self.settings.get_config()).resolve()
        target = dataset_dir.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("refusing to delete outside dataset root") from exc
        if not dataset_dir.exists():
            raise FileNotFoundError(dataset_id)
        shutil.rmtree(dataset_dir)
        return {"deleted": dataset_id}

    def save_review(self, dataset_id: str) -> dict[str, Any]:
        """处理录制服务逻辑：save_review。"""
        dataset_dir = self._dataset_path(dataset_id)
        info_path = dataset_dir / "meta" / "info.json"
        info = self._read_json(info_path)
        if not info:
            raise FileNotFoundError(dataset_id)
        if self._is_native_dataset(dataset_dir, info):
            app_info_path = dataset_dir / "meta" / "appstation_info.json"
            app_info = self._read_json(app_info_path)
            app_info.setdefault("name", dataset_dir.name)
            app_info.setdefault("status", "local")
            app_info.setdefault("format", "lerobot-v3-native")
            app_info["updatedAt"] = now_ms()
            self._write_json(app_info_path, app_info)
            return {"saved": dataset_id, "updatedAt": app_info["updatedAt"]}
        info["updatedAt"] = now_ms()
        self._write_json(info_path, info)
        return {"saved": dataset_id, "updatedAt": info["updatedAt"]}

    def export_dataset(self, dataset_id: str) -> dict[str, Any]:
        """处理录制服务逻辑：export_dataset。"""
        dataset_dir = self._dataset_path(dataset_id)
        if not (dataset_dir / "meta" / "info.json").exists():
            raise FileNotFoundError(dataset_id)
        config = self.settings.get_config()
        push_enabled = bool(config.get("storage", {}).get("pushToHub", False))
        info = self._read_json(dataset_dir / "meta" / "info.json")
        fmt = "lerobot-v3-native" if self._is_native_dataset(dataset_dir, info) else "lerobot-v3-native-required"
        return {
            "dataset": dataset_id,
            "pushToHub": push_enabled,
            "exported": False,
            "format": fmt,
            "message": f"pushToHub is disabled; local {fmt} dataset is ready",
        }

    def dataset_stats(self, dataset_id: str) -> dict[str, Any]:
        """处理录制服务逻辑：dataset_stats。"""
        dataset_dir = self._dataset_path(dataset_id)
        info = self._read_json(dataset_dir / "meta" / "info.json")
        if not info:
            raise FileNotFoundError(dataset_id)
        episodes = self._visible_episodes_for_dataset(dataset_dir, info)
        status_counts: dict[str, int] = {}
        warnings: list[str] = []
        total_frames = 0
        total_duration = 0.0
        max_force_left = 0.0
        max_force_right = 0.0
        for episode in episodes:
            status = str(episode.get("status") or "review")
            status_counts[status] = status_counts.get(status, 0) + 1
            total_frames += int(episode.get("frames", 0))
            total_duration += float(episode.get("durationS", 0.0))
            max_force_left = max(max_force_left, float(episode.get("maxForceLeft", 0.0)))
            max_force_right = max(max_force_right, float(episode.get("maxForceRight", 0.0)))
            raw_warnings = episode.get("warnings", [])
            if isinstance(raw_warnings, list):
                warnings.extend(str(item) for item in raw_warnings[:5])
        return {
            "dataset": dataset_id,
            "format": (
                "lerobot-v3-native"
                if self._is_native_dataset(dataset_dir, info)
                else str(info.get("format", "unknown"))
            ),
            "fps": int(info.get("fps", 30)),
            "episodes": len(episodes),
            "frames": total_frames,
            "durationS": round(total_duration, 3),
            "statusCounts": status_counts,
            "maxForceLeft": max_force_left,
            "maxForceRight": max_force_right,
            "warnings": warnings[:50],
            "features": info.get("features", {}),
        }

    def episode_detail(self, dataset_id: str, episode_id: str) -> dict[str, Any]:
        """处理录制服务逻辑：episode_detail。"""
        dataset_dir = self._dataset_path(dataset_id)
        info = self._read_json(dataset_dir / "meta" / "info.json")
        if not info:
            raise FileNotFoundError(dataset_id)
        episode = next(
            (
                item
                for item in self._visible_episodes_for_dataset(dataset_dir, info)
                if str(item.get("id")) == episode_id
            ),
            None,
        )
        if episode is None:
            raise FileNotFoundError(episode_id)
        return {"episode": self._episode_for_api(dataset_dir, dataset_id, episode)}

    def split_dataset(self, dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理录制服务逻辑：split_dataset。"""
        dataset_dir = self._dataset_path(dataset_id)
        info = self._read_json(dataset_dir / "meta" / "info.json")
        if not info:
            raise FileNotFoundError(dataset_id)
        episodes = self._visible_episodes_for_dataset(dataset_dir, info)
        episode_ids = sorted(str(episode.get("id", "")) for episode in episodes if episode.get("id"))
        ratios = payload.get("ratios", {})
        if not isinstance(ratios, dict):
            ratios = {}
        train_ratio = self._positive_ratio(ratios.get("train", 0.8), 0.8)
        val_ratio = self._positive_ratio(ratios.get("val", 0.1), 0.1)
        test_ratio = self._positive_ratio(ratios.get("test", 0.1), 0.1)
        total_ratio = max(train_ratio + val_ratio + test_ratio, 0.001)
        count = len(episode_ids)
        train_count = min(count, int(round(count * train_ratio / total_ratio)))
        val_count = min(count - train_count, int(round(count * val_ratio / total_ratio)))
        split = {
            "train": episode_ids[:train_count],
            "val": episode_ids[train_count : train_count + val_count],
            "test": episode_ids[train_count + val_count :],
        }
        payload_out = {"dataset": dataset_id, "createdAt": now_ms(), "ratios": ratios, "splits": split}
        self._write_json(dataset_dir / "meta" / "splits.json", payload_out)
        return payload_out

    def clean_dataset(self, dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理录制服务逻辑：clean_dataset。"""
        dataset_dir = self._dataset_path(dataset_id)
        info = self._read_json(dataset_dir / "meta" / "info.json")
        if not info:
            raise FileNotFoundError(dataset_id)
        min_frames = int(payload.get("minFrames", 1) or 1)
        max_late_ratio = float(payload.get("maxLateFrameRatio", 0.2) or 0.2)
        apply = bool(payload.get("apply", False))
        episodes = self._visible_episodes_for_dataset(dataset_dir, info)
        issues: list[dict[str, Any]] = []
        invalid_ids: set[str] = set()
        for episode in episodes:
            episode_id = str(episode.get("id", ""))
            frames = int(episode.get("frames", 0))
            late = int(episode.get("lateFrames", 0))
            reasons: list[str] = []
            if frames < min_frames:
                reasons.append(f"frames<{min_frames}")
            if frames > 0 and late / frames > max_late_ratio:
                reasons.append(f"late_ratio>{max_late_ratio:.3f}")
            if episode.get("status") == "invalid":
                reasons.append("already_invalid")
            if reasons:
                invalid_ids.add(episode_id)
                issues.append({"episodeId": episode_id, "reasons": reasons})
        applied = False
        if apply and invalid_ids and (dataset_dir / "meta" / "episodes.jsonl").exists():
            stored = self._read_episodes(dataset_dir)
            for episode in stored:
                if str(episode.get("id")) in invalid_ids:
                    episode["status"] = "invalid"
                    episode["updatedAt"] = now_ms()
            self._write_episodes(dataset_dir, stored)
            applied = True
        report = {"dataset": dataset_id, "apply": apply, "applied": applied, "issues": issues, "checked": len(episodes)}
        self._write_json(dataset_dir / "meta" / "cleaning_report.json", report)
        return report

    def push_dataset(self, dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理录制服务逻辑：push_dataset。"""
        dataset_dir = self._dataset_path(dataset_id)
        info = self._read_json(dataset_dir / "meta" / "info.json")
        if not info:
            raise FileNotFoundError(dataset_id)
        config = self.settings.get_config()
        repo_id = str(payload.get("repoId") or "").strip()
        dry_run = bool(payload.get("dryRun", True))
        push_enabled = bool(config.get("storage", {}).get("pushToHub", False))
        if not push_enabled or dry_run:
            return {
                "dataset": dataset_id,
                "repoId": repo_id,
                "pushed": False,
                "dryRun": True,
                "message": "Hub upload is dry-run or disabled; dataset remains local",
            }
        if not repo_id:
            raise RuntimeError("repoId is required when pushToHub is enabled")
        imports = self._native_imports()
        if imports is None:
            raise RuntimeError("lerobot[dataset] is not installed in backend runtime")
        LeRobotDataset, _np = imports
        dataset = LeRobotDataset(f"local/{dataset_id}", root=dataset_dir)
        dataset.push_to_hub(repo_id=repo_id)
        return {"dataset": dataset_id, "repoId": repo_id, "pushed": True, "dryRun": False}

    def update_episode(self, dataset_id: str, episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """处理录制服务逻辑：update_episode。"""
        dataset_dir = self._dataset_path(dataset_id)
        episodes = self._read_episodes(dataset_dir)
        updated = False
        for episode in episodes:
            if str(episode.get("id")) != episode_id:
                continue
            name = str(payload.get("name", "")).strip()
            status = str(payload.get("status", "")).strip()
            if name:
                episode["name"] = name
            if status in {"valid", "review", "invalid"}:
                episode["status"] = status
            episode["updatedAt"] = now_ms()
            updated = True
            break
        if not updated:
            raise FileNotFoundError(episode_id)
        self._write_episodes(dataset_dir, episodes)
        return {"episode": episode_id}

    def delete_episode(self, dataset_id: str, episode_id: str) -> dict[str, Any]:
        """处理录制服务逻辑：delete_episode。"""
        dataset_dir = self._dataset_path(dataset_id)
        episodes = self._read_episodes(dataset_dir)
        updated = False
        for episode in episodes:
            if str(episode.get("id")) == episode_id:
                episode["deleted"] = True
                episode["status"] = "invalid"
                episode["updatedAt"] = now_ms()
                updated = True
                break
        if not updated:
            raise FileNotFoundError(episode_id)
        self._write_episodes(dataset_dir, episodes)
        return {"deleted": episode_id}

    def resolve_file(self, dataset_id: str, relative_path: str) -> Path:
        """处理录制服务逻辑：resolve_file。"""
        dataset_dir = self._dataset_path(dataset_id)
        target = (dataset_dir / relative_path).resolve()
        root = dataset_dir.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("file path is outside dataset") from exc
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(relative_path)
        return target

    def resolve_frame_image(self, dataset_id: str, episode_id: str, camera: str, frame: int) -> bytes:
        """处理录制服务逻辑：resolve_frame_image。"""
        if camera not in CAMERA_FEATURE_KEYS:
            raise FileNotFoundError(camera)
        dataset_dir = self._dataset_path(dataset_id)
        episodes = self._read_episodes(dataset_dir)
        if not episodes:
            episodes = self._native_episodes_from_meta(dataset_dir, self._read_json(dataset_dir / "meta" / "info.json"))
        episode = next((item for item in episodes if str(item.get("id")) == episode_id), None)
        if episode is None:
            raise FileNotFoundError(episode_id)
        if not bool(episode.get("native", False)):
            data_path = str(episode.get("dataPath", ""))
            feature_key = CAMERA_FEATURE_KEYS[camera]
            for item in self._fallback_frame_records(dataset_dir, data_path):
                if int(item.get("frame_index", -1)) != frame:
                    continue
                raw_path = item.get(feature_key)
                if isinstance(raw_path, str):
                    path = self.resolve_file(dataset_id, raw_path)
                    if path.suffix.lower() == ".mp4":
                        return self._decode_video_frame_to_jpeg(path, frame)
                    return path.read_bytes()
            raise FileNotFoundError(data_path)
        imports = self._native_imports()
        if imports is None:
            raise FileNotFoundError("lerobot")
        LeRobotDataset, np = imports
        dataset = self._open_native_dataset_for_read(dataset_id, dataset_dir, LeRobotDataset)
        absolute_index = int(episode.get("datasetFromIndex") or 0) + max(0, int(frame))
        if absolute_index >= len(dataset):
            raise FileNotFoundError(str(frame))
        item = dataset[absolute_index]
        image = item.get(CAMERA_FEATURE_KEYS[camera])
        return self._encode_rgb_tensor_to_jpeg(image, np)

    # 内部说明。
    def _new_sample_buffers(self, config: dict[str, Any]) -> dict[str, TimedRingBuffer]:
        """处理录制服务逻辑：_new_sample_buffers。"""
        retention_s = self._sample_buffer_retention_s(config)
        return {
            source: TimedRingBuffer(
                retention_s=retention_s,
                maxlen=max(8, int(self._source_sample_rate_hz(source, config) * retention_s) + 4),
            )
            for source in SOURCE_KEYS
        }

    def _sample_buffer_retention_s(self, config: dict[str, Any]) -> float:
        """Cover warmup, downstream delay, sampling jitter and assembler lookback."""
        storage = config.get("storage", {}) if isinstance(config.get("storage"), dict) else {}
        fps = max(1, self._record_fps_from_config(config))
        consumer_delay_s = self._positive_ratio(
            storage.get("maxConsumerLatencyS"),
            WRITE_QUEUE_MAX_FRAMES / fps,
        )
        jitter_s = self._positive_ratio(storage.get("maxSampleJitterS"), MAX_SAMPLE_JITTER_S)
        lookback_s = self._positive_ratio(storage.get("sampleLookbackWindowS"), SAMPLE_LOOKBACK_WINDOW_S)
        return max(
            RING_BUFFER_RETENTION_S,
            RECORDER_HARDWARE_WARMUP_S + consumer_delay_s + jitter_s + lookback_s,
        )

    def _native_writer_active(self) -> bool:
        """Return whether the native writer thread owns an open dataset."""
        return getattr(self, "_writer_thread", None) is not None and getattr(self, "_native_dataset", None) is not None

    def _recording_config(self) -> dict[str, Any]:
        """Return the session-scoped recording config snapshot."""
        return self._recording_config_snapshot or self.settings.get_config()

    async def _wait_for_episode_warmup(self) -> None:
        """Wait until the recorder epoch that follows hardware sampler warmup."""
        delay_s = self._record_loop_start_monotonic_s - time.monotonic()
        if delay_s > 0.0:
            await asyncio.sleep(delay_s)

    async def _native_writer_command(self, kind: str) -> Any:
        """Run a native writer command without blocking the event loop."""
        writer = self._writer_thread
        if writer is None:
            return None
        future = writer.submit(kind)
        return await asyncio.to_thread(future.result)

    async def _try_begin_native_dataset(self, config: dict[str, Any]) -> bool:
        """Open or resume the native dataset on the writer thread."""
        _ = config
        if not self._native_recording_requested():
            self._native_error = "native LeRobot disabled by APPSTATION_LEROBOT_NATIVE"
            return False
        preflight = self._native_preflight()
        if preflight:
            self._native_error = preflight
            return False
        try:
            total_frames = await self._native_writer_command("open")
            self._native_dataset = self._writer_thread
            self._native_total_frames_cached = int(total_frames or 0)
            return True
        except Exception as exc:  # noqa: BLE001
            self._native_dataset = None
            self._native_error = str(exc)
            return False

    async def _save_native_episode(self) -> None:
        """Persist the current native episode on the writer thread."""
        try:
            total_frames = await self._native_writer_command("save_episode")
            self._native_total_frames_cached = int(total_frames or 0)
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)
            raise RuntimeError(f"native LeRobot save_episode failed: {exc}") from exc

    async def _clear_native_episode_buffer(self) -> None:
        """Clear an unsaved native episode on the writer thread."""
        try:
            await self._native_writer_command("clear_episode")
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)

    async def _finalize_native_dataset(self) -> None:
        """Finalize the native dataset on the writer thread."""
        if self._writer_thread is None:
            self._native_dataset = None
            return
        try:
            await self._native_writer_command("finalize")
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)
        finally:
            self._native_dataset = None

    def _start_sampler_tasks_locked(self) -> None:
        """处理录制服务逻辑：_start_sampler_tasks_locked。"""
        self._sampler_stop_event.clear()
        for source in SOURCE_KEYS:
            thread = self._sampler_threads.get(source)
            if thread is None or not thread.is_alive():
                self._sampler_threads[source] = Thread(
                    target=self._sample_source_loop,
                    args=(source,),
                    name=f"dataset-sampler-{source}",
                    daemon=True,
                )
                self._sampler_threads[source].start()

    async def _stop_sampler_tasks(self) -> None:
        """处理录制服务逻辑：_stop_sampler_tasks。"""
        self._sampler_stop_event.set()
        threads = [thread for thread in self._sampler_threads.values() if thread.is_alive()]
        for thread in threads:
            await asyncio.to_thread(thread.join, 1.0)
        self._sampler_threads = {}

    async def _drain_recording_queues(self) -> None:
        # 内部说明。
        """处理录制服务逻辑：_drain_recording_queues。"""
        await self._write_enqueue_idle.wait()
        await asyncio.to_thread(self._write_queue.join)

    async def _stop_writer_task(self) -> None:
        # 停止 writer 前必须先排空队列，保证 metadata 的帧数和磁盘内容一致。
        """处理录制服务逻辑：_stop_writer_task。"""
        await self._write_enqueue_idle.wait()
        await asyncio.to_thread(self._write_queue.join)
        writer = self._writer_thread
        if writer is None:
            return
        await asyncio.to_thread(self._write_queue.put, None)
        await asyncio.to_thread(writer.join, 2.0)
        self._writer_thread = None

    def _sample_source_loop(self, source: str) -> None:
        """Sample one hardware source on its own cadence."""
        epoch_s = 0.0
        sample_index = 0
        while self._session_active and not self._sampler_stop_event.is_set():
            try:
                config = self._recording_config()
                start_s = self._sampler_start_monotonic_s
                if start_s <= 0.0:
                    self._sampler_stop_event.wait(0.01)
                    continue
                if start_s != epoch_s:
                    epoch_s = start_s
                    sample_index = 0
                    self._source_sample_indices[source] = 0
                rate_hz = self._source_sample_rate_hz(source, config)
                period_s = 1.0 / rate_hz
                next_sample_s = epoch_s + self._source_sample_timestamp_s(source, sample_index, config)
                now = time.monotonic()
                if now > next_sample_s + period_s:
                    sample_index = max(sample_index, int(math.floor((now - epoch_s) * rate_hz)))
                    next_sample_s = epoch_s + self._source_sample_timestamp_s(source, sample_index, config)
                if now < next_sample_s:
                    if self._sampler_stop_event.wait(next_sample_s - now):
                        return
                target_s = self._source_sample_timestamp_s(source, sample_index, config)
                sample = self._sample_source_once_sync(source, config, epoch_s + target_s)
                if epoch_s != self._sampler_start_monotonic_s:
                    continue
                self._sample_buffers.setdefault(source, TimedRingBuffer()).append(sample)
                sample_index += 1
                self._source_sample_indices[source] = sample_index
            except Exception as exc:  # noqa: BLE001
                self.logs.warning("[LEROBOT]", f"{source} sampler recovered: {exc}")
                self._sampler_stop_event.wait(0.1)

    def _next_phase_aligned_sample_time(self, source: str, config: dict[str, Any], now_s: float) -> float:
        """Return the next wall-clock sample time from the shared hardware epoch."""
        start_s = self._sampler_start_monotonic_s or now_s
        rate_hz = self._source_sample_rate_hz(source, config)
        if now_s < start_s:
            return start_s
        next_index = int(math.floor((now_s - start_s) * rate_hz)) + 1
        return start_s + self._source_sample_timestamp_s(source, next_index, config)

    def _source_sample_timestamp_s(self, source: str, sample_index: int, config: dict[str, Any]) -> float:
        """Return the hardware-relative timestamp for a source sample index."""
        return max(0, int(sample_index)) / self._source_sample_rate_hz(source, config)

    def _record_target_timestamp_s(self, frame_index: int) -> float:
        """Return the absolute monotonic target timestamp for a recorder frame."""
        record_start_s = self._sampler_start_monotonic_s or self._episode_start_monotonic_s
        return record_start_s + RECORDER_HARDWARE_WARMUP_S + max(0, int(frame_index)) / max(1, self._record_fps_hz)

    async def _sample_source_once(self, source: str, config: dict[str, Any], target_s: float) -> TimedSample:
        """Sample a concrete hardware source for a preassigned absolute timestamp."""
        return await asyncio.to_thread(self._sample_source_once_sync, source, config, target_s)

    def _sample_source_once_sync(self, source: str, config: dict[str, Any], target_s: float) -> TimedSample:
        """Sample a concrete hardware source from its dedicated sampler thread."""
        if source == "hal":
            return asyncio.run(self._timed_source("hal", self.hal.motion_state(), target_s, record_quality=False))
        if source == "omega":
            return asyncio.run(self._timed_source("omega", self.hal.omega_state(), target_s, record_quality=False))
        if source == "force":
            return self._sample_force_source_sync(config, target_s)
        if source == "gripper":
            return self._gripper_source_sync(config, target_s, record_quality=False)
        camera = CAMERA_KEY_BY_SOURCE.get(source)
        if camera is not None:
            return self._sample_camera_source_sync(config, camera, target_s)
        return self._fallback_sample(source, target_s, f"{source} unsupported")

    async def _sample_force_source(self, config: dict[str, Any], target_s: float) -> TimedSample:
        """处理录制服务逻辑：_sample_force_source。"""
        return await asyncio.to_thread(self._sample_force_source_sync, config, target_s)

    def _sample_force_source_sync(self, config: dict[str, Any], target_s: float) -> TimedSample:
        """Read force data in the force sampler thread and timestamp it immediately."""
        if not self._real_hardware_mode(config):
            sampled_at = time.monotonic()
            value = SimpleNamespace(
                ok=True,
                left=list(self.telemetry.force_left),
                right=list(self.telemetry.force_right),
                sample_monotonic_s=sampled_at,
            )
            return SourceSample("force", sampled_at, value, True, "", target_monotonic_s=target_s)
        started = time.monotonic()
        try:
            value = self.hardware.force.sample(config)
            ok = bool(getattr(value, "ok", True) if not isinstance(value, dict) else value.get("ok", True))
            raw_message = getattr(value, "message", "") if not isinstance(value, dict) else value.get("message", "")
            message = "" if ok else str(raw_message)
        except Exception as exc:  # noqa: BLE001
            value = None
            ok = False
            message = f"force failed: {exc}"
        finished = time.monotonic()
        sample_time = self._source_sample_monotonic(value, finished) if ok else target_s
        return SourceSample(
            "force",
            sample_time,
            value,
            ok,
            message,
            target_monotonic_s=target_s,
            started_monotonic_s=started,
            finished_monotonic_s=finished,
            elapsed_ms=(finished - started) * 1000.0,
            stale=not ok,
        )

    async def _sample_camera_source(self, config: dict[str, Any], camera: str, target_s: float) -> TimedSample:
        """处理录制服务逻辑：_sample_camera_source。"""
        return await asyncio.to_thread(self._sample_camera_source_sync, config, camera, target_s)

    def _sample_camera_source_sync(self, config: dict[str, Any], camera: str, target_s: float) -> TimedSample:
        """Read one camera source in its sampler thread and keep its last valid frame."""
        source = CAMERA_SOURCE_KEYS[camera]
        feature_key = CAMERA_FEATURE_KEYS[camera]
        started = time.monotonic()
        if not self._real_hardware_mode(config):
            value = self._camera_placeholder_value(feature_key)
            finished = time.monotonic()
            return SourceSample(
                source,
                finished,
                value,
                True,
                "",
                target_monotonic_s=target_s,
                started_monotonic_s=started,
                finished_monotonic_s=finished,
                elapsed_ms=(finished - started) * 1000.0,
        )
        try:
            if not self._native_writer_active():
                raise RuntimeError("native LeRobot writer is not open")
            value, sampled_at = self._camera_recording_frame_with_time(config, camera)
            self._last_native_camera_frames[feature_key] = value
            ok = True
            message = ""
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"{source} cache used: {exc}"
            value = self._last_camera_value(camera, feature_key)
            sampled_at = None
        finished = time.monotonic()
        sample_time = sampled_at if sampled_at is not None else finished
        return SourceSample(
            source,
            sample_time,
            value,
            ok,
            message,
            target_monotonic_s=target_s,
            started_monotonic_s=started,
            finished_monotonic_s=finished,
            elapsed_ms=(finished - started) * 1000.0,
            timed_out=(finished - started) > self._source_timeout_s(source),
            stale=not ok,
            cache_used=not ok,
        )

    def _aligned_sample(self, source: str, target_s: float) -> TimedSample:
        """处理录制服务逻辑：_aligned_sample。"""
        buffer = self._sample_buffers.get(source)
        max_skew_s = SOURCE_MAX_SKEW_S.get(source, 0.020)
        sample = buffer.nearest(target_s, max_skew_s) if buffer is not None else None
        if sample is None and buffer is not None:
            sample = buffer.nearest(target_s, math.inf)
            if sample is not None:
                sample = replace(
                    sample,
                    target_monotonic_s=target_s,
                    stale=True,
                    cache_used=sample.cache_used or source in CAMERA_KEY_BY_SOURCE,
                    message=sample.message or f"{source} stale",
                )
        if sample is None:
            sample = self._fallback_sample(source, target_s, f"{source} missing")
        elif sample.value is None:
            fallback = self._fallback_sample(source, target_s, sample.message or f"{source} missing")
            sample = replace(sample, value=fallback.value, stale=True)
        self._record_source_quality(sample, target_s, sample.elapsed_ms)
        return sample

    def _fallback_sample(self, source: str, target_s: float, message: str) -> TimedSample:
        """处理录制服务逻辑：_fallback_sample。"""
        value: Any = None
        if source == "hal":
            value = {"positions": list(self.telemetry.motion_positions), "pulses": [0.0] * 12}
        elif source == "force":
            value = SimpleNamespace(
                ok=True,
                left=list(self.telemetry.force_left),
                right=list(self.telemetry.force_right),
            )
        elif source == "gripper":
            value = list(self.telemetry.gripper_positions)
        elif source in CAMERA_KEY_BY_SOURCE:
            camera = CAMERA_KEY_BY_SOURCE[source]
            value = self._last_camera_value(camera, CAMERA_FEATURE_KEYS[camera])
        return SourceSample(
            source,
            target_s,
            value,
            False,
            message,
            target_monotonic_s=target_s,
            stale=True,
            cache_used=source in CAMERA_KEY_BY_SOURCE,
        )

    async def _record_loop(self) -> None:
        # 瑜版洖鍩楀顏嗗箚閹稿娲伴弽?FPS 鐠嬪啫瀹?閹便垹鎶氶崣顏囶唶鐠愩劑鍣洪幐鍥ㄧ垼,娑撳秳鑵戦弬顓熸拱鏉烆噣鍣伴梿?
        # 硬件采样由后台 sampler 产生，本循环只做时间对齐、组帧和入队。
        """按训练帧率调度录制帧，完成对齐、组帧和入队。"""
        while self._session_active:
            try:
                async with self._lock:
                    recording = self._recording
                    frame_index = self._queued_episode_frames
                    episode_index = self._episode_index
                    record_start = self._record_loop_start_monotonic_s or time.monotonic()
                    period_s = 1.0 / max(1, self._record_fps_hz)
                if not recording:
                    await asyncio.sleep(0.05)
                    continue
                target_tick = self._record_target_timestamp_s(frame_index)
                scheduled_tick = target_tick
                now = time.monotonic()
                if now < scheduled_tick:
                    await asyncio.sleep(scheduled_tick - now)
                    now = time.monotonic()
                capture_tick = now
                frame = await self._collect_frame(target_tick, frame_index=frame_index)
                pending = PendingFrame(
                    episode_index=episode_index,
                    frame_index=frame_index,
                    timestamp=float(frame["timestamp"]),
                    frame=frame,
                )
                queued = False
                async with self._lock:
                    if (
                        not self._recording
                        or self._episode_index != episode_index
                        or frame_index != self._queued_episode_frames
                    ):
                        continue
                    self._quality_tracker.record_tick_locked(target_tick, scheduled_tick, capture_tick, period_s)
                    self._queued_episode_frames = frame_index + 1
                    if time.monotonic() - self._last_telemetry_frame_update_s >= 0.1:
                        self.telemetry.frame_count = max(self.telemetry.frame_count, self._queued_episode_frames)
                        self._last_telemetry_frame_update_s = time.monotonic()
                    try:
                        self._write_queue.put_nowait(pending)
                        queued = True
                    except queue.Full:
                        self._write_enqueue_pending += 1
                        self._write_enqueue_idle.clear()
                        pass
                if not queued:
                    # 内部说明。
                    try:
                        await asyncio.to_thread(self._write_queue.put, pending, True, WRITE_QUEUE_PUT_TIMEOUT_S)
                    except queue.Full:
                        async with self._lock:
                            self._recording = False
                            self.telemetry.recording = False
                            self._episode_late_frames += 1
                            self._source_warnings.append("writer backpressure timeout")
                            self._native_error = "writer queue backpressure timeout"
                        self.logs.error("[LEROBOT]", "writer queue backpressure timeout; current episode stopped")
                    finally:
                        async with self._lock:
                            self._write_enqueue_pending = max(0, self._write_enqueue_pending - 1)
                            if self._write_enqueue_pending == 0:
                                self._write_enqueue_idle.set()
                next_target = record_start + (frame_index + 1) * period_s
                await asyncio.sleep(max(0.0, next_target - time.monotonic()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logs.error("[LEROBOT]", f"record loop recovered: {exc}")
                await asyncio.sleep(0.2)

    async def _collect_frame(
        self,
        target_monotonic_s: float | None = None,
        *,
        frame_index: int | None = None,
    ) -> dict[str, Any]:
        """按目标对齐时间组装一帧训练数据。"""
        config = self._recording_config()
        frame_index = self._episode_frames if frame_index is None else frame_index
        target_monotonic_s = (
            target_monotonic_s if target_monotonic_s is not None else self._record_target_timestamp_s(frame_index)
        )
        return self._frame_assembler.assemble(config, target_monotonic_s, frame_index)

    async def _cached_source(
        self,
        source: str,
        value: Any,
        target_monotonic_s: float,
        *,
        record_quality: bool = True,
    ) -> SourceSample:
        # 内部说明。
        """处理录制服务逻辑：_cached_source。"""
        now = time.monotonic()
        sample = SourceSample(
            source,
            target_monotonic_s,
            value,
            True,
            "",
            target_monotonic_s=target_monotonic_s,
            started_monotonic_s=now,
            finished_monotonic_s=now,
        )
        if record_quality:
            self._record_source_quality(sample, target_monotonic_s, 0.0)
        return sample

    async def _gripper_source(
        self,
        config: dict[str, Any],
        target_monotonic_s: float,
        *,
        record_quality: bool = True,
    ) -> SourceSample:
        # 夹爪位置由 telemetry/worker 后台刷新；录制帧只读取缓存并检查是否过期。
        """处理录制服务逻辑：_gripper_source。"""
        return await asyncio.to_thread(
            self._gripper_source_sync,
            config,
            target_monotonic_s,
            record_quality=record_quality,
        )

    def _gripper_source_sync(
        self,
        config: dict[str, Any],
        target_monotonic_s: float,
        *,
        record_quality: bool = True,
    ) -> SourceSample:
        """Read gripper cache in the gripper sampler thread."""
        if not self._real_hardware_mode(config):
            sampled_at = time.monotonic()
            sample = SourceSample(
                "gripper",
                sampled_at,
                list(self.telemetry.gripper_positions),
                True,
                "",
                target_monotonic_s=target_monotonic_s,
                started_monotonic_s=sampled_at,
                finished_monotonic_s=sampled_at,
            )
            if record_quality:
                self._record_source_quality(sample, target_monotonic_s, 0.0)
            return sample
        self._refresh_gripper_cache_sync(config)
        stale_after_s = self._positive_ratio(config.get("gripper", {}).get("sampleStaleMs"), 2500.0) / 1000.0
        stale_after_s = max(0.5, stale_after_s)
        last_sample_at = float(getattr(self.telemetry, "_last_gripper_sample_at", 0.0) or 0.0)
        age_s = time.monotonic() - last_sample_at if last_sample_at > 0 else math.inf
        ok = age_s <= stale_after_s
        message = "" if ok else f"gripper stale: {round(age_s, 3)}s"
        finished = time.monotonic()
        sample_time = self._gripper_sample_monotonic(target_monotonic_s, finished)
        sample = SourceSample(
            "gripper",
            sample_time,
            list(self.telemetry.gripper_positions),
            ok,
            message,
            target_monotonic_s=target_monotonic_s,
            started_monotonic_s=sample_time,
            finished_monotonic_s=finished,
            stale=not ok,
        )
        if record_quality:
            self._record_source_quality(sample, target_monotonic_s, 0.0)
        return sample

    async def _refresh_gripper_cache(self, config: dict[str, Any]) -> None:
        """处理录制服务逻辑：_refresh_gripper_cache。"""
        await asyncio.to_thread(self._refresh_gripper_cache_sync, config)

    def _refresh_gripper_cache_sync(self, config: dict[str, Any]) -> None:
        """Refresh gripper cache from a sampler thread."""
        refresh_gripper = getattr(self.telemetry, "refresh_gripper_positions", None)
        if callable(refresh_gripper):
            refresh_gripper(config, time.monotonic())

    async def _timed_source(
        self,
        source: str,
        awaitable: Any,
        target_monotonic_s: float,
        *,
        record_quality: bool = True,
    ) -> SourceSample:
        # 记录每个硬件源相对录制 tick 的时序，用于质量统计而不写入训练帧。
        """处理录制服务逻辑：_timed_source。"""
        started = time.monotonic()
        try:
            value = await asyncio.wait_for(awaitable, timeout=self._source_timeout_s(source))
            ok = True
            message = ""
            timed_out = False
        except TimeoutError:
            value = None
            ok = False
            message = f"{source} timeout"
            timed_out = True
        except Exception as exc:  # noqa: BLE001
            value = None
            ok = False
            message = f"{source} failed: {exc}"
            timed_out = False
        finished = time.monotonic()
        elapsed_ms = (finished - started) * 1000.0
        fallback_sample_time = finished if ok else target_monotonic_s
        sample = SourceSample(
            source,
            self._source_sample_monotonic(value, fallback_sample_time),
            value,
            ok,
            message,
            target_monotonic_s=target_monotonic_s,
            started_monotonic_s=started,
            finished_monotonic_s=finished,
            elapsed_ms=elapsed_ms,
            timed_out=timed_out,
            stale=not ok,
            cache_used=source == "camera" and any(self._last_camera_cache_used.values()),
        )
        if record_quality:
            self._record_source_quality(sample, target_monotonic_s, elapsed_ms)
        return sample

    def _source_timeout_s(self, source: str) -> float:
        """处理录制服务逻辑：_source_timeout_s。"""
        return SOURCE_TIMEOUT_S.get(source, 0.020)

    def _source_sample_rate_hz(self, source: str, config: dict[str, Any]) -> float:
        """处理录制服务逻辑：_source_sample_rate_hz。"""
        if source == "force":
            return min(max(self._force_sample_hz_from_config(config), 1.0), 200.0)
        if source == "gripper":
            return min(max(self._positive_ratio(config.get("gripper", {}).get("sampleHz"), 30.0), 1.0), 60.0)
        if source in CAMERA_KEY_BY_SOURCE:
            return min(max(self._positive_ratio(config.get("cameras", {}).get("fps"), self._record_fps_hz), 1.0), 60.0)
        return min(max(float(self._record_fps_from_config(config)), 1.0), 60.0)

    def _force_values_from_sample(self, value: Any) -> tuple[list[float], list[float]] | None:
        """处理录制服务逻辑：_force_values_from_sample。"""
        if value is None:
            return None
        if isinstance(value, dict):
            left = value.get("left")
            right = value.get("right")
            ok = bool(value.get("ok", left is not None and right is not None))
        else:
            left = getattr(value, "left", None)
            right = getattr(value, "right", None)
            ok = bool(getattr(value, "ok", left is not None and right is not None))
        if not ok or not isinstance(left, list) or not isinstance(right, list):
            return None
        return (
            [float(item) for item in (left + [0.0] * 6)[:6]],
            [float(item) for item in (right + [0.0] * 6)[:6]],
        )

    def _source_sample_monotonic(self, value: Any, fallback_monotonic_s: float) -> float:
        """处理录制服务逻辑：_source_sample_monotonic。"""
        raw_relative = self._source_sample_relative_monotonic_s(value)
        if raw_relative is not None:
            return raw_relative
        raw_s = self._source_sample_absolute_monotonic_s(value)
        if raw_s is not None:
            return raw_s
        return fallback_monotonic_s

    def _source_sample_absolute_monotonic_s(self, value: Any) -> float | None:
        """Return a driver's process-monotonic sample timestamp when present."""
        if value is None:
            return None
        if isinstance(value, dict):
            candidates = (
                (value.get("sample_monotonic_s"), 1.0),
                (value.get("received_monotonic_ms"), 0.001),
                (value.get("monotonicMs"), 0.001),
            )
        else:
            candidates = (
                (getattr(value, "sample_monotonic_s", None), 1.0),
                (getattr(value, "received_monotonic_ms", None), 0.001),
                (getattr(value, "monotonicMs", None), 0.001),
            )
        for raw, scale in candidates:
            parsed = self._coerce_float(raw)
            if parsed is not None and parsed > 0:
                return parsed * scale
        return None

    def _source_sample_relative_monotonic_s(self, value: Any) -> float | None:
        """Return an explicitly supplied monotonic sample timestamp if present."""
        raw = value.get("monotonic_s") if isinstance(value, dict) else getattr(value, "monotonic_s", None)
        parsed = self._coerce_float(raw)
        return parsed if parsed is not None and parsed >= 0 else None

    def _gripper_sample_monotonic(self, fallback_monotonic_s: float, fallback_absolute_s: float) -> float:
        """Return the freshest gripper cache timestamp in host monotonic time."""
        samples = getattr(self.telemetry, "gripper_samples", {}) or {}
        latest_s: float | None = None
        if isinstance(samples, dict):
            for sample in samples.values():
                raw = sample.get("monotonicMs") if isinstance(sample, dict) else None
                parsed = self._coerce_float(raw)
                if parsed is None or parsed <= 0:
                    continue
                absolute_s = parsed / 1000.0
                latest_s = absolute_s if latest_s is None else max(latest_s, absolute_s)
        if latest_s is not None:
            return latest_s
        return fallback_absolute_s if fallback_absolute_s > 0 else fallback_monotonic_s

    def _coerce_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _record_source_quality(self, sample: SourceSample, target_monotonic_s: float, elapsed_ms: float) -> None:
        # timeout 用 drop 边界，warning 用更严格的 skew 边界；这样慢但成功的源也会进入质量报告。
        """处理录制服务逻辑：_record_source_quality。"""
        skew_ms = (sample.monotonic_s - target_monotonic_s) * 1000.0
        abs_skew_ms = abs(skew_ms)
        self._source_skews_ms.setdefault(sample.source, []).append(skew_ms)
        self._source_elapsed_ms.setdefault(sample.source, []).append(elapsed_ms)
        warning_threshold = SOURCE_WARNING_SKEW_MS.get(sample.source)
        if warning_threshold is not None and abs_skew_ms > warning_threshold:
            late_source_frames = getattr(self, "_late_source_frames", {})
            late_source_frames[sample.source] = late_source_frames.get(sample.source, 0) + 1
            self._late_source_frames = late_source_frames
            self._source_warnings.append(f"{sample.source} skew {round(abs_skew_ms, 3)}ms")
        if sample.stale:
            stale_counts = getattr(self, "_stale_counts", {})
            stale_counts[sample.source] = stale_counts.get(sample.source, 0) + 1
            self._stale_counts = stale_counts
        if sample.cache_used:
            cache_counts = getattr(self, "_cache_counts", {})
            cache_counts[sample.source] = cache_counts.get(sample.source, 0) + 1
            self._cache_counts = cache_counts
            camera = CAMERA_KEY_BY_SOURCE.get(sample.source)
            if camera is not None:
                self._camera_drops[camera] = self._camera_drops.get(camera, 0) + 1
        drop_threshold = SOURCE_DROP_SKEW_MS.get(sample.source)
        if sample.ok and drop_threshold is not None and abs_skew_ms > drop_threshold:
            self._drop_counts[sample.source] = self._drop_counts.get(sample.source, 0) + 1
        if sample.ok:
            self._source_fail_streaks[sample.source] = 0
            return
        self._drop_counts[sample.source] = self._drop_counts.get(sample.source, 0) + 1
        streak = self._source_fail_streaks.get(sample.source, 0) + 1
        self._source_fail_streaks[sample.source] = streak
        if sample.message:
            self._source_warnings.append(sample.message)
        if streak >= 3:
            self._source_warnings.append(f"{sample.source} consecutive failures: {streak}")

    def _native_frame_payload(self, frame: dict[str, Any]) -> dict[str, Any]:
        """处理录制服务逻辑：_native_frame_payload。"""
        native_frame = {
            "observation.state": self._np_float32(frame["observation.state"]),
            "observation.pulses": self._np_float32(frame["observation.pulses"]),
            "observation.force_left": self._np_float32(frame["observation.force_left"]),
            "observation.force_right": self._np_float32(frame["observation.force_right"]),
            "action": self._np_float32(frame["action"]),
            "task": self._task,
        }
        images = frame.get("images", {})
        if isinstance(images, dict):
            for feature_key in CAMERA_FEATURE_KEYS.values():
                image = images.get(feature_key)
                if image is None:
                    image = self._synthetic_camera_frame(feature_key)
                native_frame[feature_key] = image
        return native_frame

    def _mark_frame_written(self, frame: dict[str, Any]) -> None:
        """Record writer-thread progress for the current episode."""
        self._episode_frames += 1
        self._max_force_left = max(self._max_force_left, self._force_norm(frame["observation.force_left"]))
        self._max_force_right = max(self._max_force_right, self._force_norm(frame["observation.force_right"]))

    def _recording_motion_positions(
        self,
        config: dict[str, Any],
        positions: list[float],
        pulses: list[float],
    ) -> list[float]:
        """处理录制服务逻辑：_recording_motion_positions。"""
        origin = config.get("motion", {}).get("origin", {})
        if not isinstance(origin, dict) or len(pulses) != 12:
            return positions
        next_positions = (list(positions) + [0.0] * 12)[:12]
        relative_pulses = list(pulses)
        left_origin = self._origin_side_pulses(origin, "left")
        right_origin = self._origin_side_pulses(origin, "right")
        left_valid = bool(origin.get("leftValid", origin.get("valid", False)))
        right_valid = bool(origin.get("rightValid", origin.get("valid", False)))
        if left_valid and left_origin is not None:
            relative_pulses[:6] = [float(pulses[index]) - left_origin[index] for index in range(6)]
        if right_valid and right_origin is not None:
            relative_pulses[6:12] = [float(pulses[index + 6]) - right_origin[index] for index in range(6)]
        if not left_valid and not right_valid:
            return next_positions
        relative_positions = pulses_to_ui_state(relative_pulses)
        if left_valid and left_origin is not None:
            next_positions[:6] = relative_positions[:6]
        if right_valid and right_origin is not None:
            next_positions[6:12] = relative_positions[6:12]
        return next_positions

    def _origin_side_pulses(self, origin: dict[str, Any], side: str) -> list[float] | None:
        """处理录制服务逻辑：_origin_side_pulses。"""
        key = "leftPulse" if side == "left" else "rightPulse"
        raw = origin.get(key)
        if not isinstance(raw, list) or len(raw) < 6:
            return None
        try:
            return [float(value) for value in raw[:6]]
        except (TypeError, ValueError):
            return None

    def _begin_episode_locked(self) -> None:
        """初始化单个 episode 的时间轴、缓存和质量统计。"""
        # 每个 episode 独立统计质量指标，保存或丢弃时可以精确回滚。
        sampler_start_s = time.monotonic() + SAMPLER_START_LEAD_S
        record_start_s = sampler_start_s + RECORDER_HARDWARE_WARMUP_S
        self._sampler_start_monotonic_s = sampler_start_s
        self._record_loop_start_monotonic_s = record_start_s
        self._episode_started_at = record_start_s
        self._episode_start_monotonic_s = record_start_s
        self._episode_source_time0_s = RECORDER_HARDWARE_WARMUP_S
        self._episode_frames = 0
        self._queued_episode_frames = 0
        self._episode_late_frames = 0
        self._camera_drops = {key: 0 for key in CAMERA_KEYS}
        self._tick_target_monotonic_s = 0.0
        self._tick_capture_monotonic_s = 0.0
        self._tick_skews_ms = []
        self._last_telemetry_frame_update_s = 0.0
        self._drop_counts = {key: 0 for key in (*CAMERA_KEYS, *SOURCE_KEYS)}
        self._stale_counts = {key: 0 for key in SOURCE_KEYS}
        self._cache_counts = {key: 0 for key in SOURCE_KEYS}
        self._late_source_frames = {}
        self._source_skews_ms = {key: [] for key in SOURCE_KEYS}
        self._source_elapsed_ms = {key: [] for key in SOURCE_KEYS}
        self._source_fail_streaks = {key: 0 for key in SOURCE_KEYS}
        self._sample_buffers = self._new_sample_buffers(self._recording_config())
        self._source_sample_indices = {key: 0 for key in SOURCE_KEYS}
        self._source_warnings = []
        self._last_camera_cache_used = {key: False for key in CAMERA_KEYS}
        self._max_force_left = 0.0
        self._max_force_right = 0.0
        if not self._native_writer_active():
            raise DatasetSaveError(self._native_required_message())
        self._native_dataset_from_index = self._native_total_frames_cached
        self._recording = True

    def _finalize_episode_locked(self, *, status: str, deleted: bool) -> dict[str, Any]:
        """处理录制服务逻辑：_finalize_episode_locked。"""
        dataset_dir = self._require_dataset_dir()
        duration_s = max(0.0, time.monotonic() - self._episode_started_at)
        native = self._native_writer_active()
        if not native and not deleted:
            raise DatasetSaveError(self._native_required_message())
        episode_id = f"episode_{self._episode_index:06d}"
        skew = self._skew_stats()
        source_skew = self._source_skew_stats()
        # 每个 episode 独立统计质量指标，保存或丢弃时可以精确回滚。
        episode = {
            "id": episode_id,
            "name": episode_id,
            "task": self._task,
            "status": status,
            "deleted": deleted,
            "episodeIndex": self._episode_index,
            "frames": self._episode_frames,
            "fps": self._record_fps_hz,
            "durationS": round(duration_s, 3),
            "createdAt": now_ms(),
            "dataPath": "",
            "datasetFromIndex": self._native_dataset_from_index if native else None,
            "datasetToIndex": self._native_dataset_from_index + self._episode_frames if native else None,
            "native": native,
            "lateFrames": self._episode_late_frames,
            "cameraDrops": dict(self._camera_drops),
            "dropCounts": dict(self._drop_counts),
            "staleCounts": dict(self._stale_counts),
            "cacheCounts": dict(self._cache_counts),
            "maxSkewMs": skew["maxSkewMs"],
            "avgSkewMs": skew["avgSkewMs"],
            "jitterMs": skew["jitterMs"],
            "sourceMaxSkewMs": source_skew["maxSkewMs"],
            "sourceAvgSkewMs": source_skew["avgSkewMs"],
            "sourceJitterMs": source_skew["jitterMs"],
            "maxForceLeft": round(self._max_force_left, 6),
            "maxForceRight": round(self._max_force_right, 6),
            "warnings": self._quality_warnings(),
        }
        episodes = self._read_episodes(dataset_dir)
        episodes = [item for item in episodes if str(item.get("id")) != episode_id]
        episodes.append(episode)
        self._write_episodes(dataset_dir, episodes)
        info_path = dataset_dir / "meta" / "info.json"
        info = self._read_json(info_path)
        if info:
            if native:
                info["updatedAt"] = now_ms()
                self._write_appstation_info(dataset_dir, self.settings.get_config())
        self._episode_index += 1
        return episode

    def _mark_saved_episode_deleted_locked(self, episode: dict[str, Any]) -> None:
        """处理录制服务逻辑：_mark_saved_episode_deleted_locked。"""
        dataset_dir = self._require_dataset_dir()
        episode_id = str(episode.get("id", ""))
        episodes = self._read_episodes(dataset_dir)
        for item in episodes:
            if str(item.get("id", "")) == episode_id:
                item["deleted"] = True
                item["status"] = "invalid"
                item["updatedAt"] = now_ms()
                break
        self._write_episodes(dataset_dir, episodes)

    def _write_appstation_info(self, dataset_dir: Path, config: dict[str, Any]) -> None:
        """处理录制服务逻辑：_write_appstation_info。"""
        path = dataset_dir / "meta" / "appstation_info.json"
        info = self._read_json(path)
        payload = {
            "name": str(info.get("name") or self._dataset_name),
            "status": str(info.get("status", "local")) if info else "local",
            "format": "lerobot-v3-native",
            "codebase_version": "v3.0",
            "nativeLeRobotAvailable": True,
            "useVideos": self._native_use_videos,
            "vcodec": self._native_vcodec(),
            "recording": {
                "fps": self._record_fps_hz,
                "forceSampleHz": self._force_sample_hz,
                "alignment": (
                    "source samplers record host monotonic sample times; "
                    "record frames align at sampler_start_s + warmup_s + frame_index / record_fps"
                ),
            },
            "createdAt": int(info.get("createdAt", now_ms())) if info else now_ms(),
            "updatedAt": now_ms(),
            "hardware": {
                "cameras": config.get("cameras", {}),
                "cameraResolutions": self._camera_resolution_summary_from_config(config),
                "force": {
                    "leftIp": config.get("force", {}).get("leftIp"),
                    "rightIp": config.get("force", {}).get("rightIp"),
                    "sampleHz": config.get("force", {}).get("sampleHz"),
                },
            },
        }
        self._write_json(path, payload)

    def _create_native_dataset_metadata(self, dataset_dir: Path, config: dict[str, Any]) -> bool:
        """处理录制服务逻辑：_create_native_dataset_metadata。"""
        if not self._native_recording_requested():
            self._native_error = "native LeRobot disabled by APPSTATION_LEROBOT_NATIVE"
            return False
        preflight = self._native_preflight()
        if preflight:
            self._native_error = preflight
            return False
        imports = self._native_imports()
        if imports is None:
            self._native_error = "lerobot[dataset] is not installed in backend runtime"
            return False
        if dataset_dir.exists() and any(dataset_dir.iterdir()):
            self._native_error = "dataset directory already contains files"
            return False
        LeRobotDataset, _np = imports
        repo_id = f"local/{dataset_dir.name}"
        previous_use_videos = self._native_use_videos
        self._native_use_videos = self._native_use_videos_requested()
        try:
            if dataset_dir.exists():
                dataset_dir.rmdir()
            dataset = LeRobotDataset.create(
                repo_id=repo_id,
                fps=self._record_fps_from_config(config),
                features=self._native_features(config),
                root=dataset_dir,
                robot_type="dual_arm_micro_assembly",
                use_videos=self._native_use_videos,
                batch_encoding_size=1,
                vcodec=self._native_vcodec(),
                metadata_buffer_size=1,
            )
            dataset.finalize()
            self._write_appstation_info(dataset_dir, config)
            return True
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)
            return False
        finally:
            self._native_use_videos = previous_use_videos

    def _open_native_dataset_for_writer(self) -> Any:
        # 录制时由 writer 线程打开 LeRobot native dataset。
        """处理录制服务逻辑：_open_native_dataset_for_writer。"""
        config = self._recording_config()
        if not self._native_recording_requested():
            raise RuntimeError("native LeRobot disabled by APPSTATION_LEROBOT_NATIVE")
        preflight = self._native_preflight()
        if preflight:
            raise RuntimeError(preflight)
        imports = self._native_imports()
        if imports is None:
            raise RuntimeError("lerobot[dataset] is not installed in backend runtime")
        if self._dataset_dir is None:
            raise RuntimeError("record dataset is not initialized")
        LeRobotDataset, _np = imports
        dataset_dir = self._dataset_dir
        repo_id = f"local/{self._dataset_id}"
        self._native_use_videos = self._native_use_videos_requested()
        try:
            if self._is_native_dataset(dataset_dir, self._read_json(dataset_dir / "meta" / "info.json")):
                if self._native_dataset_is_empty(dataset_dir):
                    # 空的原生目录可能来自上次初始化失败，重建可以修复损坏的 meta。
                    app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
                    shutil.rmtree(dataset_dir)
                    dataset = LeRobotDataset.create(
                        repo_id=repo_id,
                        fps=self._record_fps_hz,
                        features=self._native_features(config),
                        root=dataset_dir,
                        robot_type="dual_arm_micro_assembly",
                        use_videos=self._native_use_videos,
                        batch_encoding_size=1,
                        vcodec=self._native_vcodec(),
                        metadata_buffer_size=1,
                    )
                    if app_info:
                        self._write_json(dataset_dir / "meta" / "appstation_info.json", app_info)
                else:
                    # 每个 episode 独立统计质量指标，保存或丢弃时可以精确回滚。
                    dataset = LeRobotDataset.resume(
                        repo_id=repo_id,
                        root=dataset_dir,
                        batch_encoding_size=1,
                        vcodec=self._native_vcodec(),
                    )
                    self._sync_recording_shape_from_native_info(dataset_dir)
            elif dataset_dir.exists() and any(dataset_dir.iterdir()):
                self._native_error = "dataset directory already contains non-native files"
                raise RuntimeError(self._native_error)
            else:
                if dataset_dir.exists():
                    dataset_dir.rmdir()
                dataset = LeRobotDataset.create(
                    repo_id=repo_id,
                    fps=self._record_fps_hz,
                    features=self._native_features(config),
                    root=dataset_dir,
                    robot_type="dual_arm_micro_assembly",
                    use_videos=self._native_use_videos,
                    batch_encoding_size=1,
                    vcodec=self._native_vcodec(),
                    metadata_buffer_size=1,
                )
            return dataset
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)
            raise

    def _native_required_message(self) -> str:
        """处理录制服务逻辑：_native_required_message。"""
        reason = self._native_error or "native LeRobot dataset initialization failed"
        return f"native LeRobot dataset is required; {reason}"

    def _native_dataset_is_empty(self, dataset_dir: Path) -> bool:
        """处理录制服务逻辑：_native_dataset_is_empty。"""
        data_dir = dataset_dir / "data"
        episode_meta_dir = dataset_dir / "meta" / "episodes"
        return (
            not any(data_dir.glob("chunk-*/*.parquet"))
            and not any(episode_meta_dir.glob("chunk-*/*.parquet"))
            and not self._read_episodes(dataset_dir)
        )

    def _native_features(self, config: dict[str, Any]) -> dict[str, Any]:
        # 这些 feature 名称需要和 LeRobot 数据集字段保持稳定，前后端按它们读取图像。
        """处理录制服务逻辑：_native_features。"""
        image_dtype = "video" if self._native_use_videos else "image"
        features: dict[str, Any] = {
            "observation.state": {"dtype": "float32", "shape": (14,), "names": list(STATE_FEATURE_NAMES)},
            "observation.pulses": {"dtype": "float32", "shape": (12,), "names": list(PULSE_FEATURE_NAMES)},
            "observation.force_left": {"dtype": "float32", "shape": (6,), "names": list(FORCE_FEATURE_NAMES)},
            "observation.force_right": {"dtype": "float32", "shape": (6,), "names": list(FORCE_FEATURE_NAMES)},
            "action": {"dtype": "float32", "shape": (14,), "names": list(ACTION_FEATURE_NAMES)},
        }
        for key, feature_key in CAMERA_FEATURE_KEYS.items():
            width, height = CAMERA_CAPTURE_SIZES[key]
            features[feature_key] = {
                "dtype": image_dtype,
                "shape": (height, width, 3),
                "names": ["height", "width", "channels"],
            }
        return features

    def _native_imports(self) -> tuple[Any, Any] | None:
        """处理录制服务逻辑：_native_imports。"""
        try:
            module = importlib.import_module("lerobot.datasets.lerobot_dataset")
            np = importlib.import_module("numpy")
        except Exception:
            return None
        return module.LeRobotDataset, np

    def _native_preflight(self) -> str:
        """处理录制服务逻辑：_native_preflight。"""
        imports = self._native_imports()
        if imports is None:
            return "lerobot[dataset] is not installed in backend runtime"
        if self._native_use_videos_requested():
            try:
                av = importlib.import_module("av")
            except Exception as exc:  # noqa: BLE001
                return f"PyAV is required for native LeRobot video recording: {exc}"
            codec = self._native_vcodec().lower()
            if codec in {"av1", "libsvtav1", "svt_av1"}:
                try:
                    av.Codec("libsvtav1", "w")
                except Exception as exc:  # noqa: BLE001
                    return f"libsvtav1 encoder is unavailable for native LeRobot video recording: {exc}"
        return ""

    def _native_recording_requested(self) -> bool:
        """处理录制服务逻辑：_native_recording_requested。"""
        value = os.environ.get("APPSTATION_LEROBOT_NATIVE", "auto").strip().lower()
        return value not in {"0", "false", "off", "no"}

    def _native_use_videos_requested(self) -> bool:
        """处理录制服务逻辑：_native_use_videos_requested。"""
        value = os.environ.get("APPSTATION_LEROBOT_USE_VIDEOS", "1").strip().lower()
        return value not in {"0", "false", "off", "no"}

    def _native_vcodec(self) -> str:
        """处理录制服务逻辑：_native_vcodec。"""
        return os.environ.get("APPSTATION_LEROBOT_VCODEC", "h264").strip() or "h264"

    def _resume_native_dataset_locked(self) -> Any:
        """处理录制服务逻辑：_resume_native_dataset_locked。"""
        imports = self._native_imports()
        if imports is None:
            raise RuntimeError("lerobot[dataset] is not installed in backend runtime")
        if self._dataset_dir is None:
            raise RuntimeError("record dataset is not initialized")
        LeRobotDataset, _np = imports
        return LeRobotDataset.resume(
            repo_id=f"local/{self._dataset_id}",
            root=self._dataset_dir,
            batch_encoding_size=1,
            vcodec=self._native_vcodec(),
        )

    def _is_native_dataset_info(self, info: dict[str, Any]) -> bool:
        """处理录制服务逻辑：_is_native_dataset_info。"""
        return str(info.get("format", "")) == "lerobot-v3-native"

    def _is_native_dataset(self, dataset_dir: Path, info: dict[str, Any]) -> bool:
        """Return whether AppStation should treat the dataset as native LeRobot."""
        if self._is_native_dataset_info(info):
            return True
        app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
        return str(app_info.get("format", "")) == "lerobot-v3-native"

    def _open_native_dataset_for_read(self, dataset_id: str, dataset_dir: Path, LeRobotDataset: Any) -> Any:
        """Open a native dataset for API previews across supported LeRobot versions."""
        try:
            return LeRobotDataset(f"local/{dataset_id}", root=dataset_dir, return_uint8=True)
        except TypeError as exc:
            if "return_uint8" not in str(exc):
                raise
            return LeRobotDataset(f"local/{dataset_id}", root=dataset_dir)

    def _camera_recording_frame(self, config: dict[str, Any], camera: str) -> Any:
        """Return an RGB camera frame for native recording without using preview JPEGs."""
        frame, _sampled_at = self._camera_recording_frame_with_time(config, camera)
        return frame

    def _camera_recording_frame_with_time(self, config: dict[str, Any], camera: str) -> tuple[Any, float | None]:
        """Return an RGB camera frame and the capture thread timestamp when available."""
        raw_snapshot_with_time = getattr(self.hardware.cameras, "snapshot_frame_with_timestamp", None)
        if callable(raw_snapshot_with_time):
            snapshot = raw_snapshot_with_time(config, camera)
            frame = getattr(snapshot, "frame", snapshot[0] if isinstance(snapshot, tuple) else snapshot)
            sampled_at = getattr(
                snapshot,
                "monotonic_s",
                snapshot[1] if isinstance(snapshot, tuple) and len(snapshot) > 1 else None,
            )
            return self._coerce_rgb_frame(frame, config, camera), self._coerce_float(sampled_at)
        raw_snapshot = getattr(self.hardware.cameras, "snapshot_frame", None)
        if callable(raw_snapshot):
            return self._coerce_rgb_frame(raw_snapshot(config, camera), config, camera), time.monotonic()
        jpeg = self.hardware.cameras.snapshot(config, camera)
        return self._decode_jpeg_to_rgb(jpeg, config, camera), time.monotonic()

    def _decode_jpeg_to_rgb(self, jpeg: bytes, config: dict[str, Any], camera: str = "global") -> Any:
        """处理录制服务逻辑：_decode_jpeg_to_rgb。"""
        imports = self._native_imports()
        if imports is None:
            raise RuntimeError("native numpy import unavailable")
        _LeRobotDataset, np = imports
        cv2 = importlib.import_module("cv2")
        buffer = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("JPEG decode failed")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        width, height = CAMERA_CAPTURE_SIZES.get(camera, CAMERA_CAPTURE_SIZES["global"])
        if rgb.shape[:2] != (height, width):
            rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        return rgb

    def _coerce_rgb_frame(self, frame: Any, config: dict[str, Any], camera: str = "global") -> Any:
        """Resize a raw RGB camera frame to the LeRobot feature shape."""
        _ = config
        imports = self._native_imports()
        if imports is None:
            return frame
        _LeRobotDataset, np = imports
        cv2 = importlib.import_module("cv2")
        rgb = np.asarray(frame, dtype=np.uint8)
        width, height = CAMERA_CAPTURE_SIZES.get(camera, CAMERA_CAPTURE_SIZES["global"])
        if rgb.shape[:2] != (height, width):
            rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        return rgb

    def _synthetic_camera_frame(self, feature_key: str) -> Any:
        """处理录制服务逻辑：_synthetic_camera_frame。"""
        imports = self._native_imports()
        if imports is None:
            return None
        _LeRobotDataset, np = imports
        camera = next((key for key, value in CAMERA_FEATURE_KEYS.items() if value == feature_key), "global")
        width, height = CAMERA_CAPTURE_SIZES.get(camera, CAMERA_CAPTURE_SIZES["global"])
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        camera_index = (
            list(CAMERA_FEATURE_KEYS.values()).index(feature_key)
            if feature_key in CAMERA_FEATURE_KEYS.values()
            else 0
        )
        frame[:, :, 0] = (camera_index + 1) * 45
        frame[:, :, 1] = (self._episode_frames * 7) % 255
        frame[:, :, 2] = (camera_index + 1) * 70
        return frame

    def _camera_placeholder_value(self, feature_key: str) -> Any:
        """处理录制服务逻辑：_camera_placeholder_value。"""
        return self._synthetic_camera_frame(feature_key)

    def _last_camera_value(self, camera: str, feature_key: str) -> Any:
        """处理录制服务逻辑：_last_camera_value。"""
        _ = camera
        return self._last_native_camera_frames.get(feature_key) or self._synthetic_camera_frame(feature_key)

    def _np_float32(self, values: object) -> Any:
        """处理录制服务逻辑：_np_float32。"""
        imports = self._native_imports()
        if imports is None:
            return values
        _LeRobotDataset, np = imports
        return np.asarray(values, dtype=np.float32)

    def _record_fps_from_config(self, config: dict[str, Any]) -> int:
        """处理录制服务逻辑：_record_fps_from_config。"""
        try:
            raw = float(config.get("storage", {}).get("recordFps") or config.get("cameras", {}).get("fps", 30))
        except (TypeError, ValueError):
            raw = 30.0
        return int(min(max(round(raw), 1), 60))

    def _force_sample_hz_from_config(self, config: dict[str, Any]) -> float:
        """处理录制服务逻辑：_force_sample_hz_from_config。"""
        try:
            raw = float(config.get("force", {}).get("sampleHz", 200))
        except (TypeError, ValueError):
            raw = 200.0
        return min(max(raw, 1.0), 10000.0)

    def _sync_recording_shape_from_native_info(self, dataset_dir: Path) -> None:
        """处理录制服务逻辑：_sync_recording_shape_from_native_info。"""
        info = self._read_json(dataset_dir / "meta" / "info.json")
        try:
            self._record_fps_hz = int(info.get("fps", self._record_fps_hz))
        except (TypeError, ValueError):
            pass

    def _episode_for_api(self, dataset_dir: Path, dataset_id: str, episode: dict[str, Any]) -> dict[str, Any]:
        """处理录制服务逻辑：_episode_for_api。"""
        samples = self._episode_samples(dataset_dir, dataset_id, episode)
        quality = self._episode_quality(episode)
        features = self._feature_summary(dataset_dir)
        camera_resolutions = self._camera_resolution_summary(dataset_dir)
        return {
            "id": str(episode.get("id", "")),
            "name": str(episode.get("name") or episode.get("id") or "episode"),
            "task": str(episode.get("task") or ""),
            "status": str(episode.get("status") or "review"),
            "quality": quality,
            "frames": int(episode.get("frames", 0)),
            "fps": int(episode.get("fps", 30)),
            "durationS": float(episode.get("durationS", 0.0)),
            "createdAt": int(episode.get("createdAt", 0)),
            "warnings": list(episode.get("warnings", [])) if isinstance(episode.get("warnings"), list) else [],
            "samples": samples,
            "lateFrames": int(episode.get("lateFrames", 0)),
            "cameraDrops": episode.get("cameraDrops", {}),
            "dropCounts": episode.get("dropCounts", {}),
            "staleCounts": episode.get("staleCounts", {}),
            "cacheCounts": episode.get("cacheCounts", {}),
            "sourceMaxSkewMs": episode.get("sourceMaxSkewMs", {}),
            "maxForceLeft": float(episode.get("maxForceLeft", 0.0)),
            "maxForceRight": float(episode.get("maxForceRight", 0.0)),
            "features": features,
            "featureSummary": features,
            "cameraResolutions": camera_resolutions,
        }

    def _feature_summary(self, dataset_dir: Path) -> dict[str, Any]:
        """处理录制服务逻辑：_feature_summary。"""
        info = self._read_json(dataset_dir / "meta" / "info.json")
        features = info.get("features", {})
        if not isinstance(features, dict):
            return {}
        wanted = [
            "observation.state",
            "action",
            "observation.pulses",
            "observation.force_left",
            "observation.force_right",
            *CAMERA_FEATURE_KEYS.values(),
        ]
        summary: dict[str, Any] = {}
        for key in wanted:
            value = features.get(key)
            if not isinstance(value, dict):
                continue
            summary[key] = {
                "shape": value.get("shape", []),
                "dtype": value.get("dtype", ""),
                "names": value.get("names", []),
            }
        return summary

    def _camera_resolution_summary(self, dataset_dir: Path) -> dict[str, dict[str, str]]:
        """处理录制服务逻辑：_camera_resolution_summary。"""
        info = self._read_json(dataset_dir / "meta" / "info.json")
        app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
        hardware = app_info.get("hardware") if app_info else info.get("hardware", {})
        if isinstance(hardware, dict):
            existing = hardware.get("cameraResolutions")
            if isinstance(existing, dict):
                return existing
            return self._camera_resolution_summary_from_config({"cameras": hardware.get("cameras", {})})
        return self._camera_resolution_summary_from_config({})

    def _camera_resolution_summary_from_config(self, config: dict[str, Any]) -> dict[str, dict[str, str]]:
        """处理录制服务逻辑：_camera_resolution_summary_from_config。"""
        cameras = config.get("cameras", {}) if isinstance(config.get("cameras"), dict) else {}
        preview = str(cameras.get("previewResolution", "native"))
        result: dict[str, dict[str, str]] = {}
        for key in CAMERA_KEYS:
            width, height = CAMERA_CAPTURE_SIZES[key]
            configured = self._configured_camera_resolution(cameras, key)
            result[key] = {
                "physical": str(cameras.get(f"{key}PhysicalResolution", "native")),
                "capture": configured,
                "preview": preview,
                "saved": f"{width}x{height}",
            }
        return result

    def _configured_camera_resolution(self, cameras: dict[str, Any], camera: str) -> str:
        """处理录制服务逻辑：_configured_camera_resolution。"""
        key = f"{camera}Resolution"
        if camera == "wrist_left":
            key = "wristLeftResolution"
        elif camera == "wrist_right":
            key = "wristRightResolution"
        return str(cameras.get(key, cameras.get("previewResolution", "native")))

    def _episode_samples(
        self,
        dataset_dir: Path,
        dataset_id: str,
        episode: dict[str, Any],
        max_samples: int = 300,
    ) -> list[dict[str, Any]]:
        """处理录制服务逻辑：_episode_samples。"""
        native_episode = bool(episode.get("native", False))
        native_dataset = self._is_native_dataset(dataset_dir, self._read_json(dataset_dir / "meta" / "info.json"))
        if native_episode or native_dataset:
            return self._native_episode_samples(dataset_dir, dataset_id, episode, max_samples)
        data_path = str(episode.get("dataPath", ""))
        records = self._fallback_frame_records(dataset_dir, data_path)
        if not records:
            return []
        stride = max(1, len(records) // max_samples)
        samples: list[dict[str, Any]] = []
        for item in records[::stride]:
            samples.append(self._fallback_sample_for_api(dataset_id, episode, item, len(samples)))
        return samples

    def _fallback_frame_records(self, dataset_dir: Path, data_path: str) -> list[dict[str, Any]]:
        """处理录制服务逻辑：_fallback_frame_records。"""
        if not data_path:
            return []
        path = dataset_dir / data_path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        return records

    def _fallback_sample_for_api(
        self,
        dataset_id: str,
        episode: dict[str, Any],
        item: dict[str, Any],
        fallback_frame: int,
    ) -> dict[str, Any]:
        """处理录制服务逻辑：_fallback_sample_for_api。"""
        state_raw = item.get("observation.state")
        if isinstance(state_raw, list) and len(state_raw) >= 14:
            state = self._lerobot14_to_ui_motion_state([float(value) for value in state_raw])
        elif isinstance(state_raw, list):
            state = lerobot_to_ui_state([float(value) for value in state_raw])
        else:
            state = [0.0] * 12
        pulses_raw = item.get("observation.pulses")
        pulses = [float(value) for value in pulses_raw] if isinstance(pulses_raw, list) else [0.0] * 12
        frame = int(item.get("frame_index", fallback_frame))
        images_raw = item.get("images")
        images: dict[str, str] = {}
        if isinstance(images_raw, dict):
            for key, feature_key in CAMERA_FEATURE_KEYS.items():
                raw_path = images_raw.get(feature_key)
                if isinstance(raw_path, str):
                    images[key] = self._fallback_image_url(dataset_id, str(episode.get("id", "")), key, frame, raw_path)
        for key, feature_key in CAMERA_FEATURE_KEYS.items():
            raw_path = item.get(feature_key)
            if isinstance(raw_path, str):
                images[key] = self._fallback_image_url(dataset_id, str(episode.get("id", "")), key, frame, raw_path)
        return {
            "frame": frame,
            "leftJoints": state[:6],
            "rightJoints": state[6:12],
            "leftPulses": pulses[:6],
            "rightPulses": pulses[6:12],
            "forceLeft": item.get("observation.force_left", [0.0] * 6),
            "forceRight": item.get("observation.force_right", [0.0] * 6),
            "images": images,
        }

    def _fallback_image_url(
        self,
        dataset_id: str,
        episode_id: str,
        camera: str,
        frame: int,
        relative_path: str,
    ) -> str:
        """处理录制服务逻辑：_fallback_image_url。"""
        if relative_path.lower().endswith(".mp4"):
            return (
                f"/api/datasets/{dataset_id}/frame_image"
                f"?episode_id={episode_id}&camera={camera}&frame={frame}"
            )
        return f"/api/datasets/{dataset_id}/file?path={relative_path}"

    def _native_episode_samples(
        self,
        dataset_dir: Path,
        dataset_id: str,
        episode: dict[str, Any],
        max_samples: int,
    ) -> list[dict[str, Any]]:
        """处理录制服务逻辑：_native_episode_samples。"""
        imports = self._native_imports()
        if imports is None:
            return []
        LeRobotDataset, _np = imports
        try:
            dataset = self._open_native_dataset_for_read(dataset_id, dataset_dir, LeRobotDataset)
        except Exception:
            return []
        start = int(episode.get("datasetFromIndex") or 0)
        stop = int(episode.get("datasetToIndex") or min(len(dataset), start + int(episode.get("frames", 0))))
        stop = min(stop, len(dataset))
        if stop <= start:
            return []
        stride = max(1, (stop - start) // max_samples)
        samples: list[dict[str, Any]] = []
        episode_id = str(episode.get("id", ""))
        for absolute_index in range(start, stop, stride):
            try:
                item = dataset[absolute_index]
            except Exception:
                continue
            state = self._tensor_to_float_list(item.get("observation.state"), expected=14)
            pulses = self._tensor_to_float_list(item.get("observation.pulses"), expected=12)
            force_left = self._tensor_to_float_list(item.get("observation.force_left"), expected=6)
            force_right = self._tensor_to_float_list(item.get("observation.force_right"), expected=6)
            frame = int(item.get("frame_index", absolute_index - start))
            images = {
                key: (
                    f"/api/datasets/{dataset_id}/frame_image"
                    f"?episode_id={episode_id}&camera={key}&frame={frame}"
                )
                for key in CAMERA_KEYS
            }
            samples.append(
                {
                    "frame": frame,
                    "leftJoints": self._lerobot14_to_ui_motion_state(state)[:6],
                    "rightJoints": self._lerobot14_to_ui_motion_state(state)[6:12],
                    "leftPulses": pulses[:6],
                    "rightPulses": pulses[6:12],
                    "forceLeft": force_left,
                    "forceRight": force_right,
                    "images": images,
                }
            )
        return samples

    def _tensor_to_float_list(self, value: Any, *, expected: int) -> list[float]:
        """处理录制服务逻辑：_tensor_to_float_list。"""
        if value is None:
            return [0.0] * expected
        if hasattr(value, "detach"):
            value = value.detach().cpu().tolist()
        elif hasattr(value, "tolist"):
            value = value.tolist()
        if not isinstance(value, list):
            return [0.0] * expected
        result = [float(item) for item in value[:expected]]
        return result + [0.0] * max(0, expected - len(result))

    def _native_episodes_from_meta(self, dataset_dir: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
        """处理录制服务逻辑：_native_episodes_from_meta。"""
        if not self._is_native_dataset(dataset_dir, info):
            return []
        try:
            pq = importlib.import_module("pyarrow.parquet")
        except Exception:
            return []
        episodes: list[dict[str, Any]] = []
        for path in sorted((dataset_dir / "meta" / "episodes").glob("chunk-*/file-*.parquet")):
            try:
                data = pq.read_table(path).to_pydict()
            except Exception:
                continue
            count = len(data.get("episode_index", []))
            for row in range(count):
                episode_index = int(data.get("episode_index", [row])[row])
                tasks = data.get("tasks", [[""]])[row]
                task = str(tasks[0]) if isinstance(tasks, list) and tasks else ""
                start = int(data.get("dataset_from_index", [0])[row])
                stop = int(data.get("dataset_to_index", [start])[row])
                frames = int(data.get("length", [max(0, stop - start)])[row])
                episodes.append(
                    {
                        "id": f"episode_{episode_index:06d}",
                        "name": f"episode_{episode_index:06d}",
                        "task": task,
                        "status": "review",
                        "deleted": False,
                        "episodeIndex": episode_index,
                        "frames": frames,
                        "fps": int(info.get("fps", 30)),
                        "durationS": round(frames / max(1, int(info.get("fps", 30))), 3),
                        "createdAt": int(path.stat().st_mtime * 1000),
                        "datasetFromIndex": start,
                        "datasetToIndex": stop,
                        "native": True,
                        "lateFrames": 0,
                        "cameraDrops": {key: 0 for key in CAMERA_KEYS},
                        "dropCounts": {key: 0 for key in (*CAMERA_KEYS, *SOURCE_KEYS)},
                        "staleCounts": {key: 0 for key in SOURCE_KEYS},
                        "cacheCounts": {key: 0 for key in SOURCE_KEYS},
                        "maxSkewMs": 0.0,
                        "avgSkewMs": 0.0,
                        "jitterMs": 0.0,
                        "sourceMaxSkewMs": {key: 0.0 for key in SOURCE_KEYS},
                        "sourceAvgSkewMs": {key: 0.0 for key in SOURCE_KEYS},
                        "sourceJitterMs": {key: 0.0 for key in SOURCE_KEYS},
                        "maxForceLeft": 0.0,
                        "maxForceRight": 0.0,
                        "warnings": [],
                    }
                )
        return episodes

    def _visible_episodes_for_dataset(self, dataset_dir: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
        """处理录制服务逻辑：_visible_episodes_for_dataset。"""
        episodes = self._read_episodes(dataset_dir)
        if not episodes and self._is_native_dataset(dataset_dir, info):
            episodes = self._native_episodes_from_meta(dataset_dir, info)
        return [episode for episode in episodes if not bool(episode.get("deleted", False))]

    def _positive_ratio(self, value: Any, default: float) -> float:
        """处理录制服务逻辑：_positive_ratio。"""
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(parsed, 0.0)

    def _compose_observation_state(self, motion_positions: list[float], gripper_positions: list[Any]) -> list[float]:
        # 将运动状态和夹爪位置组合成 LeRobot v3 的 state。
        """处理录制服务逻辑：_compose_observation_state。"""
        motion = (list(motion_positions) + [0.0] * 12)[:12]
        gripper = [self._float_or_zero(value) for value in (list(gripper_positions) + [0.0, 0.0])[:2]]
        return [
            motion[0],
            motion[1],
            motion[2],
            motion[3] * 1000.0,
            motion[4] * 1000.0,
            motion[5] * 1000.0,
            gripper[0],
            motion[6],
            motion[7],
            motion[8],
            motion[9] * 1000.0,
            motion[10] * 1000.0,
            motion[11] * 1000.0,
            gripper[1],
        ]

    def _lerobot14_to_ui_motion_state(self, state: list[float]) -> list[float]:
        # 将运动状态和夹爪位置组合成 LeRobot v3 的 state。
        """处理录制服务逻辑：_lerobot14_to_ui_motion_state。"""
        values = (list(state) + [0.0] * 14)[:14]
        return [
            values[0],
            values[1],
            values[2],
            values[3] / 1000.0,
            values[4] / 1000.0,
            values[5] / 1000.0,
            values[7],
            values[8],
            values[9],
            values[10] / 1000.0,
            values[11] / 1000.0,
            values[12] / 1000.0,
        ]

    def _float_or_zero(self, value: Any) -> float:
        """处理录制服务逻辑：_float_or_zero。"""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(result) or result < 0:
            return 0.0
        return result

    def _encode_rgb_tensor_to_jpeg(self, image: Any, np: Any) -> bytes:
        """处理录制服务逻辑：_encode_rgb_tensor_to_jpeg。"""
        if image is None:
            raise FileNotFoundError("image")
        if hasattr(image, "detach"):
            image = image.detach().cpu().numpy()
        elif hasattr(image, "numpy"):
            image = image.numpy()
        array = np.asarray(image)
        if array.ndim != 3:
            raise FileNotFoundError("image")
        if array.shape[0] == 3 and array.shape[2] != 3:
            array = np.transpose(array, (1, 2, 0))
        if array.dtype != np.uint8:
            max_value = float(array.max()) if array.size else 0.0
            if max_value <= 1.0:
                array = array * 255.0
            array = np.clip(array, 0, 255).astype(np.uint8)
        cv2 = importlib.import_module("cv2")
        bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
        ok, buffer = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            raise FileNotFoundError("image")
        return bytes(buffer)

    def _decode_video_frame_to_jpeg(self, path: Path, frame_index: int) -> bytes:
        # fallback MP4 复核时按 frame_index 解码单帧，保持前端仍然拿到 JPEG。
        """处理录制服务逻辑：_decode_video_frame_to_jpeg。"""
        cv2 = importlib.import_module("cv2")
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise FileNotFoundError(str(path))
            capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frame_index)))
            ok, frame = capture.read()
            if not ok or frame is None:
                raise FileNotFoundError(f"{path}:{frame_index}")
            ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if not ok:
                raise FileNotFoundError("image")
            return bytes(buffer)
        finally:
            capture.release()

    def _episode_quality(self, episode: dict[str, Any]) -> int:
        """处理录制服务逻辑：_episode_quality。"""
        late = int(episode.get("lateFrames", 0))
        drops_raw = episode.get("cameraDrops", {})
        drops = sum(int(value) for value in drops_raw.values()) if isinstance(drops_raw, dict) else 0
        return max(40, min(99, 96 - late * 2 - drops * 3))

    def _quality_warnings(self) -> list[str]:
        """处理录制服务逻辑：_quality_warnings。"""
        warnings: list[str] = []
        if self._episode_late_frames:
            warnings.append(f"late frames: {self._episode_late_frames}")
        skew = self._skew_stats()
        if skew["maxSkewMs"] > 20.0:
            warnings.append(f"max skew: {skew['maxSkewMs']}ms")
        warnings.extend(dict.fromkeys(self._source_warnings))
        for source, count in self._stale_counts.items():
            if count:
                warnings.append(f"{source} stale: {count}")
        for source, count in self._cache_counts.items():
            if count:
                warnings.append(f"{source} cache used: {count}")
        for key, count in self._camera_drops.items():
            if count:
                warnings.append(f"{key} camera drops: {count}")
        return warnings

    def _skew_stats(self) -> dict[str, float]:
        # 记录每个硬件源相对录制 tick 的时序，用于质量统计而不写入训练帧。
        """处理录制服务逻辑：_skew_stats。"""
        values = [abs(value) for value in self._tick_skews_ms]
        if not values:
            return {"maxSkewMs": 0.0, "avgSkewMs": 0.0, "jitterMs": 0.0}
        deltas = [
            abs(values[index] - values[index - 1])
            for index in range(1, len(values))
        ]
        jitter = sum(deltas) / len(deltas) if deltas else 0.0
        return {
            "maxSkewMs": round(max(values), 3),
            "avgSkewMs": round(sum(values) / len(values), 3),
            "jitterMs": round(jitter, 3),
        }

    def _source_skew_stats(self) -> dict[str, dict[str, float]]:
        """处理录制服务逻辑：_source_skew_stats。"""
        max_skew: dict[str, float] = {}
        avg_skew: dict[str, float] = {}
        jitter: dict[str, float] = {}
        for source, raw_values in self._source_skews_ms.items():
            values = [abs(value) for value in raw_values]
            if not values:
                max_skew[source] = 0.0
                avg_skew[source] = 0.0
                jitter[source] = 0.0
                continue
            deltas = [abs(values[index] - values[index - 1]) for index in range(1, len(values))]
            max_skew[source] = round(max(values), 3)
            avg_skew[source] = round(sum(values) / len(values), 3)
            jitter[source] = round(sum(deltas) / len(deltas), 3) if deltas else 0.0
        return {"maxSkewMs": max_skew, "avgSkewMs": avg_skew, "jitterMs": jitter}

    def _latest_action_vector(
        self,
        observation_state: list[float] | None = None,
        config: dict[str, Any] | None = None,
    ) -> list[float]:
        # action 记录绝对目标：当前 observation.state 加主手增量，夹爪目标取配置值。
        """处理录制服务逻辑：_latest_action_vector。"""
        base = (list(observation_state) + [0.0] * 14)[:14] if observation_state is not None else [0.0] * 14
        config = config or {}
        vector = self._latest_action_delta_vector()
        action = [base[index] + vector[index] for index in range(14)]
        gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
        if gripper:
            action[6] = self._float_or_zero(gripper.get("targetLeftMm", base[6]))
            action[13] = self._float_or_zero(gripper.get("targetRightMm", base[13]))
        return action

    def _latest_action_delta_vector(self) -> list[float]:
        """处理录制服务逻辑：_latest_action_delta_vector。"""
        vector = [0.0] * 14
        last_action = self.teleop.status().get("lastAction")
        if not isinstance(last_action, dict):
            return vector
        ts = int(last_action.get("ts", 0))
        if now_ms() - ts > 1000:
            return vector
        delta_vector = last_action.get("deltaVector")
        if isinstance(delta_vector, list):
            return self._motion_delta_to_action_delta(delta_vector)
        if isinstance(delta_vector, list) and len(delta_vector) == 12:
            converted: list[float] = []
            for index, raw_value in enumerate(delta_vector):
                value = float(raw_value)
                converted.append(value * 1000.0 if index % 6 >= 3 else value)
            return converted
        side = last_action.get("side")
        axis = last_action.get("axis")
        if side not in {"left", "right"} or axis not in {"X", "Y", "Z", "Roll", "Pitch", "Yaw"}:
            return vector
        axis_index = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"].index(str(axis))
        state_index = (0 if side == "left" else 7) + axis_index
        value = float(last_action.get("delta", 0.0))
        vector[state_index] = value * 1000.0 if axis_index >= 3 else value
        return vector

    def _motion_delta_to_action_delta(self, delta_vector: list[Any]) -> list[float]:
        # teleop delta 仍是 12 维位姿增量，这里插入左右夹爪槽位并把旋转从度转成 mdeg。
        """处理录制服务逻辑：_motion_delta_to_action_delta。"""
        motion = [0.0] * 12
        for index, value in enumerate(delta_vector[:12]):
            try:
                motion[index] = float(value)
            except (TypeError, ValueError):
                motion[index] = 0.0
        return [
            motion[0],
            motion[1],
            motion[2],
            motion[3] * 1000.0,
            motion[4] * 1000.0,
            motion[5] * 1000.0,
            0.0,
            motion[6],
            motion[7],
            motion[8],
            motion[9] * 1000.0,
            motion[10] * 1000.0,
            motion[11] * 1000.0,
            0.0,
        ]

    def _dataset_root(self, config: dict[str, Any]) -> Path:
        """处理录制服务逻辑：_dataset_root。"""
        raw = str(config.get("storage", {}).get("datasetRoot", "~/.appstation/datasets"))
        return Path(raw).expanduser()

    def _dataset_path(self, dataset_id: str) -> Path:
        """处理录制服务逻辑：_dataset_path。"""
        root = self._dataset_root(self.settings.get_config())
        return root / self._safe_id(dataset_id)

    def _require_dataset_dir(self) -> Path:
        """处理录制服务逻辑：_require_dataset_dir。"""
        if self._dataset_dir is None:
            raise RuntimeError("record dataset is not initialized")
        return self._dataset_dir

    def _next_episode_index(self, dataset_dir: Path) -> int:
        """处理录制服务逻辑：_next_episode_index。"""
        episodes = self._read_episodes(dataset_dir)
        if not episodes:
            episodes = self._native_episodes_from_meta(dataset_dir, self._read_json(dataset_dir / "meta" / "info.json"))
        indices = [int(item.get("episodeIndex", -1)) for item in episodes]
        return max(indices, default=-1) + 1

    def _read_episodes(self, dataset_dir: Path) -> list[dict[str, Any]]:
        """处理录制服务逻辑：_read_episodes。"""
        path = dataset_dir / "meta" / "episodes.jsonl"
        if not path.exists():
            return []
        episodes: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                episodes.append(item)
        return episodes

    def _write_episodes(self, dataset_dir: Path, episodes: list[dict[str, Any]]) -> None:
        """处理录制服务逻辑：_write_episodes。"""
        path = dataset_dir / "meta" / "episodes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in episodes)
        path.write_text(content, encoding="utf-8")

    def _episode_dataset_stub(self, dataset_dir: Path) -> dict[str, Any]:
        """处理录制服务逻辑：_episode_dataset_stub。"""
        info = self._read_json(dataset_dir / "meta" / "info.json")
        app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
        name = app_info.get("name") or info.get("name") or dataset_dir.name
        return {"id": dataset_dir.name, "name": str(name)}

    def _read_json(self, path: Path) -> dict[str, Any]:
        """处理录制服务逻辑：_read_json。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        """处理录制服务逻辑：_write_json。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_id(self, value: str) -> str:
        """处理录制服务逻辑：_safe_id。"""
        normalized = SAFE_ID.sub("_", value.strip()).strip("._-")
        return normalized[:80] or "dataset"

    def _real_hardware_mode(self, config: dict[str, Any]) -> bool:
        """处理录制服务逻辑：_real_hardware_mode。"""
        mode = os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")
        return str(mode).lower() == "real"

    def _force_norm(self, values: object) -> float:
        """处理录制服务逻辑：_force_norm。"""
        if not isinstance(values, list) or len(values) < 3:
            return 0.0
        return max(abs(float(values[0])), abs(float(values[1])), abs(float(values[2])))
