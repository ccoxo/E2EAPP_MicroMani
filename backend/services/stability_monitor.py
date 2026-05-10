from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.hal_client.client import HalClient
from backend.services.hardware_service import HardwareService


class StabilityMonitorService:
    def __init__(
        self,
        settings: SettingsService,
        hardware: HardwareService,
        hal: HalClient,
        logs: LogService,
    ) -> None:
        self.settings = settings
        self.hardware = hardware
        self.hal = hal
        self.logs = logs
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._status = self._empty_status()
        self._last_hal_connected: bool | None = None
        self._ws_client_count_provider: Callable[[], int] = lambda: 0

    def set_ws_client_count_provider(self, provider: Callable[[], int]) -> None:
        self._ws_client_count_provider = provider

    async def start(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = payload or {}
        duration_s = self._bounded_float(request.get("durationS", 60), 0.1, 86400.0)
        sample_period_s = self._bounded_float(request.get("samplePeriodS", 1.0), 0.05, 30.0)
        include_cameras = bool(request.get("includeCameras", True))
        include_force = bool(request.get("includeForce", True))
        attempt_reconnect = bool(request.get("attemptHalReconnect", True))
        async with self._lock:
            if self._task is not None and not self._task.done():
                raise RuntimeError("stability monitor is already running")
            self._status = self._empty_status()
            self._status.update(
                {
                    "active": True,
                    "runId": f"stability-{now_ms()}",
                    "startedAt": now_ms(),
                    "targetDurationS": duration_s,
                    "samplePeriodS": sample_period_s,
                    "includeCameras": include_cameras,
                    "includeForce": include_force,
                    "attemptHalReconnect": attempt_reconnect,
                }
            )
            self._last_hal_connected = None
            self._task = asyncio.create_task(
                self._run(duration_s, sample_period_s, include_cameras, include_force, attempt_reconnect),
                name="stability-monitor",
            )
        self.logs.info("[BACKEND]", f"stability monitor started for {duration_s:.1f}s")
        return self.status()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            task = self._task
            if task is not None and not task.done():
                task.cancel()
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._status["active"] = False
        self._status["finishedAt"] = now_ms()
        self.logs.warning("[BACKEND]", "stability monitor stopped")
        return self.status()

    def status(self) -> dict[str, Any]:
        status = dict(self._status)
        status["hal"] = dict(self._status.get("hal", {}))
        status["force"] = dict(self._status.get("force", {}))
        status["cameras"] = {
            str(key): dict(value) for key, value in dict(self._status.get("cameras", {})).items()
        }
        status["websocket"] = dict(self._status.get("websocket", {}))
        status["errors"] = list(self._status.get("errors", []))
        return status

    async def _run(
        self,
        duration_s: float,
        sample_period_s: float,
        include_cameras: bool,
        include_force: bool,
        attempt_reconnect: bool,
    ) -> None:
        deadline = time.monotonic() + duration_s
        try:
            while time.monotonic() < deadline:
                sample_started = time.perf_counter()
                await self._sample(include_cameras, include_force, attempt_reconnect)
                loop_ms = (time.perf_counter() - sample_started) * 1000
                self._status["samples"] = int(self._status["samples"]) + 1
                self._status["maxLoopMs"] = max(float(self._status["maxLoopMs"]), round(loop_ms, 3))
                elapsed = time.perf_counter() - sample_started
                await asyncio.sleep(max(0.0, sample_period_s - elapsed))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._append_error(f"stability monitor failed: {exc}")
        finally:
            self._status["active"] = False
            self._status["finishedAt"] = now_ms()

    async def _sample(self, include_cameras: bool, include_force: bool, attempt_reconnect: bool) -> None:
        config = self.settings.get_config()
        hal_health = await self.hal.health()
        hal_status = self._status["hal"]
        hal_status["samples"] = int(hal_status["samples"]) + 1
        if hal_health.connected:
            hal_status["connectedSamples"] = int(hal_status["connectedSamples"]) + 1
        else:
            hal_status["disconnects"] = int(hal_status["disconnects"]) + 1
            message = hal_health.message or "HAL disconnected"
            hal_status["lastError"] = message
            self._append_error(message)
            if attempt_reconnect:
                try:
                    await self.hal.command("hal.reconnect")
                    hal_status["reconnectAttempts"] = int(hal_status["reconnectAttempts"]) + 1
                except Exception as exc:  # noqa: BLE001
                    hal_status["lastReconnectError"] = str(exc)
        if self._last_hal_connected is False and hal_health.connected:
            hal_status["recoveries"] = int(hal_status["recoveries"]) + 1
        self._last_hal_connected = hal_health.connected

        websocket_status = self._status["websocket"]
        websocket_status["clients"] = self._safe_ws_client_count()
        websocket_status["observedSamples"] = int(websocket_status["observedSamples"]) + 1

        if include_force:
            await self._sample_force(config)
        if include_cameras:
            await self._sample_cameras(config)

    async def _sample_force(self, config: dict[str, Any]) -> None:
        force_status = self._status["force"]
        if not self._real_hardware_mode(config):
            force_status["skippedTestMode"] = int(force_status["skippedTestMode"]) + 1
            return
        sample_hz = self._sample_hz(config)
        sample_count = min(max(int(round(sample_hz * 0.1)), 1), 512)
        result = await asyncio.to_thread(self.hardware.force.sample_window, config, sample_count)
        force_status["samples"] = int(force_status["samples"]) + 1
        force_status["sampleHz"] = sample_hz
        force_status["windowSamples"] = sample_count
        if not result.ok:
            force_status["failures"] = int(force_status["failures"]) + 1
            force_status["lastError"] = result.message
            self._append_error(result.message)
            return
        force_status["okSamples"] = int(force_status["okSamples"]) + 1
        force_status["maxAbsLeftN"] = max(
            float(force_status["maxAbsLeftN"]),
            self._max_abs_force(result.left_window or [result.left]),
        )
        force_status["maxAbsRightN"] = max(
            float(force_status["maxAbsRightN"]),
            self._max_abs_force(result.right_window or [result.right]),
        )
        force_status["calibration"] = result.calibration

    async def _sample_cameras(self, config: dict[str, Any]) -> None:
        probe = await asyncio.to_thread(self.hardware.cameras.probe, config)
        if not probe.ok:
            self._append_error(probe.message)
        cameras = self._status["cameras"]
        for camera in probe.cameras:
            item = cameras.setdefault(
                camera.key,
                {
                    "samples": 0,
                    "okSamples": 0,
                    "minFps": 999999.0,
                    "maxFrameAgeMs": 0,
                    "lastHealth": "pending",
                    "lastError": "",
                },
            )
            item["samples"] = int(item["samples"]) + 1
            item["lastHealth"] = camera.health
            item["minFps"] = min(float(item["minFps"]), float(camera.fps))
            item["maxFrameAgeMs"] = max(float(item["maxFrameAgeMs"]), float(camera.frameAgeMs))
            if camera.health == "ok":
                item["okSamples"] = int(item["okSamples"]) + 1
            else:
                item["lastError"] = probe.message

    def _empty_status(self) -> dict[str, Any]:
        return {
            "active": False,
            "runId": "",
            "startedAt": 0,
            "finishedAt": 0,
            "targetDurationS": 0.0,
            "samplePeriodS": 0.0,
            "includeCameras": True,
            "includeForce": True,
            "attemptHalReconnect": True,
            "samples": 0,
            "maxLoopMs": 0.0,
            "hal": {
                "samples": 0,
                "connectedSamples": 0,
                "disconnects": 0,
                "recoveries": 0,
                "reconnectAttempts": 0,
                "lastError": "",
                "lastReconnectError": "",
            },
            "force": {
                "samples": 0,
                "okSamples": 0,
                "failures": 0,
                "skippedTestMode": 0,
                "sampleHz": 0.0,
                "windowSamples": 0,
                "maxAbsLeftN": 0.0,
                "maxAbsRightN": 0.0,
                "lastError": "",
                "calibration": {},
            },
            "cameras": {},
            "websocket": {"clients": 0, "observedSamples": 0},
            "errors": [],
        }

    def _append_error(self, message: str) -> None:
        if not message:
            return
        errors = list(self._status.get("errors", []))
        if errors and errors[-1] == message:
            return
        errors.append(message)
        self._status["errors"] = errors[-20:]

    def _safe_ws_client_count(self) -> int:
        try:
            return int(self._ws_client_count_provider())
        except Exception:
            return 0

    def _sample_hz(self, config: dict[str, Any]) -> float:
        try:
            raw = float(config.get("force", {}).get("sampleHz", 200))
        except (TypeError, ValueError):
            raw = 200.0
        return min(max(raw, 1.0), 10000.0)

    def _real_hardware_mode(self, config: dict[str, Any]) -> bool:
        mode = str(os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")).lower()
        return mode == "real"

    def _max_abs_force(self, rows: list[list[float]]) -> float:
        maximum = 0.0
        for row in rows:
            for value in row[:3]:
                maximum = max(maximum, abs(float(value)))
        return maximum

    def _bounded_float(self, value: Any, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = minimum
        return min(max(parsed, minimum), maximum)
