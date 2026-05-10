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


class TeleopMappingService:
    """Low-rate Omega.7 to slave-arm mapper used during recording.

    The mapping is intentionally conservative: it requires the Omega clutch
    button, sends at most one tiny jog per side per loop, and never runs in test
    HAL mode. This gives the recording page a real teleop entry point without
    surprising the operator with free-running motion.
    """

    def __init__(self, settings: SettingsService, hal: HalClient, logs: LogService) -> None:
        self.settings = settings
        self.hal = hal
        self.logs = logs
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._references: dict[str, list[float]] = {}
        self._axis_busy_until: dict[str, float] = {}
        self._last_action: dict[str, Any] | None = None
        self._last_error = ""
        self._last_error_at = 0.0
        self._armed_at_ms: int | None = None
        self._arm_sources: set[str] = set()

    async def start(self, source: str = "recording") -> dict[str, Any]:
        config = self.settings.get_config()
        mode = self._hal_mode(config)
        # 多个页面动作可能同时“武装”遥操作，source 集合用于避免过早停止。
        self._arm_sources.add(source)
        if self._armed_at_ms is None:
            self._armed_at_ms = now_ms()
            self._references.clear()
            self._axis_busy_until.clear()
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
            "teleop mapper armed for recording; clutch button required, max step 20um / 0.02deg",
        )
        return self.status()

    async def stop(self, source: str = "recording") -> dict[str, Any]:
        self._arm_sources.discard(source)
        if self._arm_sources:
            self._references.clear()
            self._axis_busy_until.clear()
            return self.status()
        self._armed_at_ms = None
        self._references.clear()
        self._axis_busy_until.clear()
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
        self._task = None
        self._stop_event = None
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
                "translationStepUm": 20.0,
                "rotationStepDeg": 0.02,
                "translationVelocityUmS": 100.0,
                "rotationVelocityDegS": 0.2,
            },
        }

    async def _run_loop(self) -> None:
        period_s = 0.1
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
            await asyncio.sleep(max(0.02, period_s - elapsed))

    async def _step(self) -> None:
        config = self.settings.get_config()
        health = await self.hal.health()
        if not health.connected or not health.ltdmc_ok or not health.omega7_ok:
            # 任一硬件边界不可用时丢弃参考点，恢复后需要重新按下离合建立基准。
            self._references.clear()
            return
        omega = await self.hal.omega_state()
        raw_hands = omega.get("hands")
        if not isinstance(raw_hands, list):
            self._references.clear()
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
            return
        reference = self._references.get(side)
        if reference is None:
            # 首帧只作为零位参考，不立即发运动命令，防止连接瞬间跳变。
            self._references[side] = pose
            return
        command = self._command_from_delta(side, pose, reference, config)
        if command is None:
            return
        axis_key = f"{side}-{command['axis']}"
        now = time.monotonic()
        if self._axis_busy_until.get(axis_key, 0.0) > now:
            # 同一轴上一条 jog 未完成前不叠加命令，降低机械冲击风险。
            return
        payload = {
            "side": side,
            "axis": command["axis"],
            "direction": command["direction"],
            "step": command["step"],
            "speedMode": "fine",
            "maxVelocityUiPerSec": command["velocity"],
            "startVelocityUiPerSec": command["velocity"] * 0.2,
            "accTimeSec": 0.05,
            "decTimeSec": 0.05,
        }
        await self.hal.command("motion.manual_axis_move", payload)
        duration = command["step"] / max(command["velocity"], 0.001) + 0.15
        self._axis_busy_until[axis_key] = now + duration
        self._references[side] = pose
        self._last_action = {
            "ts": now_ms(),
            "side": side,
            "axis": command["axis"],
            "delta": command["direction"] * command["step"],
            "unit": "um" if command["axis"] in {"X", "Y", "Z"} else "deg",
        }

    def _command_from_delta(
        self,
        side: SideName,
        pose: list[float],
        reference: list[float],
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        teleop = config.get("teleop", {})
        translation_scale = float(teleop.get(f"{side}TranslationScale", 0.2))
        rotation_scale = float(teleop.get(f"{side}RotationScale", 0.18))
        translation_deadzone_um = float(teleop.get("translationDeadzone", 0.00002)) * 1_000_000.0
        rotation_deadzone_deg = float(teleop.get("rotationDeadzone", 0.08))
        candidates: list[dict[str, Any]] = []
        for idx, axis in enumerate(AXES):
            raw_delta = pose[idx] - reference[idx]
            # 每个循环只选择变化最大的轴，保持遥操作命令离散且可预测。
            if idx < 3:
                value = raw_delta * 1_000_000.0 * translation_scale
                if abs(value) <= translation_deadzone_um * translation_scale:
                    continue
                step = min(abs(value), 20.0)
                velocity = 100.0
            else:
                value = raw_delta * rotation_scale
                if abs(value) <= rotation_deadzone_deg * rotation_scale:
                    continue
                step = min(abs(value), 0.02)
                velocity = 0.2
            candidates.append(
                {
                    "axis": axis,
                    "direction": 1 if value > 0 else -1,
                    "step": step,
                    "velocity": velocity,
                    "score": abs(value),
                }
            )
        if not candidates:
            return None
        return max(candidates, key=lambda item: float(item["score"]))

    def _hal_mode(self, config: dict[str, Any]) -> str:
        return str(os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")).lower()
