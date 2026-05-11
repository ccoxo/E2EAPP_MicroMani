from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Literal

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.hal_client.client import HalClient

AxisName = Literal["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
SideName = Literal["left", "right"]

AXES: tuple[AxisName, AxisName, AxisName, AxisName, AxisName, AxisName] = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
TRANSLATION_STEP_UM = 200.0
ROTATION_STEP_DEG = 0.2
TRANSLATION_VELOCITY_UM_S = 1000.0
ROTATION_VELOCITY_DEG_S = 0.5


class TeleopMappingService:
    """Continuous Omega.7 to slave-arm mapper used during recording."""

    def __init__(self, settings: SettingsService, hal: HalClient, logs: LogService) -> None:
        self.settings = settings
        self.hal = hal
        self.logs = logs
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._references: dict[str, list[float]] = {}
        self._active_sides: set[SideName] = set()
        self._last_action: dict[str, Any] | None = None
        self._last_error = ""
        self._last_error_at = 0.0
        self._armed_at_ms: int | None = None
        self._arm_sources: set[str] = set()

    async def start(self, source: str = "recording") -> dict[str, Any]:
        config = self.settings.get_config()
        mode = self._hal_mode(config)
        self._arm_sources.add(source)
        if self._armed_at_ms is None:
            self._armed_at_ms = now_ms()
            self._references.clear()
            self._active_sides.clear()
        if mode != "real":
            self._last_action = None
            self.logs.info("[HAL]", "teleop mapper armed in test mode; no hardware motion will be sent")
            return self.status()
        if self._task is not None and not self._task.done():
            return self.status()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="teleop-mapping")
        self.logs.warning(
            "[HAL]",
            "teleop mapper armed for recording; clutch button required, max step 200um / 0.2deg",
        )
        return self.status()

    async def stop(self, source: str = "recording") -> dict[str, Any]:
        self._arm_sources.discard(source)
        if self._arm_sources:
            self._references.clear()
            return self.status()
        self._armed_at_ms = None
        self._references.clear()
        stop_event = self._stop_event
        task = self._task
        if stop_event is not None:
            stop_event.set()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._stop_all_active_sides()
        self._task = None
        self._stop_event = None
        if self.logs is not None:
            self.logs.info("[HAL]", "teleop mapper stopped")
        return self.status()

    def status(self) -> dict[str, Any]:
        running = self._task is not None and not self._task.done()
        return {
            "armed": self._armed_at_ms is not None,
            "running": running,
            "armedAt": self._armed_at_ms,
            "sources": sorted(self._arm_sources),
            "lastAction": self._last_action,
            "lastError": self._last_error,
            "limits": {
                "translationStepUm": TRANSLATION_STEP_UM,
                "rotationStepDeg": ROTATION_STEP_DEG,
                "translationVelocityUmS": TRANSLATION_VELOCITY_UM_S,
                "rotationVelocityDegS": ROTATION_VELOCITY_DEG_S,
            },
        }

    async def _run_loop(self) -> None:
        teleop = self.settings.get_config().get("teleop", {})
        period_s = float(teleop.get("commandIntervalMs", 10)) / 1000.0
        while self._stop_event is not None and not self._stop_event.is_set():
            started = time.monotonic()
            try:
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                now = time.monotonic()
                message = str(exc)
                if message != self._last_error or now - self._last_error_at > 2.0:
                    self.logs.error("[HAL]", f"teleop mapper recovered: {message}")
                    self._last_error = message
                    self._last_error_at = now
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.001, period_s - elapsed))

    async def _step(self) -> None:
        config = self.settings.get_config()
        health = await self.hal.health()
        if not health.connected or not health.ltdmc_ok or not health.omega7_ok:
            self._references.clear()
            await self._stop_all_active_sides()
            return
        omega = await self.hal.omega_state()
        raw_hands = omega.get("hands")
        if not isinstance(raw_hands, list):
            self._references.clear()
            await self._stop_all_active_sides()
            return
        for side in ("left", "right"):
            hand = next(
                (
                    item
                    for item in raw_hands
                    if isinstance(item, dict) and item.get("side") == side
                ),
                {},
            )
            if not isinstance(hand, dict):
                continue
            await self._step_side(side, hand, config)

    async def _step_side(self, side: SideName, hand: dict[str, Any], config: dict[str, Any]) -> None:
        teleop = config.get("teleop", {})
        logical_connected = bool(teleop.get(f"{side}Connected", False))
        pose_raw = hand.get("pose")
        pose = [float(value) for value in pose_raw] if isinstance(pose_raw, list) and len(pose_raw) == 6 else None
        require_clutch = bool(teleop.get("requireClutch", False))
        active = (
            logical_connected
            and bool(hand.get("connected", False))
            and bool(hand.get("lastReadOk", False))
            and (not require_clutch or bool(hand.get("clutchPressed", False)))
            and pose is not None
        )
        if not active or pose is None:
            self._references.pop(side, None)
            await self._stop_side_if_active(side)
            return
        reference = self._references.get(side)
        if reference is None:
            self._references[side] = pose
            return
        deltas = self._deltas_from_delta(side, pose, reference, config)
        if deltas is None:
            return
        payload = {
            "side": side,
            "deltas": {axis: deltas[idx] for idx, axis in enumerate(AXES)},
            "translationVelocityUiPerSec": TRANSLATION_VELOCITY_UM_S,
            "rotationVelocityUiPerSec": ROTATION_VELOCITY_DEG_S,
            "translationStartVelocityUiPerSec": TRANSLATION_VELOCITY_UM_S * 0.2,
            "rotationStartVelocityUiPerSec": ROTATION_VELOCITY_DEG_S * 0.2,
            "accTimeSec": 0.05,
            "decTimeSec": 0.05,
        }
        await self.hal.command("motion.teleop_target_update", payload)
        self._active_sides.add(side)
        self._references[side] = pose
        dominant_index = max(range(len(deltas)), key=lambda idx: abs(deltas[idx]))
        delta_vector = [0.0] * 12
        offset = 0 if side == "left" else 6
        for idx, delta in enumerate(deltas):
            delta_vector[offset + idx] = delta
        self._last_action = {
            "ts": now_ms(),
            "side": side,
            "axis": AXES[dominant_index],
            "delta": deltas[dominant_index],
            "unit": "um" if dominant_index < 3 else "deg",
            "deltas": payload["deltas"],
            "deltaVector": delta_vector,
        }

    def _deltas_from_delta(
        self,
        side: SideName,
        pose: list[float],
        reference: list[float],
        config: dict[str, Any],
    ) -> list[float] | None:
        teleop = config.get("teleop", {})
        translation_scale = float(teleop.get(f"{side}TranslationScale", 0.2))
        rotation_scale = float(teleop.get(f"{side}RotationScale", 0.18))
        translation_deadzone_um = float(teleop.get("translationDeadzone", 0.00002)) * 1_000_000.0
        rotation_deadzone_deg = float(teleop.get("rotationDeadzone", 0.08))
        deltas = [0.0] * 6
        for idx in range(6):
            raw_delta = pose[idx] - reference[idx]
            if idx < 3:
                value = raw_delta * 1_000_000.0 * translation_scale
                if abs(value) <= translation_deadzone_um * translation_scale:
                    continue
                deltas[idx] = min(abs(value), TRANSLATION_STEP_UM) * (1 if value > 0 else -1)
            else:
                value = raw_delta * rotation_scale
                if abs(value) <= rotation_deadzone_deg * rotation_scale:
                    continue
                deltas[idx] = min(abs(value), ROTATION_STEP_DEG) * (1 if value > 0 else -1)
        if not any(delta != 0.0 for delta in deltas):
            return None
        return deltas

    async def _stop_all_active_sides(self) -> None:
        for side in tuple(self._active_sides):
            await self._stop_side_if_active(side)

    async def _stop_side_if_active(self, side: SideName) -> None:
        if side not in self._active_sides:
            return
        try:
            await self.hal.command("motion.teleop_stop_side", {"side": side})
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            self._last_error_at = time.monotonic()
            if self.logs is not None:
                self.logs.error("[HAL]", f"teleop stop side failed: {exc}")
        finally:
            self._active_sides.discard(side)

    def _hal_mode(self, config: dict[str, Any]) -> str:
        return str(os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")).lower()
