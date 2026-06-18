"""Coordinate Omega.7 teleop modes and guard live motion handoffs.

This service owns both the backend fallback loop and the HAL-native start/stop
path, so comments here focus on work-origin safety gates and state transitions
that can move real hardware.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections import deque
from typing import Any, Literal

from backend.core.config import SettingsService
from backend.core.defaults import ICF_KINEMATICS_DEFAULTS, ICF_TELEOP_DEFAULTS
from backend.core.logging import LogService, now_ms
from backend.core.motion_limits import (
    WorkOriginMissing,
    effective_limit_arrays,
    native_teleop_limit_arrays,
    rotation_work_limit_arrays,
    rotation_work_limit_enabled,
    rotation_work_limits_ui,
    side_home_reference_ui,
    side_positions_ui,
)
from backend.core.units import motion_pulse_per_unit
from backend.hal_client.client import HalClient

AxisName = Literal["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
SideName = Literal["left", "right"]

AXES: tuple[AxisName, AxisName, AxisName, AxisName, AxisName, AxisName] = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
DEFAULT_TRANSLATION_STEP_UM = 5000.0
DEFAULT_ROTATION_STEP_DEG = 0.2
DEFAULT_AXIS_OUTPUT_SCALE = [1.0] * 6
DEFAULT_ENABLED_AXES = [True] * 6
NATIVE_STATUS_SUMMARY_LOG_INTERVAL_MS = 5000
DEFAULT_NATIVE_STATUS_SAMPLE_HZ = 30.0
PRE_HOME_ROTATION_LIMIT_TOLERANCE_DEG = 1e-6


class TeleopMappingService:
    """Continuous Omega.7 to slave-arm mapper used during recording."""

    _NATIVE_ARM_SOURCES = {"teleop-connect", "recording"}

    def __init__(self, settings: SettingsService, hal: HalClient, logs: LogService) -> None:
        self.settings = settings
        self.hal = hal
        self.logs = logs
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._references: dict[str, list[float]] = {}
        self._translation_carry: dict[SideName, list[float]] = {}
        self._translation_direction: dict[SideName, list[int]] = {}
        self._continuous_direction: dict[SideName, list[int]] = {}
        self._continuous_streak: dict[SideName, list[int]] = {}
        self._active_sides: set[SideName] = set()
        self._last_action: dict[str, Any] | None = None
        self._action_history: deque[dict[str, Any]] = deque(maxlen=1000)
        self._last_error = ""
        self._last_error_at = 0.0
        self._armed_at_ms: int | None = None
        self._arm_sources: set[str] = set()
        self._last_blockers: dict[str, dict[str, Any]] = {}
        self._last_diag_zero_log_ms: dict[str, int] = {}
        self._last_native_diag_action_key = ""
        self._last_native_status_summary = ""
        self._last_native_status_summary_ms = 0
        self._native_status_cache: dict[str, Any] = {}
        self._last_native_payload: dict[str, Any] | None = None
        self._native_transition_lock = asyncio.Lock()

    async def _get_config_async(self) -> dict[str, Any]:
        if self.settings is None:
            return {}
        return await asyncio.to_thread(self.settings.get_config)

    async def start(
        self,
        source: str = "recording",
        home_side: SideName | None = None,
        *,
        pre_home: bool = True,
    ) -> dict[str, Any]:
        config = await self._get_config_async()
        op_id = self.logs.new_op_id("teleop") if self.logs is not None else None
        mode = self._hal_mode(config)
        if mode == "real" and self._native_engine(config):
            # Native teleop reconfiguration can stop/re-home/start hardware, so
            # serialize it even when UI connect and recording start race.
            async with self._native_transition_lock:
                return await self._start_native_locked(config, op_id, source, home_side, pre_home)
        if self._task is not None and not self._task.done():
            self._arm_sources.add(source)
            return self.status(config)
        if mode == "real" and pre_home:
            await self._return_to_work_origin_before_start(config, home_side)
        self._arm_sources.add(source)
        if self._armed_at_ms is None:
            self._armed_at_ms = now_ms()
            self._references.clear()
            self._translation_carry.clear()
            self._translation_direction.clear()
            self._continuous_direction.clear()
            self._continuous_streak.clear()
            self._active_sides.clear()
            self._action_history.clear()
        if mode != "real":
            self._last_action = None
            self._action_history.clear()
            self.logs.info("[HAL]", "teleop mapper armed in test mode; no hardware motion will be sent")
            return self.status(config)
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
        return self.status(config)

    async def _start_native_locked(
        self,
        config: dict[str, Any],
        op_id: str | None,
        source: str,
        home_side: SideName | None,
        pre_home: bool,
    ) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            # A running native controller may be shared by manual teleop-connect
            # and recording. Track sources separately so stopping one source does
            # not surprise the other unless the payload must be restarted.
            source_was_present = source in self._arm_sources
            self._arm_sources.add(source)
            try:
                await self._validate_native_start_current_rotation_positions(
                    config,
                    source=source,
                    allow_recovery=True,
                )
            except Exception:
                if not source_was_present:
                    self._arm_sources.discard(source)
                await self._stop_native_after_start_gate_failure(config)
                raise
            try:
                payload = self._native_payload(config)
                changed_origin_sides = self._native_payload_changed_work_origin_sides(payload)
                if payload == self._last_native_payload:
                    return self.status(config)
                if changed_origin_sides:
                    await self._stop_and_return_changed_work_origins(config, changed_origin_sides, payload)
                if source == "teleop-connect":
                    await self._start_native_payload(payload)
                else:
                    await self._configure_and_start_native_payload(payload)
            except Exception:
                if not source_was_present:
                    self._arm_sources.discard(source)
                raise
            return self.status(config)
        if pre_home:
            await self._return_to_work_origin_before_start(config, home_side)
        self._arm_sources.add(source)
        try:
            await self._validate_native_start_current_rotation_positions(
                config,
                source=source,
                allow_recovery=not (pre_home and self._native_prehome_enabled(config)),
            )
        except Exception:
            self._arm_sources.discard(source)
            raise
        if self._armed_at_ms is None:
            self._armed_at_ms = now_ms()
            self._references.clear()
            self._translation_carry.clear()
            self._translation_direction.clear()
            self._continuous_direction.clear()
            self._continuous_streak.clear()
            self._active_sides.clear()
            self._action_history.clear()
            self._last_action = None
            self._last_native_diag_action_key = ""
            self._native_status_cache = {}
        try:
            await self._configure_and_start_native(config)
        except Exception:
            self._arm_sources.discard(source)
            if not self._arm_sources:
                self._armed_at_ms = None
                self._reset_all_tracking()
                self._last_action = None
                self._action_history.clear()
                self._last_blockers = {}
                self._last_error = ""
                self._last_native_diag_action_key = ""
                self._native_status_cache = {}
                self._last_native_payload = None
            raise
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_native_status_loop(), name="hal-native-teleop-status")
        self._log_teleop_mode(config, op_id, source, "start", native=True)
        self._log_teleop_profiles(config, op_id)
        self.logs.warning("[HAL]", "HAL-native teleop controller armed")
        return self.status(config)

    async def _return_to_work_origin_before_start(
        self,
        config: dict[str, Any],
        side: SideName | None = None,
        *,
        force: bool = False,
    ) -> None:
        teleop_config = config.get("teleop", {})
        if not force and isinstance(teleop_config, dict) and not bool(teleop_config.get("homeBeforeStart", True)):
            return
        startup_config = config.get("motion", {}).get("homeOnStartup", {})
        if (
            not force
            and isinstance(startup_config, dict)
            and str(startup_config.get("mode", "work_origin")) != "work_origin"
        ):
            return
        origin = self._normalized_motion_origin(config)
        if side is None and not bool(origin["valid"]):
            raise RuntimeError("motion work origin is not captured")
        if side is None:
            await self.hal.command(
                "motion.home_all",
                {
                    "leftPulse": origin["leftPulse"],
                    "rightPulse": origin["rightPulse"],
                    "leftEnabledAxes": self._enabled_axes("left", config),
                    "rightEnabledAxes": self._enabled_axes("right", config),
                },
            )
            self.logs.info("[HAL]", "teleop pre-start return-to-work-origin completed")
            return
        valid_key = "leftValid" if side == "left" else "rightValid"
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        if not bool(origin[valid_key]):
            raise RuntimeError(f"{side} motion work origin is not captured")
        await self.hal.command(
            "motion.home_origin_side",
            {
                "side": side,
                "pulse": origin[pulse_key],
                "enabledAxes": self._enabled_axes(side, config),
            },
        )
        self.logs.info("[HAL]", f"teleop pre-start {side} return-to-work-origin completed")

    async def _validate_prehome_current_rotation_positions(
        self,
        config: dict[str, Any],
        sides: tuple[SideName, ...],
        *,
        allow_recovery: bool = False,
    ) -> None:
        if not rotation_work_limit_enabled(config):
            return
        # Native rotation limits are relative to the captured hardware home
        # reference. Validate before arming so an old work origin cannot launch
        # teleop from outside its allowed recovery envelope.
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        for side in sides:
            positions = side_positions_ui(config, side, pulses)
            home_reference = side_home_reference_ui(config, side)
            if home_reference is None:
                raise RuntimeError(f"{side} motion hardware zero is not captured")
            limits = rotation_work_limits_ui(config, side)
            enabled_axes = self._enabled_axes(side, config)
            for axis_index in range(3, 6):
                if not enabled_axes[axis_index]:
                    continue
                work_limit = limits[axis_index]
                limit_min = home_reference[axis_index] + work_limit.min
                limit_max = home_reference[axis_index] + work_limit.max
                current = positions[axis_index]
                if limit_min > limit_max:
                    raise RuntimeError(
                        f"{side} {AXES[axis_index]} current position is outside work soft limit: "
                        f"{current:.3f} not in [{limit_min:.3f}, {limit_max:.3f}]; "
                        "recapture work origin before native teleop"
                    )
                outside = (
                    current < limit_min - PRE_HOME_ROTATION_LIMIT_TOLERANCE_DEG
                    or current > limit_max + PRE_HOME_ROTATION_LIMIT_TOLERANCE_DEG
                )
                if outside and allow_recovery:
                    self._mark_native_rotation_recovery(side, AXES[axis_index], current, limit_min, limit_max)
                    continue
                if outside:
                    raise RuntimeError(
                        f"{side} {AXES[axis_index]} current position is outside work soft limit: "
                        f"{current:.3f} not in [{limit_min:.3f}, {limit_max:.3f}]; "
                        "recapture work origin before native teleop"
                    )

    async def _validate_native_start_current_rotation_positions(
        self,
        config: dict[str, Any],
        *,
        source: str | None = None,
        allow_recovery: bool = False,
    ) -> None:
        if source is not None and not self._source_requires_native_arm_motion(source):
            return
        sides = self._native_motion_target_sides(config)
        if sides:
            await self._validate_prehome_current_rotation_positions(
                config,
                sides,
                allow_recovery=allow_recovery,
            )

    def _source_requires_native_arm_motion(self, source: str) -> bool:
        return source in self._NATIVE_ARM_SOURCES

    def _native_arm_motion_active(self) -> bool:
        return any(source in self._arm_sources for source in self._NATIVE_ARM_SOURCES)

    def _native_prehome_enabled(self, config: dict[str, Any]) -> bool:
        teleop_config = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        if isinstance(teleop_config, dict) and not bool(teleop_config.get("homeBeforeStart", True)):
            return False
        startup_config = config.get("motion", {}).get("homeOnStartup", {})
        return not (
            isinstance(startup_config, dict)
            and str(startup_config.get("mode", "work_origin")) != "work_origin"
        )

    def _native_startup_blockers(self, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if not self._native_prehome_enabled(config):
            return {}
        origin = self._normalized_motion_origin(config)
        blockers: dict[str, dict[str, Any]] = {}
        ts = now_ms()
        for source_side in ("left", "right"):
            target_side = self._target_side_for_source(source_side, config)
            valid_key = "leftValid" if target_side == "left" else "rightValid"
            if bool(origin[valid_key]):
                continue
            reasons = [f"{target_side} motion work origin is not captured"]
            previous_detail = self._previous_origin_restore_blocker_detail(config, target_side)
            if previous_detail:
                reasons.append(previous_detail)
            blockers[source_side] = {
                "sourceSide": source_side,
                "targetSide": target_side,
                "active": False,
                "state": "blocked",
                "reasons": reasons,
                "ts": ts,
            }
        return blockers

    def _previous_origin_restore_blocker_detail(self, config: dict[str, Any], side: SideName) -> str | None:
        motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
        origin = motion.get("origin", {}) if isinstance(motion, dict) else {}
        if not isinstance(origin, dict) or not bool(origin.get("previousValid", False)):
            return None
        pulse_key = "previousLeftPulse" if side == "left" else "previousRightPulse"
        pulses = self._six_pulses(origin.get(pulse_key))
        try:
            positions = side_positions_ui(config, side, pulses)
            soft_limit_min, soft_limit_max = effective_limit_arrays(config, side)
        except (TypeError, ValueError, RuntimeError, WorkOriginMissing) as exc:
            return f"previous {side} work origin cannot be validated: {exc}"
        enabled_axes = self._enabled_axes(side, config)
        for axis_index, axis_name in enumerate(AXES):
            if not enabled_axes[axis_index]:
                continue
            position = positions[axis_index]
            limit_min = soft_limit_min[axis_index]
            limit_max = soft_limit_max[axis_index]
            if position < limit_min - 1e-6 or position > limit_max + 1e-6:
                return (
                    f"previous {side} work origin exceeds effective soft limit: "
                    f"{axis_name} {position:.3f} not in [{limit_min:.3f}, {limit_max:.3f}]"
                )
        return None

    def _mark_native_rotation_recovery(
        self,
        side: SideName,
        axis: str,
        current: float,
        limit_min: float,
        limit_max: float,
    ) -> None:
        message = (
            f"{side} {axis} current position is outside work soft limit: "
            f"{current:.3f} not in [{limit_min:.3f}, {limit_max:.3f}]; native recovery clipping enabled"
        )
        self._last_blockers[side] = {"state": "recovery", "message": message}
        if self.logs is not None:
            self.logs.warning("[HAL]", message)

    def _native_motion_target_sides(self, config: dict[str, Any]) -> tuple[SideName, ...]:
        if not self._native_arm_motion_active():
            return ()
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        sides: list[SideName] = []
        for source_side in ("left", "right"):
            if not bool(teleop.get(f"{source_side}Connected", False)):
                continue
            target_side = self._target_side_for_source(source_side, config)
            if target_side not in sides:
                sides.append(target_side)
        return tuple(sides)

    def _native_payload_changed_work_origin_sides(self, payload: dict[str, Any]) -> list[SideName]:
        previous = self._last_native_payload
        if not isinstance(previous, dict):
            return []
        changed: list[SideName] = []
        for side in ("left", "right"):
            valid_key = f"{side}WorkOriginValid"
            pulse_key = f"{side}WorkOriginPulse"
            if bool(payload.get(valid_key)) != bool(previous.get(valid_key)):
                changed.append(side)
                continue
            if list(payload.get(pulse_key, [])) != list(previous.get(pulse_key, [])):
                changed.append(side)
        return changed

    async def _stop_and_return_changed_work_origins(
        self,
        config: dict[str, Any],
        sides: list[SideName],
        payload: dict[str, Any],
    ) -> None:
        if not sides:
            return
        self._log_native_origin_transition(sides, payload)
        await self.hal.command("teleop.native.stop", {})
        self._native_status_cache = {}
        self._last_native_payload = None
        for side in sides:
            await self._return_to_work_origin_before_start(config, side, force=True)

    def _log_native_origin_transition(self, sides: list[SideName], payload: dict[str, Any]) -> None:
        if self.logs is None:
            return
        previous = self._last_native_payload if isinstance(self._last_native_payload, dict) else {}
        fields: dict[str, Any] = {
            "action": "force_rehome_before_native_restart",
            "changedSides": list(sides),
        }
        for side in sides:
            valid_key = f"{side}WorkOriginValid"
            pulse_key = f"{side}WorkOriginPulse"
            prefix = "left" if side == "left" else "right"
            fields[f"{prefix}OldWorkOriginValid"] = bool(previous.get(valid_key, False))
            fields[f"{prefix}NewWorkOriginValid"] = bool(payload.get(valid_key, False))
            fields[f"{prefix}OldWorkOriginPulse"] = previous.get(pulse_key, [])
            fields[f"{prefix}NewWorkOriginPulse"] = payload.get(pulse_key, [])
        self.logs.event(
            "[HAL]",
            "WARNING",
            "teleop_origin_transition",
            component="TELEOP",
            **fields,
        )

    async def stop(self, source: str = "recording", *, restart_remaining: bool = True) -> dict[str, Any]:
        config = await self._get_config_async()
        if self._native_engine(config):
            async with self._native_transition_lock:
                return await self._stop_native_locked(config, source, restart_remaining)
        self._arm_sources.discard(source)
        if self._arm_sources:
            self._reset_all_tracking()
            return self.status(config)
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
        return self.status(config)

    async def _stop_native_locked(
        self,
        config: dict[str, Any],
        source: str,
        restart_remaining: bool,
    ) -> dict[str, Any]:
        source_was_present = source in self._arm_sources
        self._arm_sources.discard(source)
        task_running = self._task is not None and not self._task.done()
        if not source_was_present and not self._arm_sources and self._armed_at_ms is None and not task_running:
            return self.status(config)
        if self._arm_sources:
            if restart_remaining:
                try:
                    await self._validate_native_start_current_rotation_positions(
                        config,
                        allow_recovery=True,
                    )
                except Exception:
                    await self._stop_native_after_start_gate_failure(config)
                    raise
                await self._configure_and_start_native(config)
            return self.status(config)
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
        try:
            await self.hal.command("teleop.native.stop", {})
        finally:
            self._task = None
            self._stop_event = None
            self._last_action = None
            self._action_history.clear()
            self._last_blockers = {}
            self._last_error = ""
            self._last_native_diag_action_key = ""
            self._last_native_status_summary = ""
            self._last_native_status_summary_ms = 0
            self._native_status_cache = {}
            self._last_native_payload = None
        if self.logs is not None:
            self._log_teleop_mode(config, None, source, "stop", native=True)
            self.logs.info("[HAL]", "HAL-native teleop controller stopped")
        return self.status(config)

    async def _stop_native_after_start_gate_failure(self, config: dict[str, Any]) -> None:
        self._arm_sources.clear()
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
        try:
            await self.hal.command("teleop.native.stop", {})
        finally:
            self._task = None
            self._stop_event = None
            self._last_action = None
            self._action_history.clear()
            self._last_blockers = {}
            self._last_error = ""
            self._last_native_diag_action_key = ""
            self._last_native_status_summary = ""
            self._last_native_status_summary_ms = 0
            self._native_status_cache = {}
            self._last_native_payload = None
        if self.logs is not None:
            self.logs.warning("[HAL]", "HAL-native teleop controller stopped after native start gate failure")

    def status(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        running = self._task is not None and not self._task.done()
        config = config if config is not None else (self.settings.get_config() if self.settings is not None else {})
        native_engine = self._native_engine(config)
        if native_engine and self._native_status_cache:
            running = bool(self._native_status_cache.get("running", running))
        blockers = dict(self._last_blockers)
        if native_engine and self._armed_at_ms is None:
            blockers = {**self._native_startup_blockers(config), **blockers}
        return {
            "armed": self._armed_at_ms is not None,
            "running": running,
            "transitioning": self._native_transition_lock.locked() if native_engine else False,
            "armedAt": self._armed_at_ms,
            "sources": sorted(self._arm_sources),
            "lastAction": self._last_action,
            "actionHistory": list(self._action_history),
            "lastError": self._last_error,
            "blockers": blockers,
            "nativeStatus": dict(self._native_status_cache) if native_engine else {},
            "limits": {
                "translationStepUm": self._translation_step_um(config),
                "rotationStepDeg": self._rotation_step_deg(config),
                "translationStepLimitPulse": self._translation_step_limit_pulse(config),
                "rotationStepLimitPulse": self._rotation_step_limit_pulse(config),
                "translationPulseDeadband": self._translation_pulse_deadband(config),
                "rotationPulseDeadband": self._rotation_pulse_deadband(config),
                "translationVelocityUmS": self._translation_max_velocity_um_s(config),
                "rotationVelocityDegS": self._rotation_max_velocity_deg_s(config),
                "continuousIncrementMode": self._continuous_increment_mode(config),
                "translationInputEpsilon": self._translation_input_epsilon_m(config),
                "rotationInputEpsilon": self._rotation_input_epsilon_deg(config),
                "translationMinActivePulse": self._translation_min_active_pulse(config),
                "rotationMinActivePulse": self._rotation_min_active_pulse(config),
                "continuousMicroConfirmTicks": self._continuous_micro_confirm_ticks(config),
            },
        }

    async def _run_native_status_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._refresh_native_status()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                now = time.monotonic()
                message = str(exc)
                if message != self._last_error or now - self._last_error_at > 2.0:
                    self.logs.error("[HAL]", f"HAL-native teleop status recovered: {message}")
                    self._last_error = message
                    self._last_error_at = now
            config = await self._get_config_async()
            await asyncio.sleep(max(self._native_status_interval_s(config), self._command_interval_s(config)))

    async def _configure_and_start_native(self, config: dict[str, Any]) -> None:
        payload = self._native_payload(config)
        await self._configure_and_start_native_payload(payload)

    async def _configure_and_start_native_payload(self, payload: dict[str, Any]) -> None:
        await self.hal.command("teleop.native.configure", payload)
        await self.hal.command("teleop.native.start", payload)
        self._last_native_payload = dict(payload)

    async def _start_native(self, config: dict[str, Any]) -> None:
        await self._start_native_payload(self._native_payload(config))

    async def _start_native_payload(self, payload: dict[str, Any]) -> None:
        await self.hal.command("teleop.native.start", payload)
        self._last_native_payload = dict(payload)

    async def _refresh_native_status(self) -> None:
        hal_result = await self.hal.command("teleop.native.status", {})
        payload = self._hal_response_payload(hal_result)
        self._native_status_cache = payload
        if isinstance(payload, dict) and not bool(payload.get("running", False)):
            self._last_action = None
            self._action_history.clear()
            self._last_blockers = {}
            self._last_native_diag_action_key = ""
            error = payload.get("lastError")
            if isinstance(error, str):
                self._last_error = error
            return
        last_action = payload.get("lastAction") if isinstance(payload, dict) else None
        config = await self._get_config_async()
        if isinstance(last_action, dict):
            self._last_action = last_action
            self._log_native_diag_action(config, last_action, payload)
        history = payload.get("actionHistory") if isinstance(payload, dict) else None
        if isinstance(history, list):
            self._action_history.clear()
            for item in history[-1000:]:
                if isinstance(item, dict):
                    self._action_history.append(item)
        blockers = payload.get("blockers") if isinstance(payload, dict) else None
        if isinstance(blockers, dict):
            self._last_blockers = dict(blockers)
        error = payload.get("lastError") if isinstance(payload, dict) else None
        if isinstance(error, str):
            self._last_error = error
        self._log_native_status_summary(config, payload)

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
            period_s = self._command_interval_s(await self._get_config_async())
            await asyncio.sleep(max(0.001, period_s - elapsed))

    async def _step(self) -> None:
        config = await self._get_config_async()
        health = await self.hal.health()
        if not health.connected or not health.ltdmc_ok or not health.omega7_ok:
            reasons = []
            if not health.connected:
                reasons.append("HAL unavailable")
            if not health.ltdmc_ok:
                reasons.append("LTDMC not ready")
            if not health.omega7_ok:
                reasons.append("Omega.7 not ready")
            self._set_all_blocked(config, reasons)
            self._reset_all_tracking()
            await self._stop_all_active_sides()
            return
        omega = await self.hal.omega_state()
        raw_hands = omega.get("hands")
        if not isinstance(raw_hands, list):
            self._set_all_blocked(config, ["omega state missing hands"])
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
        target_side = self._target_side_for_source(side, config)
        logical_connected = bool(teleop.get(f"{side}Connected", False))
        pose_raw = hand.get("pose")
        pose = [float(value) for value in pose_raw] if isinstance(pose_raw, list) and len(pose_raw) == 6 else None
        require_clutch = bool(teleop.get("requireClutch", False))
        blockers: list[str] = []
        if not logical_connected:
            blockers.append("logical hand is disconnected")
        if not bool(hand.get("connected", False)):
            blockers.append("physical hand is disconnected")
        if not bool(hand.get("lastReadOk", False)):
            blockers.append("Omega.7 lastReadOk is false")
        if require_clutch and not bool(hand.get("clutchPressed", False)):
            blockers.append("clutch is required but not pressed")
        if pose is None:
            blockers.append("Omega.7 pose is unavailable")
        active = (
            logical_connected
            and bool(hand.get("connected", False))
            and bool(hand.get("lastReadOk", False))
            and (not require_clutch or bool(hand.get("clutchPressed", False)))
            and pose is not None
        )
        if not active or pose is None:
            self._set_blocked(side, target_side, blockers)
            self._reset_side_tracking(side)
            await self._stop_side_if_active(target_side)
            return
        self._set_active(side, target_side)
        reference = self._references.get(side)
        if reference is None:
            # First valid clutch frame establishes a pose reference; movement
            # starts on the next frame so a reconnect cannot create a jump.
            self._references[side] = pose
            self._last_blockers[side]["state"] = "reference"
            return
        enabled_axes = self._enabled_axes(target_side, config)
        deltas, requested_pulse_deltas = self._deltas_from_delta(
            side,
            target_side,
            pose,
            reference,
            config,
            enabled_axes,
        )
        sync_zero_delta_target = True
        soft_limit_min, soft_limit_max = self._effective_limit_arrays(target_side, config)
        payload = {
            # HAL receives both UI-space deltas and pulse/limit metadata so the
            # final clipping decision happens next to the motion controller.
            "side": target_side,
            "deltas": {axis: deltas[idx] for idx, axis in enumerate(AXES)},
            "translationStepUm": self._translation_step_um(config),
            "rotationStepDeg": self._rotation_step_deg(config),
            "translationStepLimitPulse": self._translation_step_limit_pulse(config),
            "rotationStepLimitPulse": self._rotation_step_limit_pulse(config),
            "translationPulseDeadband": self._translation_pulse_deadband(config),
            "rotationPulseDeadband": self._rotation_pulse_deadband(config),
            "enabledAxes": enabled_axes,
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
        self._active_sides.add(target_side)
        command_started_s = time.monotonic()
        try:
            hal_result = await self.hal.command("motion.teleop_target_update", payload)
        except Exception:
            self._reset_side_tracking(side)
            await self._stop_side_if_active(target_side)
            raise
        command_latency_ms = (time.monotonic() - command_started_s) * 1000.0
        self._references[side] = pose
        applied_deltas = self._applied_deltas_from_hal_result(hal_result, deltas)
        hal_payload = self._hal_response_payload(hal_result)
        requested_delta_pulse = (
            self._six_axis_deltas(hal_payload.get("requestedDeltaPulse"))
            if isinstance(hal_payload, dict)
            else None
        )
        applied_delta_pulse = (
            self._six_axis_deltas(hal_payload.get("appliedDeltaPulse"))
            if isinstance(hal_payload, dict)
            else None
        )
        target_pulse = self._six_axis_deltas(hal_payload.get("targetPulse")) if isinstance(hal_payload, dict) else None
        current_pulse = (
            self._six_axis_deltas(hal_payload.get("currentPulse")) if isinstance(hal_payload, dict) else None
        )
        launch_delta_pulse = (
            self._six_axis_deltas(hal_payload.get("launchDeltaPulse")) if isinstance(hal_payload, dict) else None
        )
        target_ui = self._six_axis_deltas(hal_payload.get("targetUi")) if isinstance(hal_payload, dict) else None
        update_return = (
            self._six_axis_deltas(hal_payload.get("updateReturn")) if isinstance(hal_payload, dict) else None
        )
        stop_reason = self._six_axis_deltas(hal_payload.get("stopReason")) if isinstance(hal_payload, dict) else None
        axis_io_status = (
            self._six_axis_deltas(hal_payload.get("axisIoStatus")) if isinstance(hal_payload, dict) else None
        )
        moving_before = (
            self._six_axis_bools(hal_payload.get("movingBefore")) if isinstance(hal_payload, dict) else None
        )
        move_started = (
            self._six_axis_bools(hal_payload.get("moveStarted")) if isinstance(hal_payload, dict) else None
        )
        clipped = self._six_axis_bools(hal_payload.get("clipped")) if isinstance(hal_payload, dict) else None
        dominant_index = max(range(len(applied_deltas)), key=lambda idx: abs(applied_deltas[idx]))
        delta_vector = [0.0] * 12
        offset = 0 if target_side == "left" else 6
        for idx, delta in enumerate(applied_deltas):
            delta_vector[offset + idx] = delta
        action_monotonic_s = time.monotonic()
        action = {
            "ts": now_ms(),
            "monotonicMs": int(action_monotonic_s * 1000),
            "monotonic_s": action_monotonic_s,
            "side": target_side,
            "sourceSide": side,
            "axis": AXES[dominant_index],
            "delta": applied_deltas[dominant_index],
            "unit": "um" if dominant_index < 3 else "deg",
            "deltas": {axis: applied_deltas[idx] for idx, axis in enumerate(AXES)},
            "requestedDeltas": payload["deltas"],
            "appliedDeltas": {axis: applied_deltas[idx] for idx, axis in enumerate(AXES)},
            "requestedPulseDeltas": {axis: requested_pulse_deltas[idx] for idx, axis in enumerate(AXES)},
            "halRequestedPulseDeltas": (
                {axis: requested_delta_pulse[idx] for idx, axis in enumerate(AXES)}
                if requested_delta_pulse is not None
                else None
            ),
            "halAppliedPulseDeltas": (
                {axis: applied_delta_pulse[idx] for idx, axis in enumerate(AXES)}
                if applied_delta_pulse is not None
                else None
            ),
            "targetPulse": (
                {axis: target_pulse[idx] for idx, axis in enumerate(AXES)} if target_pulse is not None else None
            ),
            "currentPulse": (
                {axis: current_pulse[idx] for idx, axis in enumerate(AXES)} if current_pulse is not None else None
            ),
            "launchDeltaPulse": (
                {axis: launch_delta_pulse[idx] for idx, axis in enumerate(AXES)}
                if launch_delta_pulse is not None
                else None
            ),
            "targetUi": {axis: target_ui[idx] for idx, axis in enumerate(AXES)} if target_ui is not None else None,
            "updateReturn": (
                {axis: update_return[idx] for idx, axis in enumerate(AXES)}
                if update_return is not None
                else None
            ),
            "stopReason": (
                {axis: stop_reason[idx] for idx, axis in enumerate(AXES)}
                if stop_reason is not None
                else None
            ),
            "axisIoStatus": (
                {axis: axis_io_status[idx] for idx, axis in enumerate(AXES)}
                if axis_io_status is not None
                else None
            ),
            "movingBefore": (
                {axis: moving_before[idx] for idx, axis in enumerate(AXES)} if moving_before is not None else None
            ),
            "moveStarted": (
                {axis: move_started[idx] for idx, axis in enumerate(AXES)} if move_started is not None else None
            ),
            "clipped": {axis: clipped[idx] for idx, axis in enumerate(AXES)} if clipped is not None else None,
            "deltaVector": delta_vector,
            "commandLatencyMs": command_latency_ms,
        }
        self._last_action = action
        self._action_history.append(action)
        self._log_diag_action(config, action)

    def _applied_deltas_from_hal_result(self, hal_result: dict[str, Any], fallback: list[float]) -> list[float]:
        if isinstance(hal_result, dict):
            response = hal_result.get("response")
            candidates = [response, hal_result] if isinstance(response, dict) else [hal_result]
            for candidate in candidates:
                parsed = self._six_axis_deltas(candidate.get("appliedDeltas") if isinstance(candidate, dict) else None)
                if parsed is not None:
                    return parsed
        return list(fallback)

    def _six_axis_deltas(self, raw: Any) -> list[float] | None:
        if isinstance(raw, list) and len(raw) >= 6:
            try:
                return [float(raw[index]) for index in range(6)]
            except (TypeError, ValueError):
                return None
        if isinstance(raw, dict):
            try:
                return [float(raw[axis]) for axis in AXES]
            except (KeyError, TypeError, ValueError):
                return None
        return None

    def _six_axis_bools(self, raw: Any) -> list[bool] | None:
        if isinstance(raw, list) and len(raw) >= 6:
            return [bool(raw[index]) for index in range(6)]
        if isinstance(raw, dict):
            try:
                return [bool(raw[axis]) for axis in AXES]
            except KeyError:
                return None
        return None

    def _axis_dict_from_six(self, raw: Any) -> dict[str, float] | None:
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        values = self._six_axis_deltas(raw)
        if values is None:
            return None
        return {axis: values[index] for index, axis in enumerate(AXES)}

    def _axis_bool_dict_from_six(self, raw: Any) -> dict[str, bool] | None:
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        values = self._six_axis_bools(raw)
        if values is None:
            return None
        return {axis: values[index] for index, axis in enumerate(AXES)}

    def _hal_response_payload(self, hal_result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(hal_result, dict):
            return {}
        response = hal_result.get("response")
        if isinstance(response, dict):
            return response
        return hal_result

    def _log_diag_action(self, config: dict[str, Any], action: dict[str, Any]) -> None:
        if self.logs is None or not bool(config.get("teleop", {}).get("diagLog", False)):
            return
        requested = action.get("requestedDeltas")
        applied = action.get("appliedDeltas")
        requested_pulse = action.get("halRequestedPulseDeltas") or action.get("requestedPulseDeltas")
        applied_pulse = action.get("halAppliedPulseDeltas")
        target_pulse = action.get("targetPulse")
        current_pulse = action.get("currentPulse")
        launch_pulse = action.get("launchDeltaPulse")
        update_return = action.get("updateReturn")
        stop_reason = action.get("stopReason")
        axis_io_status = action.get("axisIoStatus")
        moving_before = action.get("movingBefore")
        move_started = action.get("moveStarted")
        clipped = action.get("clipped")
        has_motion = self._axis_dict_has_motion(requested) or self._axis_dict_has_motion(applied)
        has_clip = isinstance(clipped, dict) and any(bool(clipped.get(axis)) for axis in AXES)
        now = now_ms()
        zero_key = f"{action.get('sourceSide')}->{action.get('side')}"
        if not has_motion and not has_clip:
            last_zero_ms = self._last_diag_zero_log_ms.get(zero_key, 0)
            if now - last_zero_ms < 1000:
                return
            self._last_diag_zero_log_ms[zero_key] = now
        clip_axes = (
            ",".join(axis for axis in AXES if isinstance(clipped, dict) and bool(clipped.get(axis)))
            or "-"
        )
        latency = float(action.get("commandLatencyMs", 0.0))
        self.logs.event(
            "[HAL]",
            "INFO",
            "teleop_status",
            component="TELEOP",
            rate_key=f"teleop_status:{action.get('sourceSide')}->{action.get('side')}",
            rate_ms=1000 if not has_motion and not has_clip else None,
            sideMap=f"{action.get('sourceSide')}->{action.get('side')}",
            refState="active",
            blockReason="-",
            axis=action.get("axis"),
            raw=self._format_axis_values(requested),
            filtered=self._format_axis_values(applied),
            reqPulse=self._format_axis_values(requested_pulse),
            emitPulse=self._format_axis_values(applied_pulse or requested_pulse),
            targetPulse=self._format_axis_values(target_pulse),
            currentPulse=self._format_axis_values(current_pulse),
            limit="payload",
            clip=clip_axes,
            updateRet=self._format_axis_values(update_return),
            stopReason=self._format_axis_values(stop_reason),
            axisIoStatus=self._format_axis_values(axis_io_status),
            lastError=self._last_error or "",
            latencyMs=round(latency, 3),
        )
        self.logs.info(
            "[HAL]",
            (
                f"teleop diag {action.get('sourceSide')}->{action.get('side')} "
                f"axis={action.get('axis')} clip={clip_axes} latency={latency:.1f}ms "
                f"req={self._format_axis_values(requested)} "
                f"app={self._format_axis_values(applied)} "
                f"pulseReq={self._format_axis_values(requested_pulse)} "
                f"pulseApp={self._format_axis_values(applied_pulse)} "
                f"targetPulse={self._format_axis_values(target_pulse)} "
                f"currentPulse={self._format_axis_values(current_pulse)} "
                f"launchPulse={self._format_axis_values(launch_pulse)} "
                f"movingBefore={self._format_axis_flags(moving_before)} "
                f"moveStarted={self._format_axis_flags(move_started)} "
                f"updateRet={self._format_axis_values(update_return)} "
                f"stopReason={self._format_axis_values(stop_reason)} "
                f"axisIoStatus={self._format_axis_values(axis_io_status)}"
            ),
        )

    def _log_native_diag_action(
        self,
        config: dict[str, Any],
        action: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not bool(config.get("teleop", {}).get("diagLog", False)):
            return
        action_key = self._native_diag_action_key(action)
        if action_key == self._last_native_diag_action_key:
            return
        self._last_native_diag_action_key = action_key
        diag_action = dict(action)
        diag_action["requestedDeltas"] = self._axis_dict_from_six(
            action.get("requestedDeltas") or action.get("deltas")
        )
        diag_action["appliedDeltas"] = self._axis_dict_from_six(
            action.get("appliedDeltas") or action.get("deltas")
        )
        diag_action["halRequestedPulseDeltas"] = self._axis_dict_from_six(
            action.get("halRequestedPulseDeltas")
            or action.get("requestedDeltaPulse")
            or action.get("requestedPulseDeltas")
        )
        diag_action["halAppliedPulseDeltas"] = self._axis_dict_from_six(
            action.get("halAppliedPulseDeltas") or action.get("appliedDeltaPulse")
        )
        diag_action["targetPulse"] = self._axis_dict_from_six(action.get("targetPulse"))
        diag_action["currentPulse"] = self._axis_dict_from_six(action.get("currentPulse"))
        diag_action["launchDeltaPulse"] = self._axis_dict_from_six(action.get("launchDeltaPulse"))
        diag_action["updateReturn"] = self._axis_dict_from_six(action.get("updateReturn"))
        diag_action["stopReason"] = self._axis_dict_from_six(action.get("stopReason"))
        diag_action["axisIoStatus"] = self._axis_dict_from_six(action.get("axisIoStatus"))
        diag_action["movingBefore"] = self._axis_bool_dict_from_six(action.get("movingBefore"))
        diag_action["moveStarted"] = self._axis_bool_dict_from_six(action.get("moveStarted"))
        diag_action["clipped"] = self._axis_bool_dict_from_six(action.get("clipped"))
        diag_action.setdefault("commandLatencyMs", 0.0)
        self._log_diag_action(config, diag_action)
        self._log_native_axis_trace(config, diag_action, payload if isinstance(payload, dict) else {})

    def _log_native_axis_trace(
        self,
        config: dict[str, Any],
        action: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        if self.logs is None or not bool(config.get("teleop", {}).get("diagLog", False)):
            return
        source = str(action.get("sourceSide") or "?")
        target = str(action.get("side") or "?")
        input_detail = self._native_input_detail(payload, source)
        blockers = payload.get("blockers")
        block = blockers.get(source, {}) if isinstance(blockers, dict) else {}
        raw_pose = self._axis_dict_from_candidates(
            input_detail.get("semanticPose"),
            input_detail.get("rawPose"),
            input_detail.get("currentPose"),
            input_detail.get("pose"),
        )
        ref_pose = self._axis_dict_from_candidates(input_detail.get("referencePose"), input_detail.get("refPose"))
        raw_delta = self._axis_dict_from_candidates(input_detail.get("rawDelta"), action.get("requestedDeltas"))
        filtered_delta = self._axis_dict_from_candidates(input_detail.get("filteredDelta"), action.get("appliedDeltas"))
        output_delta = self._axis_dict_from_candidates(input_detail.get("outputDeltaUi"), action.get("appliedDeltas"))
        requested_pulse = self._axis_dict_from_candidates(
            input_detail.get("requestedPulse"),
            action.get("halRequestedPulseDeltas"),
            action.get("requestedPulseDeltas"),
        )
        emitted_pulse = self._axis_dict_from_candidates(
            input_detail.get("emittedPulse"),
            action.get("halAppliedPulseDeltas"),
            action.get("halRequestedPulseDeltas"),
            action.get("requestedPulseDeltas"),
        )
        latency = float(action.get("commandLatencyMs", 0.0))
        self.logs.event(
            "[HAL]",
            "INFO",
            "teleop_axis_trace",
            component="TELEOP",
            source=source,
            target=target,
            sideMap=f"{source}->{target}",
            axis=action.get("axis"),
            rawPose=self._format_axis_values(raw_pose),
            refPose=self._format_axis_values(ref_pose),
            rawDelta=self._format_axis_values(raw_delta),
            filteredDelta=self._format_axis_values(filtered_delta),
            outputDelta=self._format_axis_values(output_delta),
            requestedPulse=self._format_axis_values(requested_pulse),
            emitPulse=self._format_axis_values(emitted_pulse),
            currentPulse=self._format_axis_values(action.get("currentPulse")),
            targetPulse=self._format_axis_values(action.get("targetPulse")),
            launchPulse=self._format_axis_values(action.get("launchDeltaPulse")),
            movingBefore=self._format_axis_flags(action.get("movingBefore")),
            moveStarted=self._format_axis_flags(action.get("moveStarted")),
            clipped=self._format_axis_flags(action.get("clipped")),
            updateRet=self._format_axis_values(action.get("updateReturn")),
            stopReason=self._format_axis_values(action.get("stopReason")),
            axisIoStatus=self._format_axis_values(action.get("axisIoStatus")),
            blockReason=self._native_block_reason(block),
            referenceValid=bool(input_detail.get("referenceValid", False)),
            inputActive=bool(input_detail.get("inputActive", False)),
            lastError=str(payload.get("lastError") or self._last_error or ""),
            latencyMs=round(latency, 3),
        )

    def _native_input_detail(self, payload: dict[str, Any], source: str) -> dict[str, Any]:
        inputs = payload.get("inputs")
        if not isinstance(inputs, dict):
            return {}
        detail = inputs.get(source)
        return detail if isinstance(detail, dict) else {}

    def _axis_dict_from_candidates(self, *raw_values: Any) -> dict[str, float] | None:
        for raw in raw_values:
            parsed = self._axis_dict_from_six(raw)
            if parsed is not None:
                return parsed
        return None

    def _native_block_reason(self, block: Any) -> str:
        if not isinstance(block, dict):
            return "-"
        state = str(block.get("state", "") or "")
        message = str(block.get("message", "") or "")
        if state and message:
            return f"{state}:{message}"
        return state or message or "-"

    def _log_native_status_summary(self, config: dict[str, Any], payload: dict[str, Any]) -> None:
        if self.logs is None or not isinstance(payload, dict) or not bool(payload.get("running", False)):
            return
        summary = self._native_status_summary(payload)
        if not summary:
            return
        now = now_ms()
        if (
            self._last_native_status_summary_ms > 0
            and now - self._last_native_status_summary_ms < NATIVE_STATUS_SUMMARY_LOG_INTERVAL_MS
        ):
            return
        self._last_native_status_summary = summary
        self._last_native_status_summary_ms = now
        self._log_native_status_events(payload)
        self.logs.info("[HAL]", summary)

    def _log_teleop_mode(
        self,
        config: dict[str, Any],
        op_id: str | None,
        source: str,
        action: str,
        *,
        native: bool,
    ) -> None:
        if self.logs is None:
            return
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        command_interval_ms = round(self._command_interval_s(config) * 1000, 3)
        self.logs.event(
            "[HAL]",
            "INFO",
            "teleop_mode",
            component="TELEOP",
            op_id=op_id,
            source=source,
            action=action,
            controlMode=teleop.get("controlMode", ""),
            inputIntervalMs=command_interval_ms,
            commandIntervalMs=command_interval_ms,
            deviceCount="unknown",
            mappingMode=teleop.get("mappingMode", ""),
            swapTeleopChannels=teleop.get("swapTeleopChannels", False),
            localStabilityMode=teleop.get("stabilityMode", ""),
            zeroForceHold=teleop.get("zeroForceHold", False),
            scales={
                "left": teleop.get("leftAxisOutputScale", []),
                "right": teleop.get("rightAxisOutputScale", []),
            },
            deadzones={
                "translation": teleop.get("translationDeadzone", ""),
                "rotation": teleop.get("rotationDeadzone", ""),
            },
            stepLimits={
                "translationPulse": teleop.get("translationStepLimitPulse", ""),
                "rotationPulse": teleop.get("rotationStepLimitPulse", ""),
            },
            engine="hal_native" if native else "python",
        )

    def _log_teleop_profiles(self, config: dict[str, Any], op_id: str | None) -> None:
        if self.logs is None:
            return
        motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
        for side in ("left", "right"):
            axis_map = self._physical_axis_map(side, config)
            for index, axis in enumerate(AXES):
                rotation = index >= 3
                self.logs.event(
                    "[HAL]",
                    "INFO",
                    "teleop_profile",
                    component="TELEOP",
                    op_id=op_id,
                    axis=f"{side}.{axis}",
                    side=side,
                    card=motion.get(f"{side}CardNo", 1 if side == "left" else 0),
                    physicalAxis=axis_map[index],
                    pulsePerUnit=self._pulse_per_unit(side, axis, config),
                    startSpeed=(
                        self._rotation_start_velocity_deg_s(config)
                        if rotation
                        else self._translation_start_velocity_um_s(config)
                    ),
                    maxSpeed=(
                        self._rotation_max_velocity_deg_s(config)
                        if rotation
                        else self._translation_max_velocity_um_s(config)
                    ),
                    acc=self._motion_profile_acc_sec(config),
                    dec=self._motion_profile_dec_sec(config),
                    profileRet="not_available",
                    sProfileRet="not_available",
                )

    def _log_native_status_events(self, payload: dict[str, Any]) -> None:
        if self.logs is None:
            return
        inputs = payload.get("inputs")
        blockers = payload.get("blockers")
        if not isinstance(inputs, dict):
            return
        for source_side in ("left", "right"):
            detail = inputs.get(source_side)
            if not isinstance(detail, dict):
                continue
            target_side = str(detail.get("targetSide") or "?")
            block = blockers.get(source_side, {}) if isinstance(blockers, dict) else {}
            self.logs.event(
                "[HAL]",
                "INFO",
                "teleop_status",
                component="TELEOP",
                sideMap=f"{source_side}->{target_side}",
                refState="ref" if bool(detail.get("referenceValid")) else "no-ref",
                blockReason=str(block.get("state", "-")) if isinstance(block, dict) else "-",
                referenceValid=bool(detail.get("referenceValid", False)),
                inputActive=bool(detail.get("inputActive", False)),
                rawPose=self._format_axis_values(
                    self._axis_dict_from_candidates(
                        detail.get("semanticPose"),
                        detail.get("rawPose"),
                        detail.get("currentPose"),
                        detail.get("pose"),
                    )
                ),
                refPose=self._format_axis_values(
                    self._axis_dict_from_candidates(detail.get("referencePose"), detail.get("refPose"))
                ),
                raw=self._format_axis_values(self._axis_dict_from_six(detail.get("rawDelta"))),
                filteredDelta=self._format_axis_values(self._axis_dict_from_six(detail.get("filteredDelta"))),
                filtered=self._format_axis_values(self._axis_dict_from_six(detail.get("outputDeltaUi"))),
                outputDelta=self._format_axis_values(self._axis_dict_from_six(detail.get("outputDeltaUi"))),
                reqPulse=self._format_axis_values(self._axis_dict_from_six(detail.get("requestedPulse"))),
                emitPulse=self._format_axis_values(self._axis_dict_from_six(detail.get("emittedPulse"))),
                targetPulse="-",
                currentPulse="-",
                limit="native",
                clip="-",
                updateRet="-",
                lastError=str(payload.get("lastError") or ""),
                latencyMs=0,
            )

    def _native_status_summary(self, payload: dict[str, Any]) -> str:
        inputs = payload.get("inputs")
        blockers = payload.get("blockers")
        parts: list[str] = []
        if isinstance(inputs, dict):
            for source_side in ("left", "right"):
                detail = inputs.get(source_side)
                if not isinstance(detail, dict):
                    continue
                target_side = str(detail.get("targetSide") or "?")
                block = blockers.get(source_side, {}) if isinstance(blockers, dict) else {}
                parts.append(self._format_native_input_summary(source_side, target_side, detail, block))
        last_action_summary = self._format_native_last_action_summary(payload.get("lastAction"))
        if last_action_summary:
            parts.append(last_action_summary)
        gripper_summary = self._format_native_gripper_summary(payload.get("grippers"))
        if gripper_summary:
            parts.append(gripper_summary)
        error = payload.get("lastError")
        if isinstance(error, str) and error:
            parts.append(f"lastError={error}")
        return "native status " + " | ".join(parts) if parts else ""

    def _format_native_input_summary(
        self,
        source_side: str,
        target_side: str,
        detail: dict[str, Any],
        block: Any,
    ) -> str:
        raw = self._axis_dict_from_six(detail.get("rawDelta"))
        requested = self._axis_dict_from_six(detail.get("requestedPulse"))
        emitted = self._axis_dict_from_six(detail.get("emittedPulse"))
        output = self._axis_dict_from_six(detail.get("outputDeltaUi"))
        reference = "ref" if bool(detail.get("referenceValid")) else "no-ref"
        input_state = "input" if bool(detail.get("inputActive")) else "idle"
        block_text = ""
        if isinstance(block, dict):
            state = str(block.get("state", "") or "")
            message = str(block.get("message", "") or "")
            if state or message:
                block_text = f" block={state}{':' + message if message else ''}"
        return (
            f"{source_side}->{target_side} {reference}/{input_state}{block_text} "
            f"raw={self._format_axis_values(raw)} "
            f"reqPulse={self._format_axis_values(requested)} "
            f"emitPulse={self._format_axis_values(emitted)} "
            f"out={self._format_axis_values(output)}"
        )

    def _format_native_last_action_summary(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        source = str(raw.get("sourceSide") or "?")
        side = str(raw.get("side") or "?")
        launch = self._axis_dict_from_six(raw.get("launchDeltaPulse"))
        current_pulse = self._axis_dict_from_six(raw.get("currentPulse"))
        target_pulse = self._axis_dict_from_six(raw.get("targetPulse"))
        update_return = self._axis_dict_from_six(raw.get("updateReturn"))
        stop_reason = self._axis_dict_from_six(raw.get("stopReason"))
        axis_io_status = self._axis_dict_from_six(raw.get("axisIoStatus"))
        move_started = self._axis_bool_dict_from_six(raw.get("moveStarted"))
        moving_before = self._axis_bool_dict_from_six(raw.get("movingBefore"))
        clipped = self._axis_bool_dict_from_six(raw.get("clipped"))
        pieces = [
            f"last={source}->{side}",
            f"currentPulse={self._format_axis_values(current_pulse)}",
            f"targetPulse={self._format_axis_values(target_pulse)}",
            f"launchPulse={self._format_axis_values(launch)}",
            f"movingBefore={self._format_axis_flags(moving_before)}",
            f"moveStarted={self._format_axis_flags(move_started)}",
            f"clip={self._format_axis_flags(clipped)}",
            f"updateRet={self._format_axis_values(update_return)}",
            f"stopReason={self._format_axis_values(stop_reason)}",
            f"axisIoStatus={self._format_axis_values(axis_io_status)}",
        ]
        return " ".join(pieces)

    def _format_native_gripper_summary(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        parts: list[str] = []
        for side in ("left", "right"):
            detail = raw.get(side)
            if not isinstance(detail, dict):
                continue
            try:
                last_command_ts = int(detail.get("lastCommandTs", 0) or 0)
            except (TypeError, ValueError):
                last_command_ts = 0
            if last_command_ts <= 0:
                status = "IDLE"
            else:
                status = "OK" if bool(detail.get("ok", False)) else "ERR"
            try:
                target = float(detail.get("targetMm", 0.0))
            except (TypeError, ValueError):
                target = 0.0
            message = str(detail.get("message", "") or "")
            piece = f"{side}:{status} target={target:.4g}"
            if message:
                piece += f" {message}"
            parts.append(piece)
        return "grip=" + ";".join(parts) if parts else ""

    def _native_diag_action_key(self, action: dict[str, Any]) -> str:
        return "|".join(
            str(action.get(key, ""))
            for key in (
                "ts",
                "monotonicMs",
                "monotonic_s",
                "sourceSide",
                "side",
                "axis",
                "delta",
                "unit",
            )
        )

    def _axis_dict_has_motion(self, raw: Any) -> bool:
        if not isinstance(raw, dict):
            return False
        for axis in AXES:
            try:
                if abs(float(raw.get(axis, 0.0))) > 1e-9:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _format_axis_values(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return "-"
        parts: list[str] = []
        for axis in AXES:
            try:
                value = float(raw.get(axis, 0.0))
            except (TypeError, ValueError):
                continue
            if abs(value) > 1e-9:
                parts.append(f"{axis}:{value:.4g}")
        return "[" + ",".join(parts) + "]" if parts else "[0]"

    def _format_axis_flags(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return "-"
        parts = [f"{axis}:1" for axis in AXES if bool(raw.get(axis, False))]
        return "[" + ",".join(parts) + "]" if parts else "[]"

    def _deltas_from_delta(
        self,
        side: SideName,
        target_side: SideName,
        pose: list[float],
        reference: list[float],
        config: dict[str, Any],
        enabled_axes: list[bool],
    ) -> tuple[list[float], list[float]]:
        teleop = config.get("teleop", {})
        translation_key = f"{side}TranslationScale"
        rotation_key = f"{side}RotationScale"
        translation_scale = float(teleop.get(translation_key, ICF_TELEOP_DEFAULTS[translation_key]))
        rotation_scale = float(teleop.get(rotation_key, ICF_TELEOP_DEFAULTS[rotation_key]))
        axis_scale = self._axis_output_scale(target_side, config)
        impulse_coeff = self._impulse_coefficients(target_side, config)
        target_signed_pulse_per_unit = self._signed_pulse_per_unit(target_side, config)
        translation_deadzone_m = float(teleop.get("translationDeadzone", 0.00002))
        rotation_deadzone_deg = float(teleop.get("rotationDeadzone", ICF_TELEOP_DEFAULTS["rotationDeadzone"]))
        continuous_increment = self._continuous_increment_mode(config)
        deltas = [0.0] * 6
        requested_pulse_deltas = [0.0] * 6
        for idx in range(6):
            if not enabled_axes[idx]:
                if continuous_increment:
                    self._reset_continuous_axis(side, idx)
                continue
            raw_delta = pose[idx] - reference[idx]
            if idx < 3:
                filtered = self._filter_translation_delta(
                    side,
                    idx,
                    raw_delta,
                    translation_deadzone_m,
                    config,
                    continuous_increment,
                )
                if filtered == 0.0:
                    if continuous_increment:
                        self._reset_continuous_axis(side, idx)
                    continue
                output_scale = translation_scale * axis_scale[idx]
                deadband_pulse = self._translation_pulse_deadband(config)
                min_active_pulse = self._translation_min_active_pulse(config)
            else:
                filtered = self._filter_rotation_delta(raw_delta, rotation_deadzone_deg, config, continuous_increment)
                if filtered == 0.0:
                    if continuous_increment:
                        self._reset_continuous_axis(side, idx)
                    continue
                output_scale = rotation_scale * axis_scale[idx]
                deadband_pulse = self._rotation_pulse_deadband(config)
                min_active_pulse = self._rotation_min_active_pulse(config)
            impulse_float = filtered * impulse_coeff[idx]
            requested_pulse_float = impulse_float * output_scale
            impulse = self._llround(impulse_float)
            requested_pulse = self._llround(impulse * output_scale)
            if continuous_increment:
                requested_pulse = self._apply_continuous_pulse_gate(
                    side,
                    idx,
                    requested_pulse,
                    requested_pulse_float,
                    deadband_pulse,
                    min_active_pulse,
                    config,
                )
            requested_pulse_deltas[idx] = float(requested_pulse)
            deltas[idx] = self._pulse_delta_to_ui_delta(
                float(requested_pulse),
                idx,
                target_signed_pulse_per_unit[idx],
            )
        return deltas, requested_pulse_deltas

    def _filter_translation_delta(
        self,
        side: SideName,
        axis_index: int,
        raw_delta: float,
        deadzone_m: float,
        config: dict[str, Any],
        continuous_increment: bool,
    ) -> float:
        if continuous_increment:
            return raw_delta if abs(raw_delta) >= self._translation_input_epsilon_m(config) else 0.0
        return self._filter_incremental_translation(side, axis_index, raw_delta, deadzone_m, config)

    def _filter_rotation_delta(
        self,
        raw_delta: float,
        deadzone_deg: float,
        config: dict[str, Any],
        continuous_increment: bool,
    ) -> float:
        threshold = self._rotation_input_epsilon_deg(config) if continuous_increment else deadzone_deg
        return raw_delta if abs(raw_delta) >= threshold else 0.0

    def _apply_continuous_pulse_gate(
        self,
        side: SideName,
        axis_index: int,
        requested_pulse: int,
        requested_pulse_float: float,
        pulse_deadband: float,
        min_active_pulse: float,
        config: dict[str, Any],
    ) -> int:
        if requested_pulse_float == 0.0:
            self._reset_continuous_axis(side, axis_index)
            return 0
        sign = 1 if requested_pulse_float > 0 else -1
        minimum = self._minimum_active_pulse(pulse_deadband, min_active_pulse)
        directions = self._continuous_direction.setdefault(side, [0, 0, 0, 0, 0, 0])
        streaks = self._continuous_streak.setdefault(side, [0, 0, 0, 0, 0, 0])
        confirm_ticks = self._continuous_micro_confirm_ticks(config)

        if abs(requested_pulse) >= minimum:
            directions[axis_index] = sign
            streaks[axis_index] = confirm_ticks
            return requested_pulse
        if confirm_ticks <= 0:
            return 0

        if directions[axis_index] == sign:
            streaks[axis_index] += 1
        else:
            directions[axis_index] = sign
            streaks[axis_index] = 1
        if streaks[axis_index] < confirm_ticks:
            return 0
        return sign * minimum

    def _minimum_active_pulse(self, pulse_deadband: float, min_active_pulse: float) -> int:
        deadband_limit = int(math.floor(max(0.0, pulse_deadband)))
        return max(1, int(math.ceil(min_active_pulse)), deadband_limit + 1)

    def _reset_continuous_axis(self, side: SideName, axis_index: int) -> None:
        direction = self._continuous_direction.get(side)
        if direction is not None and 0 <= axis_index < len(direction):
            direction[axis_index] = 0
        streak = self._continuous_streak.get(side)
        if streak is not None and 0 <= axis_index < len(streak):
            streak[axis_index] = 0

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

    def _native_status_interval_s(self, config: dict[str, Any]) -> float:
        gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
        try:
            sample_hz = float(gripper.get("sampleHz", DEFAULT_NATIVE_STATUS_SAMPLE_HZ))
        except (TypeError, ValueError):
            sample_hz = DEFAULT_NATIVE_STATUS_SAMPLE_HZ
        sample_hz = min(max(sample_hz, 1.0), 60.0)
        return 1.0 / sample_hz

    def _native_engine(self, config: dict[str, Any]) -> bool:
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        return str(teleop.get("engine", "python_mapper")).lower() == "hal_native"

    def _native_payload(self, config: dict[str, Any]) -> dict[str, Any]:
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
        gripper_teleop = teleop.get("gripperTeleop", {}) if isinstance(teleop.get("gripperTeleop"), dict) else {}
        worker_timeout = gripper.get("workerCommandTimeoutMs")
        if worker_timeout is None:
            worker_timeout = float(gripper.get("workerCommandTimeoutSec", 2.0)) * 1000.0
        arm_motion_active = self._native_arm_motion_active()
        payload: dict[str, Any] = {
            "engine": str(teleop.get("engine", "hal_native")),
            "controlMode": str(teleop.get("controlMode", "incremental_position")),
            "mappingMode": str(teleop.get("mappingMode", ICF_TELEOP_DEFAULTS["mappingMode"])),
            "nativeLoopHz": int(float(teleop.get("nativeLoopHz", 100))),
            "nativeTranslationDeadzoneM": float(teleop.get("nativeTranslationDeadzoneM", 0.002)),
            "nativeTranslationFullScaleM": float(teleop.get("nativeTranslationFullScaleM", 0.04)),
            "nativeRotationDeadzoneDeg": float(teleop.get("nativeRotationDeadzoneDeg", 2.0)),
            "nativeRotationFullScaleDeg": float(teleop.get("nativeRotationFullScaleDeg", 30.0)),
            "nativeVelocitySmoothingMs": float(teleop.get("nativeVelocitySmoothingMs", 40.0)),
            "kalmanFilterEnabled": bool(teleop.get("kalmanFilterEnabled", False)),
            "kalmanBeta": float(teleop.get("kalmanBeta", ICF_TELEOP_DEFAULTS["kalmanBeta"])),
            "kalmanMinVariance": float(teleop.get("kalmanMinVariance", ICF_TELEOP_DEFAULTS["kalmanMinVariance"])),
            "kalmanMaxVariance": float(teleop.get("kalmanMaxVariance", ICF_TELEOP_DEFAULTS["kalmanMaxVariance"])),
            "kalmanDtMinSec": float(teleop.get("kalmanDtMinSec", ICF_TELEOP_DEFAULTS["kalmanDtMinSec"])),
            "kalmanDtMaxSec": float(teleop.get("kalmanDtMaxSec", ICF_TELEOP_DEFAULTS["kalmanDtMaxSec"])),
            "kalmanTranslationPositionVariance": float(
                teleop.get(
                    "kalmanTranslationPositionVariance",
                    ICF_TELEOP_DEFAULTS["kalmanTranslationPositionVariance"],
                )
            ),
            "kalmanTranslationVelocityVariance": float(
                teleop.get(
                    "kalmanTranslationVelocityVariance",
                    ICF_TELEOP_DEFAULTS["kalmanTranslationVelocityVariance"],
                )
            ),
            "kalmanTranslationMeasurementVariance": float(
                teleop.get(
                    "kalmanTranslationMeasurementVariance",
                    ICF_TELEOP_DEFAULTS["kalmanTranslationMeasurementVariance"],
                )
            ),
            "kalmanTranslationProcessPositionVariance": float(
                teleop.get(
                    "kalmanTranslationProcessPositionVariance",
                    ICF_TELEOP_DEFAULTS["kalmanTranslationProcessPositionVariance"],
                )
            ),
            "kalmanTranslationProcessVelocityVariance": float(
                teleop.get(
                    "kalmanTranslationProcessVelocityVariance",
                    ICF_TELEOP_DEFAULTS["kalmanTranslationProcessVelocityVariance"],
                )
            ),
            "kalmanRotationPositionVariance": float(
                teleop.get("kalmanRotationPositionVariance", ICF_TELEOP_DEFAULTS["kalmanRotationPositionVariance"])
            ),
            "kalmanRotationVelocityVariance": float(
                teleop.get("kalmanRotationVelocityVariance", ICF_TELEOP_DEFAULTS["kalmanRotationVelocityVariance"])
            ),
            "kalmanRotationMeasurementVariance": float(
                teleop.get(
                    "kalmanRotationMeasurementVariance",
                    ICF_TELEOP_DEFAULTS["kalmanRotationMeasurementVariance"],
                )
            ),
            "kalmanRotationProcessPositionVariance": float(
                teleop.get(
                    "kalmanRotationProcessPositionVariance",
                    ICF_TELEOP_DEFAULTS["kalmanRotationProcessPositionVariance"],
                )
            ),
            "kalmanRotationProcessVelocityVariance": float(
                teleop.get(
                    "kalmanRotationProcessVelocityVariance",
                    ICF_TELEOP_DEFAULTS["kalmanRotationProcessVelocityVariance"],
                )
            ),
            "kalmanTranslationIntentVelocityThreshold": float(
                teleop.get(
                    "kalmanTranslationIntentVelocityThreshold",
                    ICF_TELEOP_DEFAULTS["kalmanTranslationIntentVelocityThreshold"],
                )
            ),
            "kalmanRotationIntentVelocityThreshold": float(
                teleop.get(
                    "kalmanRotationIntentVelocityThreshold",
                    ICF_TELEOP_DEFAULTS["kalmanRotationIntentVelocityThreshold"],
                )
            ),
            "translationDeadzone": float(teleop.get("translationDeadzone", 0.00002)),
            "rotationDeadzone": float(teleop.get("rotationDeadzone", ICF_TELEOP_DEFAULTS["rotationDeadzone"])),
            "incrementalTranslationMinEffectiveDelta": self._incremental_translation_min_effective_delta(config),
            "incrementalTranslationReverseDeadzone": self._incremental_translation_reverse_deadzone(config),
            "continuousIncrementMode": self._continuous_increment_mode(config),
            "translationInputEpsilon": self._translation_input_epsilon_m(config),
            "rotationInputEpsilon": self._rotation_input_epsilon_deg(config),
            "translationMinActivePulse": self._translation_min_active_pulse(config),
            "rotationMinActivePulse": self._rotation_min_active_pulse(config),
            "continuousMicroConfirmTicks": self._continuous_micro_confirm_ticks(config),
            "leftConnected": arm_motion_active and bool(teleop.get("leftConnected", False)),
            "rightConnected": arm_motion_active and bool(teleop.get("rightConnected", False)),
            "swapTeleopChannels": bool(teleop.get("swapTeleopChannels", True)),
            "requireClutch": bool(teleop.get("requireClutch", False)),
            "leftGravityCompensation": bool(teleop.get("leftGravityCompensation", True)),
            "rightGravityCompensation": bool(teleop.get("rightGravityCompensation", True)),
            "leftGravityScale": float(teleop.get("leftGravityScale", ICF_TELEOP_DEFAULTS["leftGravityScale"])),
            "rightGravityScale": float(teleop.get("rightGravityScale", ICF_TELEOP_DEFAULTS["rightGravityScale"])),
            "leftTranslationScale": float(
                teleop.get("leftTranslationScale", ICF_TELEOP_DEFAULTS["leftTranslationScale"])
            ),
            "rightTranslationScale": float(
                teleop.get("rightTranslationScale", ICF_TELEOP_DEFAULTS["rightTranslationScale"])
            ),
            "leftRotationScale": float(
                teleop.get("leftRotationScale", ICF_TELEOP_DEFAULTS["leftRotationScale"])
            ),
            "rightRotationScale": float(
                teleop.get("rightRotationScale", ICF_TELEOP_DEFAULTS["rightRotationScale"])
            ),
            "leftAxisOutputScale": self._axis_output_scale("left", config),
            "rightAxisOutputScale": self._axis_output_scale("right", config),
            "leftImpulseCoeff": self._impulse_coefficients("left", config),
            "rightImpulseCoeff": self._impulse_coefficients("right", config),
            "leftEnabledAxes": self._enabled_axes("left", config),
            "rightEnabledAxes": self._enabled_axes("right", config),
            "leftSoftLimitMin": self._soft_limit_arrays("left", config)[0],
            "leftSoftLimitMax": self._soft_limit_arrays("left", config)[1],
            "rightSoftLimitMin": self._soft_limit_arrays("right", config)[0],
            "rightSoftLimitMax": self._soft_limit_arrays("right", config)[1],
            "rotationWorkLimitEnabled": rotation_work_limit_enabled(config),
            "leftRotationWorkLimitMin": self._rotation_work_limit_arrays("left", config)[0],
            "leftRotationWorkLimitMax": self._rotation_work_limit_arrays("left", config)[1],
            "rightRotationWorkLimitMin": self._rotation_work_limit_arrays("right", config)[0],
            "rightRotationWorkLimitMax": self._rotation_work_limit_arrays("right", config)[1],
            "leftWorkOriginValid": self._work_origin_valid("left", config),
            "rightWorkOriginValid": self._work_origin_valid("right", config),
            "leftWorkOriginPulse": self._work_origin_pulse("left", config),
            "rightWorkOriginPulse": self._work_origin_pulse("right", config),
            "leftHomeReferenceValid": self._home_reference_valid("left", config),
            "rightHomeReferenceValid": self._home_reference_valid("right", config),
            "leftHomeReferencePulse": self._home_reference_pulse("left", config),
            "rightHomeReferencePulse": self._home_reference_pulse("right", config),
            "translationStepLimitPulse": self._translation_step_limit_pulse(config),
            "rotationStepLimitPulse": self._rotation_step_limit_pulse(config),
            "translationPulseDeadband": self._translation_pulse_deadband(config),
            "rotationPulseDeadband": self._rotation_pulse_deadband(config),
            "translationStartVelocityUmS": self._translation_start_velocity_um_s(config),
            "translationMaxVelocityUmS": self._translation_max_velocity_um_s(config),
            "rotationStartVelocityDegS": self._rotation_start_velocity_deg_s(config),
            "rotationMaxVelocityDegS": self._rotation_max_velocity_deg_s(config),
            "motionProfileAccSec": self._motion_profile_acc_sec(config),
            "motionProfileDecSec": self._motion_profile_dec_sec(config),
            "gripperTeleopEnabled": self._native_gripper_teleop_enabled(config),
            "leftPort": str(gripper.get("leftPort", "COM8")),
            "rightPort": str(gripper.get("rightPort", "COM9")),
            "leftSlaveId": int(gripper.get("leftSlaveId", 10)),
            "rightSlaveId": int(gripper.get("rightSlaveId", 9)),
            "baudrate": int(gripper.get("baudrate", 115200)),
            "strokeMm": float(gripper.get("strokeMm", 26)),
            "icfTargetProtectionEnabled": bool(gripper.get("icfTargetProtectionEnabled", True)),
            "icfTargetMinGapMm": float(gripper.get("icfTargetMinGapMm", 1.02)),
            "jodellDllPath": str(gripper.get("jodellDllPath", "")),
            "gripperProcessWorkersEnabled": bool(gripper.get("processWorkersEnabled", True)),
            "jodellWorkerExePath": str(gripper.get("jodellWorkerExePath", "")),
            "gripperWorkerCommandTimeoutMs": float(worker_timeout),
            "leftGapMinMm": float(gripper_teleop.get("leftGapMinMm", 0.0)),
            "leftGapMaxMm": float(gripper_teleop.get("leftGapMaxMm", 25.0)),
            "rightGapMinMm": float(gripper_teleop.get("rightGapMinMm", 0.0)),
            "rightGapMaxMm": float(gripper_teleop.get("rightGapMaxMm", 25.0)),
            "leftGapInvert": bool(gripper_teleop.get("leftGapInvert", False)),
            "rightGapInvert": bool(gripper_teleop.get("rightGapInvert", False)),
            "leftSourceHand": str(gripper_teleop.get("leftSourceHand", "PhysicalLeft")),
            "rightSourceHand": str(gripper_teleop.get("rightSourceHand", "PhysicalRight")),
            "gripSpeed": int(gripper_teleop.get("gripSpeed", 255)),
            "gripTorque": int(gripper_teleop.get("gripTorque", 1)),
            "positionDeadbandCounts": int(gripper_teleop.get("positionDeadbandCounts", 1)),
            "minCommandIntervalMs": float(gripper_teleop.get("minCommandIntervalMs", 20)),
            "buttonFallback": bool(gripper_teleop.get("buttonFallback", True)),
        }
        return payload

    def _work_origin_valid(self, side: SideName, config: dict[str, Any]) -> bool:
        origin = self._normalized_motion_origin(config)
        return bool(origin["leftValid" if side == "left" else "rightValid"])

    def _home_reference_valid(self, side: SideName, config: dict[str, Any]) -> bool:
        reference = self._normalized_home_reference(config)
        return bool(reference["leftValid" if side == "left" else "rightValid"])

    def _native_gripper_teleop_enabled(self, config: dict[str, Any]) -> bool:
        _ = config
        return False

    def _work_origin_pulse(self, side: SideName, config: dict[str, Any]) -> list[float]:
        origin = self._normalized_motion_origin(config)
        pulses = origin["leftPulse" if side == "left" else "rightPulse"]
        return [float(value) for value in pulses] if isinstance(pulses, list) else [0.0] * 6

    def _home_reference_pulse(self, side: SideName, config: dict[str, Any]) -> list[float]:
        reference = self._normalized_home_reference(config)
        pulses = reference["leftPulse" if side == "left" else "rightPulse"]
        return [float(value) for value in pulses] if isinstance(pulses, list) else [0.0] * 6

    def _translation_step_um(self, config: dict[str, Any]) -> float:
        return max(0.0, float(config.get("teleop", {}).get("translationStepUm", DEFAULT_TRANSLATION_STEP_UM)))

    def _rotation_step_deg(self, config: dict[str, Any]) -> float:
        return max(0.0, float(config.get("teleop", {}).get("rotationStepDeg", DEFAULT_ROTATION_STEP_DEG)))

    def _translation_step_limit_pulse(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("translationStepLimitPulse", 4000.0)))

    def _rotation_step_limit_pulse(self, config: dict[str, Any]) -> float:
        return max(1.0, float(config.get("teleop", {}).get("rotationStepLimitPulse", 1250.0)))

    def _translation_pulse_deadband(self, config: dict[str, Any]) -> float:
        return max(
            0.0,
            float(
                config.get("teleop", {}).get(
                    "translationPulseDeadband",
                    ICF_TELEOP_DEFAULTS["translationPulseDeadband"],
                )
            ),
        )

    def _rotation_pulse_deadband(self, config: dict[str, Any]) -> float:
        return max(
            0.0,
            float(
                config.get("teleop", {}).get(
                    "rotationPulseDeadband",
                    ICF_TELEOP_DEFAULTS["rotationPulseDeadband"],
                )
            ),
        )

    def _translation_start_velocity_um_s(self, config: dict[str, Any]) -> float:
        return max(
            0.0,
            float(
                config.get("teleop", {}).get(
                    "translationStartVelocityUmS",
                    ICF_TELEOP_DEFAULTS["translationStartVelocityUmS"],
                )
            ),
        )

    def _translation_max_velocity_um_s(self, config: dict[str, Any]) -> float:
        return max(
            1.0,
            float(
                config.get("teleop", {}).get(
                    "translationMaxVelocityUmS",
                    ICF_TELEOP_DEFAULTS["translationMaxVelocityUmS"],
                )
            ),
        )

    def _rotation_start_velocity_deg_s(self, config: dict[str, Any]) -> float:
        return max(
            0.0,
            float(
                config.get("teleop", {}).get(
                    "rotationStartVelocityDegS",
                    ICF_TELEOP_DEFAULTS["rotationStartVelocityDegS"],
                )
            ),
        )

    def _rotation_max_velocity_deg_s(self, config: dict[str, Any]) -> float:
        return max(
            1.0,
            float(
                config.get("teleop", {}).get(
                    "rotationMaxVelocityDegS",
                    ICF_TELEOP_DEFAULTS["rotationMaxVelocityDegS"],
                )
            ),
        )

    def _continuous_increment_mode(self, config: dict[str, Any]) -> bool:
        return bool(
            config.get("teleop", {}).get(
                "continuousIncrementMode",
                ICF_TELEOP_DEFAULTS["continuousIncrementMode"],
            )
        )

    def _continuous_micro_confirm_ticks(self, config: dict[str, Any]) -> int:
        return max(
            0,
            int(
                math.ceil(
                    float(
                        config.get("teleop", {}).get(
                            "continuousMicroConfirmTicks",
                            ICF_TELEOP_DEFAULTS["continuousMicroConfirmTicks"],
                        )
                    )
                )
            ),
        )

    def _translation_input_epsilon_m(self, config: dict[str, Any]) -> float:
        return max(
            0.0,
            float(
                config.get("teleop", {}).get(
                    "translationInputEpsilon",
                    ICF_TELEOP_DEFAULTS["translationInputEpsilon"],
                )
            ),
        )

    def _rotation_input_epsilon_deg(self, config: dict[str, Any]) -> float:
        return max(
            0.0,
            float(
                config.get("teleop", {}).get(
                    "rotationInputEpsilon",
                    ICF_TELEOP_DEFAULTS["rotationInputEpsilon"],
                )
            ),
        )

    def _translation_min_active_pulse(self, config: dict[str, Any]) -> float:
        return max(
            1.0,
            float(
                config.get("teleop", {}).get(
                    "translationMinActivePulse",
                    ICF_TELEOP_DEFAULTS["translationMinActivePulse"],
                )
            ),
        )

    def _rotation_min_active_pulse(self, config: dict[str, Any]) -> float:
        return max(
            1.0,
            float(
                config.get("teleop", {}).get(
                    "rotationMinActivePulse",
                    ICF_TELEOP_DEFAULTS["rotationMinActivePulse"],
                )
            ),
        )

    def _motion_profile_acc_sec(self, config: dict[str, Any]) -> float:
        return max(
            0.001,
            float(config.get("teleop", {}).get("motionProfileAccSec", ICF_TELEOP_DEFAULTS["motionProfileAccSec"])),
        )

    def _motion_profile_dec_sec(self, config: dict[str, Any]) -> float:
        return max(
            0.001,
            float(config.get("teleop", {}).get("motionProfileDecSec", ICF_TELEOP_DEFAULTS["motionProfileDecSec"])),
        )

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

    def _impulse_coefficients(self, side: SideName, config: dict[str, Any]) -> list[float]:
        teleop = config.get("teleop", {})
        key = f"{side}ImpulseCoeff"
        raw = teleop.get(key) if isinstance(teleop, dict) else None
        if isinstance(raw, list) and len(raw) == 6:
            try:
                return [float(value) for value in raw]
            except (TypeError, ValueError):
                pass
        signed_pulse_per_unit = self._signed_pulse_per_unit(side, config)
        return [
            signed_pulse_per_unit[index] * 1000.0 if index < 3 else signed_pulse_per_unit[index]
            for index in range(6)
        ]

    def _signed_pulse_per_unit(self, side: SideName, config: dict[str, Any]) -> list[float]:
        motion = config.get("motion", {})
        kinematics = motion.get("kinematics", {}) if isinstance(motion, dict) else {}
        key = f"{side}SignedPulsePerUnit"
        raw = kinematics.get(key) if isinstance(kinematics, dict) else None
        if isinstance(raw, list) and len(raw) == 6:
            try:
                return [float(value) for value in raw]
            except (TypeError, ValueError):
                pass
        return [float(value) for value in ICF_KINEMATICS_DEFAULTS[key]]

    def _physical_axis_map(self, side: str, config: dict[str, Any]) -> list[int]:
        motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
        kinematics = motion.get("kinematics", {}) if isinstance(motion.get("kinematics"), dict) else {}
        key = f"{side}PhysicalAxis"
        legacy_key = f"{side}AxisMap"
        raw = kinematics.get(key, kinematics.get(legacy_key, ICF_KINEMATICS_DEFAULTS[key]))
        if not isinstance(raw, list) or len(raw) < 6:
            raw = ICF_KINEMATICS_DEFAULTS[key]
        try:
            return [int(raw[index]) for index in range(6)]
        except (TypeError, ValueError):
            return [int(value) for value in ICF_KINEMATICS_DEFAULTS[key]]

    def _pulse_per_unit(self, side: str, axis: str, config: dict[str, Any]) -> float:
        offset = 0 if side == "left" else 6
        return float(motion_pulse_per_unit(config)[offset + AXES.index(axis)])

    def _pulse_delta_to_ui_delta(self, pulse_delta: float, axis_index: int, signed_pulse_per_unit: float) -> float:
        if signed_pulse_per_unit == 0:
            return 0.0
        physical_delta = pulse_delta / signed_pulse_per_unit
        return physical_delta * 1000.0 if axis_index < 3 else physical_delta

    def _llround(self, value: float) -> int:
        return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))

    def _motion_card_no(self, side: SideName, config: dict[str, Any]) -> int:
        motion = config.get("motion", {})
        fallback = 1 if side == "left" else 0
        if not isinstance(motion, dict):
            return fallback
        try:
            return int(motion.get(f"{side}CardNo", fallback))
        except (TypeError, ValueError):
            return fallback

    def _enabled_axes(self, side: SideName, config: dict[str, Any]) -> list[bool]:
        key = f"{side}EnabledAxes"
        raw = config.get("teleop", {}).get(key, DEFAULT_ENABLED_AXES)
        if not isinstance(raw, list) or len(raw) != 6:
            raw = DEFAULT_ENABLED_AXES
        axes = [bool(value) for value in raw]
        if self._motion_card_no(side, config) == 0:
            axes[5] = False
        return axes

    def _motion_state_pulses(self, state: dict[str, Any]) -> list[float]:
        raw_pulses = state.get("pulses")
        if not isinstance(raw_pulses, list) or len(raw_pulses) != 12:
            raise RuntimeError("HAL motion state does not include 12 pulse values")
        return [float(value) for value in raw_pulses]

    def _soft_limit_arrays(self, side: SideName, config: dict[str, Any]) -> tuple[list[float], list[float]]:
        return native_teleop_limit_arrays(config, side)

    def _effective_limit_arrays(self, side: SideName, config: dict[str, Any]) -> tuple[list[float], list[float]]:
        try:
            return effective_limit_arrays(config, side)
        except WorkOriginMissing as exc:
            raise RuntimeError(str(exc)) from exc

    def _rotation_work_limit_arrays(self, side: SideName, config: dict[str, Any]) -> tuple[list[float], list[float]]:
        return rotation_work_limit_arrays(config, side)

    def _target_side_for_source(self, side: SideName, config: dict[str, Any]) -> SideName:
        teleop = config.get("teleop", {})
        if isinstance(teleop, dict) and bool(teleop.get("swapTeleopChannels", False)):
            return "right" if side == "left" else "left"
        return side

    def _set_all_blocked(self, config: dict[str, Any], reasons: list[str]) -> None:
        for side in ("left", "right"):
            self._set_blocked(side, self._target_side_for_source(side, config), reasons)

    def _set_blocked(self, source_side: SideName, target_side: SideName, reasons: list[str]) -> None:
        self._last_blockers[source_side] = {
            "sourceSide": source_side,
            "targetSide": target_side,
            "active": False,
            "state": "blocked",
            "reasons": list(reasons),
            "ts": now_ms(),
        }

    def _set_active(self, source_side: SideName, target_side: SideName) -> None:
        self._last_blockers[source_side] = {
            "sourceSide": source_side,
            "targetSide": target_side,
            "active": True,
            "state": "active",
            "reasons": [],
            "ts": now_ms(),
        }

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
        self._continuous_direction.clear()
        self._continuous_streak.clear()

    def _reset_side_tracking(self, side: SideName) -> None:
        self._references.pop(side, None)
        self._translation_carry.pop(side, None)
        self._translation_direction.pop(side, None)
        self._continuous_direction.pop(side, None)
        self._continuous_streak.pop(side, None)

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

    def _normalized_motion_origin(self, config: dict[str, Any]) -> dict[str, object]:
        raw_origin = config.get("motion", {}).get("origin", {})
        origin = raw_origin if isinstance(raw_origin, dict) else {}
        left_pulse = self._six_pulses(origin.get("leftPulse"))
        right_pulse = self._six_pulses(origin.get("rightPulse"))
        left_valid = bool(origin.get("leftValid", origin.get("valid", False)))
        right_valid = bool(origin.get("rightValid", origin.get("valid", False)))
        return {
            "valid": bool(left_valid and right_valid),
            "leftValid": left_valid,
            "rightValid": right_valid,
            "leftPulse": left_pulse,
            "rightPulse": right_pulse,
        }

    def _normalized_home_reference(self, config: dict[str, Any]) -> dict[str, object]:
        raw_reference = config.get("motion", {}).get("homeReference", {})
        reference = raw_reference if isinstance(raw_reference, dict) else {}
        left_pulse = self._six_pulses(reference.get("leftPulse"))
        right_pulse = self._six_pulses(reference.get("rightPulse"))
        left_valid = bool(reference.get("leftValid", reference.get("valid", False)))
        right_valid = bool(reference.get("rightValid", reference.get("valid", False)))
        return {
            "valid": bool(left_valid and right_valid),
            "leftValid": left_valid,
            "rightValid": right_valid,
            "leftPulse": left_pulse,
            "rightPulse": right_pulse,
        }

    def _six_pulses(self, value: object) -> list[float]:
        if not isinstance(value, list):
            return [0.0] * 6
        pulses = [float(item) for item in value[:6]]
        return pulses + [0.0] * max(0, 6 - len(pulses))

    def _hal_mode(self, config: dict[str, Any]) -> str:
        return str(os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")).lower()
