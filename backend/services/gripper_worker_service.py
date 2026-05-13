from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from typing import Any
from uuid import uuid4

from backend.core.config import SettingsService
from backend.core.logging import LogService
from backend.drivers.gripper_rs485 import GripperResult
from backend.workers.gripper_worker import run_gripper_worker


@dataclass
class _WorkerHandle:
    side: str
    signature: tuple[Any, ...]
    process: BaseProcess
    command_queue: Queue
    status_queue: Queue
    response_queue: Queue


class GripperWorkerService:
    """Owns one isolated process per Jodell COM port."""

    def __init__(
        self,
        settings: SettingsService,
        logs: LogService,
        *,
        context: BaseContext | None = None,
    ) -> None:
        self.settings = settings
        self.logs = logs
        self._ctx = context or get_context("spawn")
        self._handles: dict[str, _WorkerHandle] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def is_enabled(self, config: dict[str, Any] | None = None) -> bool:
        active = config if config is not None else self.settings.get_config()
        return str(active.get("gripper", {}).get("sampleMode", "direct")).lower() == "dual_worker"

    def sync_config(self, config: dict[str, Any]) -> None:
        if not self.is_enabled(config):
            self.stop_all()

    def positions(self, config: dict[str, Any]) -> list[float | None]:
        samples = self.samples(config)
        return [
            self._sample_position(samples.get("left")),
            self._sample_position(samples.get("right")),
        ]

    def samples(self, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """返回左右 worker 的最新样本，不等待新的串口读取。"""
        if not self.is_enabled(config):
            return {}
        with self._lock:
            self._ensure_workers(config)
            self._drain_status()
            stale_ms = float(config.get("gripper", {}).get("sampleStaleMs", 500))
            result: dict[str, dict[str, Any]] = {}
            for side, sample in self._latest.items():
                # 调用方需要区分“读到旧值”和“读不到值”，因此在缓存层统一标记过期状态。
                item = dict(sample)
                age_ms = self._sample_age_ms(item)
                item["ageMs"] = age_ms
                item["stale"] = age_ms > stale_ms
                result[side] = item
            return result

    def position(self, config: dict[str, Any], side: str, timeout_sec: float = 0.25) -> GripperResult:
        if not self.is_enabled(config):
            return GripperResult(False, "gripper workers disabled")
        deadline = time.monotonic() + timeout_sec
        with self._lock:
            self._ensure_worker(side, config)
            while True:
                self._drain_status()
                sample = self._latest.get(side)
                if sample and sample.get("positionMm") is not None:
                    age_ms = self._sample_age_ms(sample)
                    stale_ms = float(config.get("gripper", {}).get("sampleStaleMs", 500))
                    ok = bool(sample.get("ok")) and age_ms <= stale_ms
                    details = dict(sample)
                    details["ageMs"] = age_ms
                    return GripperResult(ok, str(sample.get("message", "")), float(sample["positionMm"]), details)
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            return GripperResult(False, f"{side} gripper worker has no position sample")

    def command(self, config: dict[str, Any], side: str, command: str, target_mm: float | None) -> GripperResult:
        if not self.is_enabled(config):
            return GripperResult(False, "gripper workers disabled", target_mm)
        if command not in {"enable", "disable", "stop"} and not bool(
            config.get("gripper", {}).get(f"{side}Enabled", False)
        ):
            return GripperResult(False, f"{side} gripper is disabled; enable it before motion commands", target_mm)
        timeout_sec = float(config.get("gripper", {}).get("workerCommandTimeoutSec", 2.0))
        request_id = uuid4().hex
        with self._lock:
            handle = self._ensure_worker(side, config)
            self._drain_status()
            handle.command_queue.put(
                {
                    "type": "command",
                    "id": request_id,
                    "command": command,
                    "targetMm": target_mm,
                }
            )
            deadline = time.monotonic() + timeout_sec
            while True:
                self._drain_status()
                try:
                    response = handle.response_queue.get(timeout=0.01)
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        return GripperResult(False, f"{side} gripper worker command timeout", target_mm)
                    continue
                if not isinstance(response, dict) or response.get("id") != request_id:
                    continue
                details = dict(response)
                position_mm = response.get("positionMm")
                return GripperResult(
                    bool(response.get("ok")),
                    str(response.get("message", "")),
                    float(position_mm) if position_mm is not None else target_mm,
                    details,
                )

    def status(self, config: dict[str, Any]) -> dict[str, Any]:
        if not self.is_enabled(config):
            return {"ok": False, "message": "gripper workers disabled", "sides": {}}
        with self._lock:
            self._ensure_workers(config)
            self._drain_status()
            sides: dict[str, Any] = {}
            for side in ("left", "right"):
                handle = self._handles.get(side)
                sample = self._latest.get(side, {})
                age_ms = self._sample_age_ms(sample) if sample else None
                sides[side] = {
                    "running": bool(handle and handle.process.is_alive()),
                    "pid": handle.process.pid if handle else None,
                    "ok": bool(sample.get("ok", False)),
                    "positionMm": sample.get("positionMm"),
                    "sampleHz": sample.get("sampleHz", 0.0),
                    "ageMs": age_ms,
                    "message": sample.get("message", "no sample yet"),
                }
            ok = all(bool(item["running"]) and bool(item["ok"]) for item in sides.values())
            return {"ok": ok, "message": "dual gripper workers", "sides": sides}

    def stop_all(self, timeout_sec: float = 1.0) -> None:
        with self._lock:
            for side in list(self._handles):
                self._stop_worker(side, timeout_sec=timeout_sec)

    def _ensure_workers(self, config: dict[str, Any]) -> None:
        self._ensure_worker("left", config)
        self._ensure_worker("right", config)

    def _ensure_worker(self, side: str, config: dict[str, Any]) -> _WorkerHandle:
        signature = self._signature(side, config)
        handle = self._handles.get(side)
        if handle and handle.signature == signature and handle.process.is_alive():
            return handle
        if handle is not None:
            self._stop_worker(side, timeout_sec=0.5)
        command_queue = self._ctx.Queue()
        status_queue = self._ctx.Queue(maxsize=1)
        response_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=run_gripper_worker,
            args=(side, config, command_queue, status_queue, response_queue, os.getpid()),
            name=f"gripper-{side}-worker",
            daemon=True,
        )
        process.start()
        next_handle = _WorkerHandle(side, signature, process, command_queue, status_queue, response_queue)
        self._handles[side] = next_handle
        self.logs.info("[GRIPPER]", f"{side} gripper worker started pid={process.pid}")
        return next_handle

    def _stop_worker(self, side: str, *, timeout_sec: float) -> None:
        handle = self._handles.pop(side, None)
        if handle is None:
            return
        try:
            handle.command_queue.put({"type": "stop", "id": uuid4().hex})
        except Exception:
            pass
        handle.process.join(timeout_sec)
        if handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout_sec)
        self.logs.info("[GRIPPER]", f"{side} gripper worker stopped")

    def _drain_status(self) -> None:
        for side, handle in list(self._handles.items()):
            while True:
                try:
                    sample = handle.status_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(sample, dict):
                    self._latest[side] = sample

    def _latest_position(self, side: str) -> float | None:
        return self._sample_position(self._latest.get(side))

    def _sample_position(self, sample: dict[str, Any] | None) -> float | None:
        if not sample or not sample.get("ok"):
            return None
        value = sample.get("positionMm")
        return float(value) if value is not None else None

    def _sample_age_ms(self, sample: dict[str, Any]) -> float:
        ts_ms = float(sample.get("tsMs") or 0)
        return max(0.0, time.time() * 1000.0 - ts_ms)

    def _signature(self, side: str, config: dict[str, Any]) -> tuple[Any, ...]:
        gripper = config["gripper"]
        prefix = "left" if side == "left" else "right"
        return (
            side,
            gripper.get(f"{prefix}Port"),
            gripper.get(f"{prefix}SlaveId"),
            gripper.get("baudrate", 115200),
            gripper.get("jodellDllPath", ""),
            gripper.get("sampleHz", 30),
            gripper.get("sampleEnableOnNegative", True),
            gripper.get("strokeMm", 26),
        )
