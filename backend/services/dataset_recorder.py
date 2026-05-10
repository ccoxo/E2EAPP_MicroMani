from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, TextIO

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.core.units import lerobot_to_ui_state, pulses_to_ui_state, ui_to_lerobot_state
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
    "global": (1920, 1080),
    "wrist_left": (1920, 1080),
    "wrist_right": (1920, 1080),
}
STATE_FEATURE_NAMES: tuple[str, ...] = (
    "left_x_um",
    "left_y_um",
    "left_z_um",
    "left_roll_mdeg",
    "left_pitch_mdeg",
    "left_yaw_mdeg",
    "right_x_um",
    "right_y_um",
    "right_z_um",
    "right_roll_mdeg",
    "right_pitch_mdeg",
    "right_yaw_mdeg",
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
    "right_dx_um",
    "right_dy_um",
    "right_dz_um",
    "right_droll_mdeg",
    "right_dpitch_mdeg",
    "right_dyaw_mdeg",
)
FORCE_FEATURE_NAMES: tuple[str, ...] = ("fx", "fy", "fz", "mx", "my", "mz")
GRIPPER_FEATURE_NAMES: tuple[str, str] = ("left_gap_mm", "right_gap_mm")


class DatasetRecorderService:
    """Recorder using the app hardware services with a native LeRobot path.

    If `lerobot[dataset]` is installed, frames are written through
    `LeRobotDataset.create/resume/add_frame/save_episode`. If it is not
    available, the service keeps the previous JSONL/JPEG fallback so the app can
    still record and review data without changing the rest of the UI.
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
        self.settings = settings
        self.hardware = hardware
        self.hal = hal
        self.telemetry = telemetry
        self.logs = logs
        self.teleop = teleop
        self._lock = asyncio.Lock()
        self._loop_task: asyncio.Task[None] | None = None
        self._session_active = False
        self._recording = False
        self._session_id = ""
        self._dataset_id = ""
        self._dataset_name = ""
        self._task = ""
        self._dataset_dir: Path | None = None
        self._episode_index = 0
        self._episode_started_at = 0.0
        self._session_started_at = 0.0
        self._episode_frames = 0
        self._episode_late_frames = 0
        self._camera_drops = {key: 0 for key in CAMERA_KEYS}
        self._max_force_left = 0.0
        self._max_force_right = 0.0
        self._writer: TextIO | None = None
        self._native_dataset: Any | None = None
        self._native_error = ""
        self._native_use_videos = False
        self._native_dataset_from_index = 0
        self._record_fps_hz = 30
        self._force_sample_hz = 200.0
        self._force_window_samples_per_frame = 7
        self._force_window_enabled = True
        self._current_data_path: Path | None = None
        self._current_episode_paths: list[Path] = []
        self._last_saved_episode: dict[str, Any] | None = None

    async def start_session(self, dataset_name: str, task: str) -> dict[str, Any]:
        async with self._lock:
            if self._session_active:
                raise RuntimeError("record session already active")
            config = self.settings.get_config()
            self._dataset_name = dataset_name.strip() or "micro_assembly_v1"
            self._dataset_id = self._safe_id(self._dataset_name)
            self._task = task.strip() or "unspecified task"
            self._session_id = f"session-{now_ms()}"
            self._last_saved_episode = None
            self._record_fps_hz = self._record_fps_from_config(config)
            self._force_sample_hz = self._force_sample_hz_from_config(config)
            self._force_window_samples_per_frame = self._force_window_samples_from_config(config)
            self._force_window_enabled = True
            dataset_root = self._dataset_root(config)
            dataset_root.mkdir(parents=True, exist_ok=True)
            self._dataset_dir = dataset_root / self._dataset_id
            self._native_dataset = None
            self._native_error = ""
            native_started = self._try_begin_native_dataset_locked(config)
            if native_started:
                self._write_appstation_info(self._require_dataset_dir(), config)
            else:
                self._dataset_dir.mkdir(parents=True, exist_ok=True)
                self._write_info(self._dataset_dir, config)
            self._episode_index = self._next_episode_index(self._dataset_dir)
            self._session_started_at = time.monotonic()
            self._session_active = True
            self._loop_task = asyncio.create_task(self._record_loop(), name="dataset-recorder")
            self._begin_episode_locked()
            self.telemetry.episode_count = self._episode_index
            self.telemetry.frame_count = 0
            self.telemetry.recording = True
        await self.teleop.start("recording")
        self.logs.info("[LEROBOT]", f"record session started: {self._dataset_name} episode={self._episode_index:06d}")
        return self.status()

    async def save_episode(self) -> dict[str, Any]:
        async with self._lock:
            if not self._session_active:
                raise RuntimeError("record session is not active")
            episode = self._finalize_episode_locked(status="review", deleted=False)
            self._last_saved_episode = episode
            self._recording = False
            self.telemetry.recording = False
            self.telemetry.episode_count = self._episode_index + 1
        await self.teleop.stop("recording")
        self.logs.info("[LEROBOT]", f"record episode saved: {episode['id']}")
        return {"episode": episode, "status": self.status()}

    async def discard_episode(self) -> dict[str, Any]:
        async with self._lock:
            if not self._session_active:
                raise RuntimeError("record session is not active")
            if self._recording:
                self._close_writer_locked()
                self._discard_current_episode_files_locked()
            elif self._last_saved_episode is not None:
                if self._native_dataset is not None:
                    self._mark_saved_episode_deleted_locked(self._last_saved_episode)
                else:
                    self._remove_saved_episode_locked(self._last_saved_episode)
                self._last_saved_episode = None
            self._begin_episode_locked()
            self.telemetry.recording = True
            self.telemetry.frame_count = 0
        await self.teleop.start("recording")
        self.logs.warning("[LEROBOT]", f"record episode discarded; rerecording episode={self._episode_index:06d}")
        return self.status()

    async def skip_reset(self) -> dict[str, Any]:
        async with self._lock:
            if not self._session_active:
                raise RuntimeError("record session is not active")
            self._last_saved_episode = None
            self._begin_episode_locked()
            self.telemetry.recording = True
            self.telemetry.frame_count = 0
        await self.teleop.start("recording")
        self.logs.info("[LEROBOT]", f"reset skipped; recording episode={self._episode_index:06d}")
        return self.status()

    async def finish_session(self) -> dict[str, Any]:
        async with self._lock:
            if self._session_active and self._recording:
                self._close_writer_locked()
                self._discard_current_episode_files_locked()
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
        self._finalize_native_dataset()
        self.logs.info("[LEROBOT]", "record session finished")
        return self.status()

    def status(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._episode_started_at if self._recording else 0.0
        return {
            "session": self._session_id,
            "datasetId": self._dataset_id,
            "datasetName": self._dataset_name,
            "task": self._task,
            "active": self._session_active,
            "recording": self._recording,
            "episodeIndex": self._episode_index,
            "frameCount": self._episode_frames,
            "lateFrames": self._episode_late_frames,
            "elapsedS": round(elapsed, 3),
            "fps": self._record_fps_hz,
            "forceSampleHz": self._force_sample_hz,
            "forceWindowSamples": self._force_window_samples_per_frame,
            "datasetRoot": str(self._dataset_root(self.settings.get_config())),
            "format": "lerobot-v3-native" if self._native_dataset is not None else "lerobot-compatible-jsonl-fallback",
            "nativeError": self._native_error,
            "teleop": self.teleop.status(),
        }

    def list_datasets(self) -> list[dict[str, Any]]:
        root = self._dataset_root(self.settings.get_config())
        if not root.exists():
            return []
        datasets: list[dict[str, Any]] = []
        for dataset_dir in (item for item in root.iterdir() if item.is_dir()):
            info = self._read_json(dataset_dir / "meta" / "info.json")
            if not info:
                continue
            app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
            native_format = self._is_native_dataset_info(info)
            episodes = self._read_episodes(dataset_dir)
            if not episodes and native_format:
                episodes = self._native_episodes_from_meta(dataset_dir, info)
            visible_episodes = [episode for episode in episodes if not bool(episode.get("deleted", False))]
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
                        or ("lerobot-v3-native" if native_format else "unknown")
                    ),
                    "episodes": [
                        self._episode_for_api(dataset_dir, dataset_dir.name, episode) for episode in visible_episodes
                    ],
                }
            )
        return sorted(datasets, key=lambda item: int(item.get("updatedAt", 0)), reverse=True)

    def create_dataset(self, payload: dict[str, Any]) -> dict[str, Any]:
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
                self._write_info(dataset_dir, config)
        finally:
            self._dataset_name = previous_name
        return {"dataset": self._episode_dataset_stub(dataset_dir)}

    def update_dataset(self, dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        dataset_dir = self._dataset_path(dataset_id)
        info_path = dataset_dir / "meta" / "info.json"
        info = self._read_json(info_path)
        if not info:
            raise FileNotFoundError(dataset_id)
        name = str(payload.get("name", "")).strip()
        if self._is_native_dataset_info(info):
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
        dataset_dir = self._dataset_path(dataset_id)
        info_path = dataset_dir / "meta" / "info.json"
        info = self._read_json(info_path)
        if not info:
            raise FileNotFoundError(dataset_id)
        if self._is_native_dataset_info(info):
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
        dataset_dir = self._dataset_path(dataset_id)
        if not (dataset_dir / "meta" / "info.json").exists():
            raise FileNotFoundError(dataset_id)
        config = self.settings.get_config()
        push_enabled = bool(config.get("storage", {}).get("pushToHub", False))
        info = self._read_json(dataset_dir / "meta" / "info.json")
        fmt = "lerobot-v3-native" if self._is_native_dataset_info(info) else "lerobot-compatible-jsonl-fallback"
        return {
            "dataset": dataset_id,
            "pushToHub": push_enabled,
            "exported": False,
            "format": fmt,
            "message": f"pushToHub is disabled; local {fmt} dataset is ready",
        }

    def dataset_stats(self, dataset_id: str) -> dict[str, Any]:
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
            "format": "lerobot-v3-native" if self._is_native_dataset_info(info) else str(info.get("format", "unknown")),
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

    def split_dataset(self, dataset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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
            frame_count = max(1, int(episode.get("frames", 1)))
            for sample in self._episode_samples(dataset_dir, dataset_id, episode, max_samples=frame_count):
                if int(sample.get("frame", -1)) == frame:
                    image = sample.get("images", {}).get(camera) if isinstance(sample.get("images"), dict) else None
                    if isinstance(image, str) and "?path=" in image:
                        return self.resolve_file(dataset_id, image.split("?path=", 1)[1]).read_bytes()
            raise FileNotFoundError(data_path)
        imports = self._native_imports()
        if imports is None:
            raise FileNotFoundError("lerobot")
        LeRobotDataset, np = imports
        dataset = LeRobotDataset(f"local/{dataset_id}", root=dataset_dir, return_uint8=True)
        absolute_index = int(episode.get("datasetFromIndex") or 0) + max(0, int(frame))
        if absolute_index >= len(dataset):
            raise FileNotFoundError(str(frame))
        item = dataset[absolute_index]
        image = item.get(CAMERA_FEATURE_KEYS[camera])
        return self._encode_rgb_tensor_to_jpeg(image, np)

    async def _record_loop(self) -> None:
        # 录制循环按目标 FPS 调度；慢帧只记质量指标，不中断本轮采集。
        period_s = 1.0 / max(1, self._record_fps_hz)
        next_tick = time.monotonic()
        while self._session_active:
            try:
                if not self._recording:
                    await asyncio.sleep(0.05)
                    next_tick = time.monotonic()
                    continue
                now = time.monotonic()
                if now > next_tick + period_s:
                    async with self._lock:
                        self._episode_late_frames += 1
                frame = await self._collect_frame()
                async with self._lock:
                    self._write_frame_locked(frame)
                next_tick += period_s
                await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logs.error("[LEROBOT]", f"record loop recovered: {exc}")
                await asyncio.sleep(0.2)

    async def _collect_frame(self) -> dict[str, Any]:
        config = self.settings.get_config()
        motion_positions = list(self.telemetry.motion_positions)
        motion_pulses = [0.0] * 12
        try:
            # 录制优先读取 HAL 的即时位置，遥测缓存只作为短暂不可用时的兜底。
            motion_state = await self.hal.motion_state()
            raw_positions = motion_state.get("positions")
            if isinstance(raw_positions, list) and len(raw_positions) == 12:
                motion_positions = [float(value) for value in raw_positions]
            raw_pulses = motion_state.get("pulses")
            if isinstance(raw_pulses, list) and len(raw_pulses) == 12:
                motion_pulses = [float(value) for value in raw_pulses]
        except Exception:
            pass
        motion_positions = self._recording_motion_positions(config, motion_positions, motion_pulses)
        force_left = list(self.telemetry.force_left)
        force_right = list(self.telemetry.force_right)
        force_left_window = self._force_window_from_latest(force_left)
        force_right_window = self._force_window_from_latest(force_right)
        if self._real_hardware_mode(config):
            # 真机模式用窗口采样保存力传感器短时上下文，方便后续训练识别接触变化。
            force_result = await asyncio.to_thread(
                self.hardware.force.sample_window,
                config,
                self._force_window_samples_per_frame,
            )
            if force_result.ok:
                force_left = [float(value) for value in force_result.left]
                force_right = [float(value) for value in force_result.right]
                force_left_window = self._normalize_force_window(force_result.left_window, force_left)
                force_right_window = self._normalize_force_window(force_result.right_window, force_right)
        image_payload = await self._capture_cameras(config)
        action = self._latest_action_vector()
        return {
            "timestamp": time.time(),
            "observation.state": ui_to_lerobot_state(motion_positions),
            "observation.pulses": motion_pulses,
            "observation.force_left": force_left,
            "observation.force_right": force_right,
            "observation.force_left_window": force_left_window,
            "observation.force_right_window": force_right_window,
            "observation.force_window_dt": self._force_window_offsets(),
            "observation.gripper": list(self.telemetry.gripper_positions),
            "action": action,
            "images": image_payload,
        }

    async def _capture_cameras(self, config: dict[str, Any]) -> dict[str, Any]:
        if self._native_dataset is not None:
            return await self._capture_native_camera_arrays(config)
        if self._dataset_dir is None:
            return {}
        if not self._real_hardware_mode(config):
            return {}
        paths: dict[str, str] = {}
        # 三路相机并发截图，避免串行等待把录制循环拖慢。
        tasks = [
            asyncio.to_thread(self.hardware.cameras.snapshot, config, key)
            for key in CAMERA_KEYS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for key, result in zip(CAMERA_KEYS, results, strict=True):
            if isinstance(result, BaseException):
                self._camera_drops[key] = self._camera_drops.get(key, 0) + 1
                continue
            relative = (
                Path("videos")
                / "chunk-000"
                / CAMERA_FEATURE_KEYS[key]
                / f"episode_{self._episode_index:06d}"
                / f"frame_{self._episode_frames:06d}.jpg"
            )
            target = self._dataset_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, result)
            self._current_episode_paths.append(target)
            paths[CAMERA_FEATURE_KEYS[key]] = relative.as_posix()
        return paths

    def _write_frame_locked(self, frame: dict[str, Any]) -> None:
        if not self._recording:
            return
        if self._native_dataset is not None:
            self._write_native_frame_locked(frame)
            return
        if self._writer is None:
            return
        state = frame["observation.state"]
        pulses = frame["observation.pulses"]
        force_left = frame["observation.force_left"]
        force_right = frame["observation.force_right"]
        record = {
            "index": self._episode_frames,
            "episode_index": self._episode_index,
            "frame_index": self._episode_frames,
            "timestamp": frame["timestamp"],
            "task": self._task,
            "observation.state": state,
            "observation.pulses": pulses,
            "observation.force_left": force_left,
            "observation.force_right": force_right,
            "observation.force_left_window": frame["observation.force_left_window"],
            "observation.force_right_window": frame["observation.force_right_window"],
            "observation.force_window_dt": frame["observation.force_window_dt"],
            "observation.gripper": frame["observation.gripper"],
            "action": frame["action"],
            "next.done": False,
            "images": frame["images"],
        }
        self._writer.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._writer.flush()
        self._episode_frames += 1
        self.telemetry.frame_count = self._episode_frames
        self._max_force_left = max(self._max_force_left, self._force_norm(force_left))
        self._max_force_right = max(self._max_force_right, self._force_norm(force_right))

    def _write_native_frame_locked(self, frame: dict[str, Any]) -> None:
        if self._native_dataset is None:
            return
        native_frame = {
            "observation.state": self._np_float32(frame["observation.state"]),
            "observation.pulses": self._np_float32(frame["observation.pulses"]),
            "observation.force_left": self._np_float32(frame["observation.force_left"]),
            "observation.force_right": self._np_float32(frame["observation.force_right"]),
            "observation.gripper": self._np_float32(frame["observation.gripper"]),
            "action": self._np_float32(frame["action"]),
            "task": self._task,
        }
        if self._force_window_enabled:
            native_frame["observation.force_left_window"] = self._np_float32(
                frame["observation.force_left_window"]
            )
            native_frame["observation.force_right_window"] = self._np_float32(
                frame["observation.force_right_window"]
            )
            native_frame["observation.force_window_dt"] = self._np_float32(frame["observation.force_window_dt"])
        images = frame.get("images", {})
        if isinstance(images, dict):
            for feature_key in CAMERA_FEATURE_KEYS.values():
                image = images.get(feature_key)
                if image is None:
                    image = self._synthetic_camera_frame(feature_key)
                native_frame[feature_key] = image
        self._native_dataset.add_frame(native_frame)
        self._episode_frames += 1
        self.telemetry.frame_count = self._episode_frames
        self._max_force_left = max(self._max_force_left, self._force_norm(frame["observation.force_left"]))
        self._max_force_right = max(self._max_force_right, self._force_norm(frame["observation.force_right"]))

    def _recording_motion_positions(
        self,
        config: dict[str, Any],
        positions: list[float],
        pulses: list[float],
    ) -> list[float]:
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
        key = "leftPulse" if side == "left" else "rightPulse"
        raw = origin.get(key)
        if not isinstance(raw, list) or len(raw) < 6:
            return None
        try:
            return [float(value) for value in raw[:6]]
        except (TypeError, ValueError):
            return None

    def _begin_episode_locked(self) -> None:
        dataset_dir = self._require_dataset_dir()
        self._close_writer_locked()
        # 每个 episode 独立统计质量指标，保存/丢弃时可以精确回滚。
        self._episode_started_at = time.monotonic()
        self._episode_frames = 0
        self._episode_late_frames = 0
        self._camera_drops = {key: 0 for key in CAMERA_KEYS}
        self._max_force_left = 0.0
        self._max_force_right = 0.0
        self._current_episode_paths = []
        if self._native_dataset is not None:
            self._native_dataset_from_index = self._native_total_frames()
            self._current_data_path = None
            self._recording = True
            return
        data_path = Path("data") / "chunk-000" / f"episode_{self._episode_index:06d}.jsonl"
        absolute_data_path = dataset_dir / data_path
        absolute_data_path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = absolute_data_path.open("w", encoding="utf-8")
        self._current_data_path = absolute_data_path
        self._current_episode_paths.append(absolute_data_path)
        self._recording = True

    def _finalize_episode_locked(self, *, status: str, deleted: bool) -> dict[str, Any]:
        dataset_dir = self._require_dataset_dir()
        data_path = self._current_data_path
        duration_s = max(0.0, time.monotonic() - self._episode_started_at)
        self._close_writer_locked()
        native = self._native_dataset is not None
        if native:
            self._save_native_episode_locked()
        episode_id = f"episode_{self._episode_index:06d}"
        # 统一写入 AppStation 自己的 episode 索引，兼容原生 LeRobot 和 JSONL fallback。
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
            "dataPath": self._relative_to_dataset(dataset_dir, data_path) if data_path is not None else "",
            "datasetFromIndex": self._native_dataset_from_index if native else None,
            "datasetToIndex": self._native_dataset_from_index + self._episode_frames if native else None,
            "native": native,
            "lateFrames": self._episode_late_frames,
            "cameraDrops": dict(self._camera_drops),
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
            info["updatedAt"] = now_ms()
            if native:
                self._write_appstation_info(dataset_dir, self.settings.get_config())
            else:
                self._write_json(info_path, info)
        self._episode_index += 1
        return episode

    def _remove_saved_episode_locked(self, episode: dict[str, Any]) -> None:
        dataset_dir = self._require_dataset_dir()
        episode_id = str(episode.get("id", ""))
        self._episode_index = int(episode.get("episodeIndex", self._episode_index))
        episodes = [item for item in self._read_episodes(dataset_dir) if str(item.get("id", "")) != episode_id]
        self._write_episodes(dataset_dir, episodes)
        self._remove_episode_artifacts_locked(self._episode_index)

    def _mark_saved_episode_deleted_locked(self, episode: dict[str, Any]) -> None:
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

    def _remove_episode_artifacts_locked(self, episode_index: int) -> None:
        dataset_dir = self._require_dataset_dir().resolve()
        data_path = dataset_dir / "data" / "chunk-000" / f"episode_{episode_index:06d}.jsonl"
        self._safe_unlink(dataset_dir, data_path)
        for feature_key in CAMERA_FEATURE_KEYS.values():
            image_dir = dataset_dir / "videos" / "chunk-000" / feature_key / f"episode_{episode_index:06d}"
            self._safe_rmtree(dataset_dir, image_dir)

    def _discard_current_episode_files_locked(self) -> None:
        if self._native_dataset is not None:
            try:
                self._native_dataset.clear_episode_buffer()
            except Exception as exc:  # noqa: BLE001
                self._native_error = str(exc)
            return
        dataset_dir = self._require_dataset_dir().resolve()
        for path in self._current_episode_paths:
            try:
                target = path.resolve()
                target.relative_to(dataset_dir)
            except ValueError:
                continue
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                pass
        self._current_episode_paths = []

    def _safe_unlink(self, root: Path, path: Path) -> None:
        try:
            target = path.resolve()
            target.relative_to(root)
        except ValueError:
            return
        try:
            if target.exists() and target.is_file():
                target.unlink()
        except OSError:
            pass

    def _safe_rmtree(self, root: Path, path: Path) -> None:
        try:
            target = path.resolve()
            target.relative_to(root)
        except ValueError:
            return
        try:
            if target.exists() and target.is_dir():
                shutil.rmtree(target)
        except OSError:
            pass

    def _close_writer_locked(self) -> None:
        if self._writer is None:
            return
        try:
            self._writer.close()
        finally:
            self._writer = None

    def _write_info(self, dataset_dir: Path, config: dict[str, Any]) -> None:
        info_path = dataset_dir / "meta" / "info.json"
        info = self._read_json(info_path)
        created_at = int(info.get("createdAt", now_ms())) if info else now_ms()
        payload = {
            "name": self._dataset_name,
            "status": str(info.get("status", "local")) if info else "local",
            "robot_type": "dual_arm_micro_assembly",
            "fps": self._record_fps_from_config(config),
            "format": "lerobot-compatible-jsonl-fallback",
            "nativeLeRobotAvailable": importlib.util.find_spec("lerobot") is not None,
            "screenCaptureAvailable": self._screen_capture_available(),
            "createdAt": created_at,
            "updatedAt": now_ms(),
            "features": self._features(),
            "units": {
                "observation.state": "translations um, rotations 0.001 degree",
                "observation.pulses": "raw LTDMC pulse counts from dmc_get_position",
                "frontend.state": "translations um, rotations degree",
                "force": "raw NI-DAQ values; calibration transform pending",
                "observation.force_*_window": "high-rate force samples aligned to each LeRobot frame",
                "observation.force_window_dt": "seconds relative to frame timestamp; last sample is closest to frame",
            },
            "recording": {
                "fps": self._record_fps_from_config(config),
                "forceSampleHz": self._force_sample_hz_from_config(config),
                "forceWindowSamples": self._force_window_samples_from_config(config),
                "alignment": (
                    "LeRobot frame timestamp is frame_index/fps; "
                    "force windows store per-frame high-rate history"
                ),
            },
            "hardware": {
                "cameras": config.get("cameras", {}),
                "force": {
                    "leftIp": config.get("force", {}).get("leftIp"),
                    "rightIp": config.get("force", {}).get("rightIp"),
                    "sampleHz": config.get("force", {}).get("sampleHz"),
                },
            },
        }
        self._write_json(info_path, payload)

    def _write_appstation_info(self, dataset_dir: Path, config: dict[str, Any]) -> None:
        path = dataset_dir / "meta" / "appstation_info.json"
        info = self._read_json(path)
        payload = {
            "name": str(info.get("name") or self._dataset_name),
            "status": str(info.get("status", "local")) if info else "local",
            "format": "lerobot-v3-native",
            "nativeLeRobotAvailable": True,
            "useVideos": self._native_use_videos,
            "vcodec": self._native_vcodec(),
            "recording": {
                "fps": self._record_fps_hz,
                "forceSampleHz": self._force_sample_hz,
                "forceWindowSamples": self._force_window_samples_per_frame,
                "alignment": (
                    "state/images/scalar force are sampled once per frame; "
                    "force windows preserve high-rate samples"
                ),
            },
            "createdAt": int(info.get("createdAt", now_ms())) if info else now_ms(),
            "updatedAt": now_ms(),
            "hardware": {
                "cameras": config.get("cameras", {}),
                "force": {
                    "leftIp": config.get("force", {}).get("leftIp"),
                    "rightIp": config.get("force", {}).get("rightIp"),
                    "sampleHz": config.get("force", {}).get("sampleHz"),
                },
            },
        }
        self._write_json(path, payload)

    def _features(self) -> dict[str, Any]:
        image_features = {}
        config = self.settings.get_config()
        for key in CAMERA_KEYS:
            width, height = self._camera_size(config, key)
            image_features[CAMERA_FEATURE_KEYS[key]] = {
                "dtype": "image",
                "shape": [3, height, width],
                "names": ["channels", "height", "width"],
                "encoding": "jpeg_sequence",
            }
        return {
            "observation.state": {"dtype": "float32", "shape": [12], "names": list(STATE_FEATURE_NAMES)},
            "observation.pulses": {"dtype": "float32", "shape": [12], "names": list(PULSE_FEATURE_NAMES)},
            "action": {"dtype": "float32", "shape": [12], "names": list(ACTION_FEATURE_NAMES)},
            "observation.force_left": {"dtype": "float32", "shape": [6], "names": list(FORCE_FEATURE_NAMES)},
            "observation.force_right": {"dtype": "float32", "shape": [6], "names": list(FORCE_FEATURE_NAMES)},
            "observation.force_left_window": {
                "dtype": "float32",
                "shape": [self._force_window_samples_from_config(self.settings.get_config()), 6],
                "names": ["sample", list(FORCE_FEATURE_NAMES)],
            },
            "observation.force_right_window": {
                "dtype": "float32",
                "shape": [self._force_window_samples_from_config(self.settings.get_config()), 6],
                "names": ["sample", list(FORCE_FEATURE_NAMES)],
            },
            "observation.force_window_dt": {
                "dtype": "float32",
                "shape": [self._force_window_samples_from_config(self.settings.get_config())],
                "names": ["sample_offset_s"],
            },
            "observation.gripper": {"dtype": "float32", "shape": [2], "names": list(GRIPPER_FEATURE_NAMES)},
            **image_features,
        }

    def _create_native_dataset_metadata(self, dataset_dir: Path, config: dict[str, Any]) -> bool:
        if not self._native_recording_requested():
            return False
        imports = self._native_imports()
        if imports is None:
            return False
        if dataset_dir.exists() and any(dataset_dir.iterdir()):
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

    def _try_begin_native_dataset_locked(self, config: dict[str, Any]) -> bool:
        # 原生 LeRobot 可用时优先使用；失败时调用方会退回 JSONL/JPEG 兼容格式。
        if not self._native_recording_requested():
            self._native_error = "native LeRobot disabled by APPSTATION_LEROBOT_NATIVE"
            return False
        imports = self._native_imports()
        if imports is None:
            self._native_error = "lerobot[dataset] is not installed in backend runtime"
            return False
        if self._dataset_dir is None:
            return False
        LeRobotDataset, _np = imports
        dataset_dir = self._dataset_dir
        repo_id = f"local/{self._dataset_id}"
        self._native_use_videos = self._native_use_videos_requested()
        try:
            if self._is_native_dataset_info(self._read_json(dataset_dir / "meta" / "info.json")):
                if self._native_dataset_is_empty(dataset_dir):
                    # 空的原生目录可能来自上次初始化失败，重建可以修复损坏的 meta。
                    app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
                    shutil.rmtree(dataset_dir)
                    self._native_dataset = LeRobotDataset.create(
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
                    # 已有有效原生数据集时继续追加，保留历史 episode。
                    self._native_dataset = LeRobotDataset.resume(
                        repo_id=repo_id,
                        root=dataset_dir,
                        batch_encoding_size=1,
                        vcodec=self._native_vcodec(),
                    )
                    self._sync_recording_shape_from_native_info(dataset_dir)
            elif dataset_dir.exists() and any(dataset_dir.iterdir()):
                self._native_error = "dataset directory already contains non-native files"
                return False
            else:
                if dataset_dir.exists():
                    dataset_dir.rmdir()
                self._native_dataset = LeRobotDataset.create(
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
            return True
        except Exception as exc:  # noqa: BLE001
            self._native_dataset = None
            self._native_error = str(exc)
            return False

    def _native_dataset_is_empty(self, dataset_dir: Path) -> bool:
        data_dir = dataset_dir / "data"
        episode_meta_dir = dataset_dir / "meta" / "episodes"
        return (
            not any(data_dir.glob("chunk-*/*.parquet"))
            and not any(episode_meta_dir.glob("chunk-*/*.parquet"))
            and not self._read_episodes(dataset_dir)
        )

    def _native_features(self, config: dict[str, Any]) -> dict[str, Any]:
        # feature shape 是训练/回放的契约；这里集中生成，避免录制路径出现字段漂移。
        image_dtype = "video" if self._native_use_videos else "image"
        features: dict[str, Any] = {
            "observation.state": {"dtype": "float32", "shape": (12,), "names": list(STATE_FEATURE_NAMES)},
            "observation.pulses": {"dtype": "float32", "shape": (12,), "names": list(PULSE_FEATURE_NAMES)},
            "observation.force_left": {"dtype": "float32", "shape": (6,), "names": list(FORCE_FEATURE_NAMES)},
            "observation.force_right": {"dtype": "float32", "shape": (6,), "names": list(FORCE_FEATURE_NAMES)},
            "observation.force_left_window": {
                "dtype": "float32",
                "shape": (self._force_window_samples_per_frame, 6),
                "names": ["sample", list(FORCE_FEATURE_NAMES)],
            },
            "observation.force_right_window": {
                "dtype": "float32",
                "shape": (self._force_window_samples_per_frame, 6),
                "names": ["sample", list(FORCE_FEATURE_NAMES)],
            },
            "observation.force_window_dt": {
                "dtype": "float32",
                "shape": (self._force_window_samples_per_frame,),
                "names": ["sample_offset_s"],
            },
            "observation.gripper": {"dtype": "float32", "shape": (2,), "names": list(GRIPPER_FEATURE_NAMES)},
            "action": {"dtype": "float32", "shape": (12,), "names": list(ACTION_FEATURE_NAMES)},
        }
        for key, feature_key in CAMERA_FEATURE_KEYS.items():
            width, height = self._camera_size(config, key)
            features[feature_key] = {
                "dtype": image_dtype,
                "shape": (3, height, width),
                "names": ["channels", "height", "width"],
            }
        return features

    def _native_imports(self) -> tuple[Any, Any] | None:
        try:
            module = importlib.import_module("lerobot.datasets.lerobot_dataset")
            np = importlib.import_module("numpy")
        except Exception:
            return None
        return module.LeRobotDataset, np

    def _native_recording_requested(self) -> bool:
        value = os.environ.get("APPSTATION_LEROBOT_NATIVE", "auto").strip().lower()
        return value not in {"0", "false", "off", "no"}

    def _native_use_videos_requested(self) -> bool:
        value = os.environ.get("APPSTATION_LEROBOT_USE_VIDEOS", "1").strip().lower()
        return value not in {"0", "false", "off", "no"}

    def _native_vcodec(self) -> str:
        return os.environ.get("APPSTATION_LEROBOT_VCODEC", "h264").strip() or "h264"

    def _native_total_frames(self) -> int:
        if self._native_dataset is None:
            return 0
        meta = getattr(self._native_dataset, "meta", None)
        total = getattr(meta, "total_frames", None)
        if total is None:
            total = getattr(self._native_dataset, "num_frames", 0)
        if total is None:
            return 0
        try:
            return int(total)
        except (TypeError, ValueError):
            return 0

    def _save_native_episode_locked(self) -> None:
        if self._native_dataset is None:
            return
        try:
            self._native_dataset.save_episode(parallel_encoding=False)
            self._native_dataset.finalize()
            self._native_dataset = self._resume_native_dataset_locked()
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)
            raise RuntimeError(f"native LeRobot save_episode failed: {exc}") from exc

    def _resume_native_dataset_locked(self) -> Any:
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

    def _finalize_native_dataset(self) -> None:
        dataset = self._native_dataset
        self._native_dataset = None
        if dataset is None:
            return
        try:
            dataset.finalize()
        except Exception as exc:  # noqa: BLE001
            self._native_error = str(exc)

    def _is_native_dataset_info(self, info: dict[str, Any]) -> bool:
        return str(info.get("codebase_version", "")).startswith("v3.")

    async def _capture_native_camera_arrays(self, config: dict[str, Any]) -> dict[str, Any]:
        real_mode = self._real_hardware_mode(config)
        if not real_mode:
            return {
                feature_key: self._synthetic_camera_frame(feature_key)
                for feature_key in CAMERA_FEATURE_KEYS.values()
            }
        tasks = [asyncio.to_thread(self.hardware.cameras.snapshot, config, key) for key in CAMERA_KEYS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        images: dict[str, Any] = {}
        for key, result in zip(CAMERA_KEYS, results, strict=True):
            feature_key = CAMERA_FEATURE_KEYS[key]
            if isinstance(result, BaseException):
                self._camera_drops[key] = self._camera_drops.get(key, 0) + 1
                images[feature_key] = self._synthetic_camera_frame(feature_key)
                continue
            try:
                images[feature_key] = self._decode_jpeg_to_rgb(result, config, key)
            except Exception:  # noqa: BLE001
                self._camera_drops[key] = self._camera_drops.get(key, 0) + 1
                images[feature_key] = self._synthetic_camera_frame(feature_key)
        return images

    def _decode_jpeg_to_rgb(self, jpeg: bytes, config: dict[str, Any], camera: str = "global") -> Any:
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
        width, height = self._camera_size(config, camera)
        if rgb.shape[:2] != (height, width):
            rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        return rgb

    def _synthetic_camera_frame(self, feature_key: str) -> Any:
        imports = self._native_imports()
        if imports is None:
            return None
        _LeRobotDataset, np = imports
        camera = next((key for key, value in CAMERA_FEATURE_KEYS.items() if value == feature_key), "global")
        width, height = self._camera_size(self.settings.get_config(), camera)
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

    def _np_float32(self, values: object) -> Any:
        imports = self._native_imports()
        if imports is None:
            return values
        _LeRobotDataset, np = imports
        return np.asarray(values, dtype=np.float32)

    def _record_fps_from_config(self, config: dict[str, Any]) -> int:
        try:
            raw = float(config.get("storage", {}).get("recordFps") or config.get("cameras", {}).get("fps", 30))
        except (TypeError, ValueError):
            raw = 30.0
        return int(min(max(round(raw), 1), 60))

    def _force_sample_hz_from_config(self, config: dict[str, Any]) -> float:
        try:
            raw = float(config.get("force", {}).get("sampleHz", 200))
        except (TypeError, ValueError):
            raw = 200.0
        return min(max(raw, 1.0), 10000.0)

    def _force_window_samples_from_config(self, config: dict[str, Any]) -> int:
        force_config = config.get("force", {})
        try:
            override = int(force_config.get("recordWindowSamples", 0))
        except (TypeError, ValueError):
            override = 0
        if override > 0:
            return min(max(override, 1), 512)
        sample_hz = self._force_sample_hz_from_config(config)
        fps = self._record_fps_from_config(config)
        return min(max(int(math.ceil(sample_hz / max(1, fps))), 1), 512)

    def _force_window_from_latest(self, values: list[float]) -> list[list[float]]:
        row = (list(values) + [0.0] * 6)[:6]
        return [list(row) for _ in range(self._force_window_samples_per_frame)]

    def _normalize_force_window(self, window: object, latest: list[float]) -> list[list[float]]:
        if not isinstance(window, list):
            return self._force_window_from_latest(latest)
        rows: list[list[float]] = []
        for item in window:
            if not isinstance(item, list):
                continue
            rows.append(([float(value) for value in item] + [0.0] * 6)[:6])
        if not rows:
            return self._force_window_from_latest(latest)
        target = self._force_window_samples_per_frame
        if len(rows) < target:
            rows = [list(rows[0]) for _ in range(target - len(rows))] + rows
        return rows[-target:]

    def _force_window_offsets(self) -> list[float]:
        sample_hz = max(self._force_sample_hz, 1.0)
        count = self._force_window_samples_per_frame
        return [round((index - count + 1) / sample_hz, 9) for index in range(count)]

    def _sync_recording_shape_from_native_info(self, dataset_dir: Path) -> None:
        info = self._read_json(dataset_dir / "meta" / "info.json")
        try:
            self._record_fps_hz = int(info.get("fps", self._record_fps_hz))
        except (TypeError, ValueError):
            pass
        features = info.get("features", {})
        if not isinstance(features, dict):
            return
        force_window = features.get("observation.force_left_window")
        if not isinstance(force_window, dict):
            self._force_window_enabled = False
            return
        shape = force_window.get("shape", [])
        if isinstance(shape, list) and shape:
            try:
                self._force_window_samples_per_frame = min(max(int(shape[0]), 1), 512)
                self._force_window_enabled = True
            except (TypeError, ValueError):
                pass

    def _camera_size(self, config: dict[str, Any], camera: str = "global") -> tuple[int, int]:
        cameras = config.get("cameras", {})
        key = f"{camera}Resolution"
        if camera == "wrist_left":
            key = "wristLeftResolution"
        elif camera == "wrist_right":
            key = "wristRightResolution"
        fallback = CAMERA_CAPTURE_SIZES.get(camera, CAMERA_CAPTURE_SIZES["global"])
        raw = str(cameras.get(key, "native")).lower().replace(" ", "")
        if raw == "native":
            raw = str(cameras.get("previewResolution", "native")).lower().replace(" ", "")
        if raw == "native":
            return fallback
        if "x" not in raw:
            return fallback
        width_raw, height_raw = raw.split("x", 1)
        try:
            width = min(max(int(width_raw), 160), 4096)
            height = min(max(int(height_raw), 120), 2160)
        except ValueError:
            return fallback
        return width, height

    def _episode_for_api(self, dataset_dir: Path, dataset_id: str, episode: dict[str, Any]) -> dict[str, Any]:
        samples = self._episode_samples(dataset_dir, dataset_id, episode)
        quality = self._episode_quality(episode)
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
            "maxForceLeft": float(episode.get("maxForceLeft", 0.0)),
            "maxForceRight": float(episode.get("maxForceRight", 0.0)),
        }

    def _episode_samples(
        self,
        dataset_dir: Path,
        dataset_id: str,
        episode: dict[str, Any],
        max_samples: int = 300,
    ) -> list[dict[str, Any]]:
        native_episode = bool(episode.get("native", False))
        native_dataset = self._is_native_dataset_info(self._read_json(dataset_dir / "meta" / "info.json"))
        if native_episode or native_dataset:
            return self._native_episode_samples(dataset_dir, dataset_id, episode, max_samples)
        data_path = str(episode.get("dataPath", ""))
        if not data_path:
            return []
        path = dataset_dir / data_path
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        stride = max(1, len(lines) // max_samples)
        samples: list[dict[str, Any]] = []
        for line in lines[::stride]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            state_raw = item.get("observation.state")
            state = (
                lerobot_to_ui_state([float(value) for value in state_raw])
                if isinstance(state_raw, list)
                else [0.0] * 12
            )
            pulses_raw = item.get("observation.pulses")
            pulses = [float(value) for value in pulses_raw] if isinstance(pulses_raw, list) else [0.0] * 12
            images_raw = item.get("images")
            images: dict[str, str] = {}
            if isinstance(images_raw, dict):
                for key, feature_key in CAMERA_FEATURE_KEYS.items():
                    raw_path = images_raw.get(feature_key)
                    if isinstance(raw_path, str):
                        images[key] = f"/api/datasets/{dataset_id}/file?path={raw_path}"
            samples.append(
                {
                    "frame": int(item.get("frame_index", len(samples))),
                    "leftJoints": state[:6],
                    "rightJoints": state[6:12],
                    "leftPulses": pulses[:6],
                    "rightPulses": pulses[6:12],
                    "forceLeft": item.get("observation.force_left", [0.0] * 6),
                    "forceRight": item.get("observation.force_right", [0.0] * 6),
                    "images": images,
                }
            )
        return samples

    def _native_episode_samples(
        self,
        dataset_dir: Path,
        dataset_id: str,
        episode: dict[str, Any],
        max_samples: int,
    ) -> list[dict[str, Any]]:
        imports = self._native_imports()
        if imports is None:
            return []
        LeRobotDataset, _np = imports
        try:
            dataset = LeRobotDataset(f"local/{dataset_id}", root=dataset_dir, return_uint8=True)
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
            state = self._tensor_to_float_list(item.get("observation.state"), expected=12)
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
                    "leftJoints": lerobot_to_ui_state(state)[:6],
                    "rightJoints": lerobot_to_ui_state(state)[6:12],
                    "leftPulses": pulses[:6],
                    "rightPulses": pulses[6:12],
                    "forceLeft": force_left,
                    "forceRight": force_right,
                    "images": images,
                }
            )
        return samples

    def _tensor_to_float_list(self, value: Any, *, expected: int) -> list[float]:
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
        if not self._is_native_dataset_info(info):
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
                        "maxForceLeft": 0.0,
                        "maxForceRight": 0.0,
                        "warnings": [],
                    }
                )
        return episodes

    def _visible_episodes_for_dataset(self, dataset_dir: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
        episodes = self._read_episodes(dataset_dir)
        if not episodes and self._is_native_dataset_info(info):
            episodes = self._native_episodes_from_meta(dataset_dir, info)
        return [episode for episode in episodes if not bool(episode.get("deleted", False))]

    def _positive_ratio(self, value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(parsed, 0.0)

    def _encode_rgb_tensor_to_jpeg(self, image: Any, np: Any) -> bytes:
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

    def _episode_quality(self, episode: dict[str, Any]) -> int:
        late = int(episode.get("lateFrames", 0))
        drops_raw = episode.get("cameraDrops", {})
        drops = sum(int(value) for value in drops_raw.values()) if isinstance(drops_raw, dict) else 0
        return max(40, min(99, 96 - late * 2 - drops * 3))

    def _quality_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self._episode_late_frames:
            warnings.append(f"late frames: {self._episode_late_frames}")
        for key, count in self._camera_drops.items():
            if count:
                warnings.append(f"{key} camera drops: {count}")
        return warnings

    def _latest_action_vector(self) -> list[float]:
        vector = [0.0] * 12
        last_action = self.teleop.status().get("lastAction")
        if not isinstance(last_action, dict):
            return vector
        ts = int(last_action.get("ts", 0))
        if now_ms() - ts > 1000:
            return vector
        side = last_action.get("side")
        axis = last_action.get("axis")
        if side not in {"left", "right"} or axis not in {"X", "Y", "Z", "Roll", "Pitch", "Yaw"}:
            return vector
        axis_index = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"].index(str(axis))
        state_index = (0 if side == "left" else 6) + axis_index
        value = float(last_action.get("delta", 0.0))
        vector[state_index] = value * 1000.0 if axis_index >= 3 else value
        return vector

    def _dataset_root(self, config: dict[str, Any]) -> Path:
        raw = str(config.get("storage", {}).get("datasetRoot", "~/.appstation/datasets"))
        return Path(raw).expanduser()

    def _dataset_path(self, dataset_id: str) -> Path:
        root = self._dataset_root(self.settings.get_config())
        return root / self._safe_id(dataset_id)

    def _require_dataset_dir(self) -> Path:
        if self._dataset_dir is None:
            raise RuntimeError("record dataset is not initialized")
        return self._dataset_dir

    def _next_episode_index(self, dataset_dir: Path) -> int:
        episodes = self._read_episodes(dataset_dir)
        if not episodes:
            episodes = self._native_episodes_from_meta(dataset_dir, self._read_json(dataset_dir / "meta" / "info.json"))
        indices = [int(item.get("episodeIndex", -1)) for item in episodes]
        return max(indices, default=-1) + 1

    def _read_episodes(self, dataset_dir: Path) -> list[dict[str, Any]]:
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
        path = dataset_dir / "meta" / "episodes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in episodes)
        path.write_text(content, encoding="utf-8")

    def _episode_dataset_stub(self, dataset_dir: Path) -> dict[str, Any]:
        info = self._read_json(dataset_dir / "meta" / "info.json")
        app_info = self._read_json(dataset_dir / "meta" / "appstation_info.json")
        name = app_info.get("name") or info.get("name") or dataset_dir.name
        return {"id": dataset_dir.name, "name": str(name)}

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_id(self, value: str) -> str:
        normalized = SAFE_ID.sub("_", value.strip()).strip("._-")
        return normalized[:80] or "dataset"

    def _relative_to_dataset(self, dataset_dir: Path, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return path.relative_to(dataset_dir).as_posix()
        except ValueError:
            return path.name

    def _real_hardware_mode(self, config: dict[str, Any]) -> bool:
        mode = os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")
        return str(mode).lower() == "real"

    def _screen_capture_available(self) -> bool:
        return importlib.util.find_spec("mss") is not None or importlib.util.find_spec("PIL") is not None

    def _force_norm(self, values: object) -> float:
        if not isinstance(values, list) or len(values) < 3:
            return 0.0
        return max(abs(float(values[0])), abs(float(values[1])), abs(float(values[2])))
