from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Literal

from backend.core.config import SettingsService
from backend.core.defaults import ICF_TELEOP_DEFAULTS
from backend.core.logging import LogService, now_ms
from backend.hal_client.client import HalClient

AxisName = Literal["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
SideName = Literal["left", "right"]

AXES: tuple[AxisName, AxisName, AxisName, AxisName, AxisName, AxisName] = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
DEFAULT_TRANSLATION_STEP_UM = 5000.0
DEFAULT_ROTATION_STEP_DEG = 0.2
DEFAULT_AXIS_OUTPUT_SCALE = [1.0] * 6
DEFAULT_ENABLED_AXES = [True] * 6


class TeleopMappingService:
    """Continuous Omega.7 to slave-arm mapper used during recording."""

    def __init__(self, settings: SettingsService, hal: HalClient, logs: LogService) -> None:
        self.settings = settings
        self.hal = hal
        self.logs = logs
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._references: dict[str, list[float]] = {}
        self._translation_carry: dict[SideName, list[float]] = {}
        self._translation_direction: dict[SideName, list[int]] = {}
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
            self._translation_carry.clear()
            self._translation_direction.clear()
            self._active_sides.clear()
        if mode != "real":
            self._last_action = None
            self.logs.info("[HAL]", "teleop mapper armed in test mode; no hardware motion will be sent")
            return self.status()
        if self._task is not None and not self._task.done():
            return self.status()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="teleop-mapping")
        translation_step = self._translation_step_um(config)
        rotation_step = self._rotation_step_deg(config)
        self.logs.warning(
            "[HAL]",
            (
                "teleop mapper armed for recording; clutch button required, "
                f"max step {translation_step:g}um / {rotation_step:g}deg"
            ),
        )
        return self.status()

    async def stop(self, source: str = "recording") -> dict[str, Any]:
        self._arm_sources.discard(source)
        if self._arm_sources:
            self._reset_all_tracking()
            return self.status()
        self._armed_at_ms = None
        self._reset_all_tracking()
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
        config = self.settings.get_config() if self.settings is not None else {}
        return {
            "armed": self._armed_at_ms is not None,
            "running": running,
            "armedAt": self._armed_at_ms,
            "sources": sorted(self._arm_sources),
            "lastAction": self._last_action,
            "lastError": self._last_error,
            "limits": {
                "translationStepUm": self._translation_step_um(config),
                "rotationStepDeg": self._rotation_step_deg(config),
                "translationStepLimitPulse": self._translation_step_limit_pulse(config),
                "rotationStepLimitPulse": self._rotation_step_limit_pulse(config),
                "translationVelocityUmS": self._translation_max_velocity_um_s(config),
                "rotationVelocityDegS": self._rotation_max_velocity_deg_s(config),
            },
        }

    async def _run_loop(self) -> None:
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
            period_s = self._command_interval_s(self.settings.get_config())
            await asyncio.sleep(max(0.001, period_s - elapsed))

    async def _step(self) -> None:
        config = self.settings.get_config()
        health = await self.hal.health()
        if not health.connected or not health.ltdmc_ok or not health.omega7_ok:
            self._reset_all_tracking()
            await self._stop_all_active_sides()
            return
        omega = await self.hal.omega_state()
        raw_hands = omega.get("hands")
        if not isinstance(raw_hands, list):
            self._reset_all_tracking()
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
            self._reset_side_tracking(side)
            await self._stop_side_if_active(side)
            return
        reference = self._references.get(side)
        if reference is None:
            self._references[side] = pose
            return
        deltas = self._deltas_from_delta(side, pose, reference, config)
        sync_zero_delta_target = True
        soft_limit_min, soft_limit_max = self._soft_limit_arrays(side, config)
        payload = {
            "side": side,
            "deltas": {axis: deltas[idx] for idx, axis in enumerate(AXES)},
            "translationStepUm": self._translation_step_um(config),
            "rotationStepDeg": self._rotation_step_deg(config),
            "translationStepLimitPulse": self._translation_step_limit_pulse(config),
            "rotationStepLimitPulse": self._rotation_step_limit_pulse(config),
            "enabledAxes": self._enabled_axes(side, config),
            "syncZeroDeltaTarget": sync_zero_delta_target,
            "softLimitMin": soft_limit_min,
            "softLimitMax": soft_limit_max,
            "translationVelocityUiPerSec": self._translation_max_velocity_um_s(config),
            "rotationVelocityUiPerSec": self._rotation_max_velocity_deg_s(config),
            "translationStartVelocityUiPerSec": self._translation_start_velocity_um_s(config),
            "rotationStartVelocityUiPerSec": self._rotation_start_velocity_deg_s(config),
            "accTimeSec": self._motion_profile_acc_sec(config),
            "decTimeSec": self._motion_profile_dec_sec(config),
        }
        self._active_sides.add(side)
        try:
            await self.hal.command("motion.teleop_target_update", payload)
        except Exception:
            self._reset_side_tracking(side)
            await self._stop_side_if_active(side)
            raise
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
    ) -> list[float]:
        teleop = config.get("teleop", {})
        translation_key = f"{side}TranslationScale"
        rotation_key = f"{side}RotationScale"
        translation_scale = float(teleop.get(translation_key, ICF_TELEOP_DEFAULTS[translation_key]))
        rotation_scale = float(teleop.get(rotation_key, ICF_TELEOP_DEFAULTS[rotation_key]))
        axis_scale = self._axis_output_scale(side, config)
        translation_deadzone_m = float(teleop.get("translationDeadzone", 0.00002))
        rotation_deadzone_deg = float(teleop.get("rotationDeadzone", 0.05))
        deltas = [0.0] * 6
        for idx in range(6):
            raw_delta = pose[idx] - reference[idx]
            if idx < 3:
                filtered = self._filter_incremental_translation(side, idx, raw_delta, translation_deadzone_m, config)
                if filtered == 0.0:
                    continue
                deltas[idx] = filtered * 1_000_000.0 * translation_scale * axis_scale[idx]
            else:
                if abs(raw_delta) < rotation_deadzone_deg:
                    continue
                deltas[idx] = raw_delta * rotation_scale * axis_scale[idx]
        return deltas

    def _filter_incremental_translation(
        self,
        side: SideName,
        axis_index: int,
        raw_delta: float,
        deadzone_m: float,
        config: dict[str, Any],
    ) -> float:
        if abs(raw_delta) < deadzone_m:
            return 0.0
        carry = self._translation_carry.setdefault(side, [0.0, 0.0, 0.0])
        direction = self._translation_direction.setdefault(side, [0, 0, 0])
        carry[axis_index] += raw_delta
        carry_sign = 1 if carry[axis_index] > 1e-12 else -1 if carry[axis_index] < -1e-12 else 0
        if carry_sign == 0:
            carry[axis_index] = 0.0
            return 0.0
        reversing = direction[axis_index] != 0 and carry_sign != direction[axis_index]
        threshold = (
            self._incremental_translation_reverse_deadzone(config)
            if reversing
            else self._incremental_translation_min_effective_delta(config)
        )
        if abs(carry[axis_index]) < threshold:
            return 0.0
        output = carry[axis_index]
        carry[axis_index] = 0.0
        direction[axis_index] = carry_sign
        return output

    def _command_interval_s(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("commandIntervalMs", 10))) / 1000.0

    def _translation_step_um(self, config: dict[str, Any]) -> float:
        return max(0.0, float(config.get("teleop", {}).get("translationStepUm", DEFAULT_TRANSLATION_STEP_UM)))

    def _rotation_step_deg(self, config: dict[str, Any]) -> float:
        return max(0.0, float(config.get("teleop", {}).get("rotationStepDeg", DEFAULT_ROTATION_STEP_DEG)))

    def _translation_step_limit_pulse(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("translationStepLimitPulse", 4000.0)))

    def _rotation_step_limit_pulse(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("rotationStepLimitPulse", 1250.0)))

    def _translation_start_velocity_um_s(self, config: dict[str, Any]) -> float:
        return max(0.0, float(config.get("teleop", {}).get("translationStartVelocityUmS", 300.0)))

    def _translation_max_velocity_um_s(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("translationMaxVelocityUmS", 4000.0)))

    def _rotation_start_velocity_deg_s(self, config: dict[str, Any]) -> float:
        return max(0.0, float(config.get("teleop", {}).get("rotationStartVelocityDegS", 0.25)))

    def _rotation_max_velocity_deg_s(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("rotationMaxVelocityDegS", 3.0)))

    def _motion_profile_acc_sec(self, config: dict[str, Any]) -> float:
        return max(0.001, float(config.get("teleop", {}).get("motionProfileAccSec", 0.05)))

    def _motion_profile_dec_sec(self, config: dict[str, Any]) -> float:
        return max(0.001, float(config.get("teleop", {}).get("motionProfileDecSec", 0.05)))

    def _incremental_translation_min_effective_delta(self, config: dict[str, Any]) -> float:
        teleop = config.get("teleop", {})
        return max(
            float(teleop.get("translationDeadzone", 0.00002)),
            float(teleop.get("incrementalTranslationMinEffectiveDelta", 0.00005)),
        )

    def _incremental_translation_reverse_deadzone(self, config: dict[str, Any]) -> float:
        return max(
            self._incremental_translation_min_effective_delta(config),
            float(config.get("teleop", {}).get("incrementalTranslationReverseDeadzone", 0.00010)),
        )

    def _axis_output_scale(self, side: SideName, config: dict[str, Any]) -> list[float]:
        key = f"{side}AxisOutputScale"
        raw = config.get("teleop", {}).get(key, ICF_TELEOP_DEFAULTS.get(key, DEFAULT_AXIS_OUTPUT_SCALE))
        if not isinstance(raw, list) or len(raw) != 6:
            raw = ICF_TELEOP_DEFAULTS.get(key, DEFAULT_AXIS_OUTPUT_SCALE)
        return [float(value) for value in raw]

    def _enabled_axes(self, side: SideName, config: dict[str, Any]) -> list[bool]:
        key = f"{side}EnabledAxes"
        raw = config.get("teleop", {}).get(key, DEFAULT_ENABLED_AXES)
        if not isinstance(raw, list) or len(raw) != 6:
            raw = DEFAULT_ENABLED_AXES
        return [bool(value) for value in raw]

    def _soft_limit_arrays(self, side: SideName, config: dict[str, Any]) -> tuple[list[float], list[float]]:
        teleop = config.get("teleop", {})
        min_key = f"{side}SoftLimitMin"
        max_key = f"{side}SoftLimitMax"
        default_mins = [float(value) for value in ICF_TELEOP_DEFAULTS[min_key]]
        default_maxes = [float(value) for value in ICF_TELEOP_DEFAULTS[max_key]]
        if not isinstance(teleop, dict):
            return default_mins, default_maxes
        mins = self._coerce_teleop_soft_limit_array(teleop.get(min_key))
        maxes = self._coerce_teleop_soft_limit_array(teleop.get(max_key))
        if mins is None or maxes is None:
            return default_mins, default_maxes
        if any(min_value >= max_value for min_value, max_value in zip(mins, maxes, strict=True)):
            return default_mins, default_maxes
        return mins, maxes

    def _coerce_teleop_soft_limit_array(self, raw: Any) -> list[float] | None:
        if not isinstance(raw, list) or len(raw) != 6:
            return None
        try:
            return [float(value) for value in raw]
        except (TypeError, ValueError):
            return None

    def _reset_all_tracking(self) -> None:
        self._references.clear()
        self._translation_carry.clear()
        self._translation_direction.clear()

    def _reset_side_tracking(self, side: SideName) -> None:
        self._references.pop(side, None)
        self._translation_carry.pop(side, None)
        self._translation_direction.pop(side, None)

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
