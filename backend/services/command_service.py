from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any, cast

from backend.core.config import SettingsService, reanchor_motion_soft_limits_to_current_origin
from backend.core.gripper_protection import (
    icf_target_min_gap_mm,
    icf_target_protection_enabled,
    protected_gripper_target_mm,
)
from backend.core.logging import LogService, now_ms
from backend.core.motion_limits import (
    WorkOriginMissing,
    effective_limits_ui,
    manual_axis_limits_ui,
    side_origin_ui,
    side_positions_ui,
    target_allowed_with_recovery,
)
from backend.core.schemas import GripperCommandRequest, ManualAxisMoveRequest, SettingsCommandRequest
from backend.core.units import motion_pulse_per_unit, pulse_to_ui
from backend.hal_client.client import HalClient
from backend.services.hardware_service import HardwareService
from backend.services.telemetry_hub import TelemetryHub

AXIS_ORDER = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
AXIS_LABELS = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
RIGHT_YAW_DISABLED_AXES = [True, True, True, True, True, False]
MANUAL_AXIS_STEP_LIMIT_PULSE = 100000.0
MANUAL_TRANSLATION_STEP_LIMIT_UM = 5000.0
MANUAL_ROTATION_STEP_LIMIT_DEG = 2.0
MANUAL_COARSE_ROTATION_STEP_LIMIT_DEG = 10.0
MANUAL_AXIS_CHUNK_WAIT_POLL_SEC = 0.02
MANUAL_AXIS_CHUNK_WAIT_MIN_TIMEOUT_SEC = 3.0
MANUAL_AXIS_CHUNK_WAIT_TIMEOUT_MARGIN_SEC = 1.0
ORIGIN_DRIFT_TRANSLATION_CONFIRM_UM = 5000.0
ORIGIN_DRIFT_ROTATION_CONFIRM_DEG = 1.0
ORIGIN_MUTATION_LOCKED_MESSAGE = "motion work origin cannot be changed while recording session is active"
UNREADABLE_SEVON_FEEDBACK_AXES: set[tuple[str, str]] = {("right", axis) for axis in AXIS_ORDER}
MANUAL_AXIS_DIRECTION_SIGN: dict[str, list[int]] = {
    "left": [1, -1, 1, 1, 1, 1],
    "right": [-1, -1, -1, 1, 1, 1],
}


def axis_enabled_feedback_unreadable(side: str, axis: str) -> bool:
    return (side, axis) in UNREADABLE_SEVON_FEEDBACK_AXES


def normalize_motion_axis_enabled(side: str, values: list[Any]) -> list[bool | None]:
    normalized: list[bool | None] = [bool(value) for value in values[:6]]
    for axis_index, axis in enumerate(AXIS_ORDER[: len(normalized)]):
        if axis_enabled_feedback_unreadable(side, axis) and normalized[axis_index] is not True:
            normalized[axis_index] = None
    return normalized


class MotionOriginDriftConfirmationRequired(RuntimeError):
    def __init__(self, drift: dict[str, object]) -> None:
        super().__init__("motion work origin drift requires explicit confirmation")
        self.drift = drift


class CommandService:
    def __init__(
        self,
        settings: SettingsService,
        telemetry: TelemetryHub,
        hal: HalClient,
        logs: LogService,
        hardware: HardwareService | None = None,
        gripper_workers: Any | None = None,
        origin_mutation_locked: Callable[[], bool] | None = None,
        teleop: Any | None = None,
    ) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.hal = hal
        self.logs = logs
        self.hardware = hardware
        self.gripper_workers = gripper_workers
        self.teleop = teleop
        self._origin_mutation_locked = origin_mutation_locked or (lambda: False)
        self._motion_enable_restore_snapshot: dict[str, list[bool]] | None = None

    def set_origin_mutation_lock_checker(self, checker: Callable[[], bool]) -> None:
        self._origin_mutation_locked = checker

    def _ensure_origin_mutation_allowed(self) -> None:
        if self._origin_mutation_locked():
            raise RuntimeError(ORIGIN_MUTATION_LOCKED_MESSAGE)

    async def _ensure_motion_return_allowed(self) -> dict[str, Any]:
        if bool(getattr(self.telemetry, "estop_active", False)):
            raise RuntimeError("emergency stop active; acknowledge safety before returning to work origin")
        state = await self.hal.motion_state()
        if bool(state.get("estop_active", False)):
            raise RuntimeError("emergency stop active; acknowledge safety before returning to work origin")
        return state

    async def _stop_manual_teleop_connect_before_motion_return(self) -> None:
        teleop = getattr(self, "teleop", None)
        if teleop is None:
            return
        status_fn = getattr(teleop, "status", None)
        stop_fn = getattr(teleop, "stop", None)
        if not callable(status_fn) or not callable(stop_fn):
            return
        status = status_fn()
        sources = status.get("sources") if isinstance(status, dict) else None
        if not isinstance(sources, list) or "teleop-connect" not in sources:
            return
        await stop_fn("teleop-connect", restart_remaining=False)
        self.logs.info("[HAL]", "teleop-connect stopped before return-to-work-origin")

    async def _stop_native_teleop_after_origin_change(self) -> None:
        teleop = getattr(self, "teleop", None)
        if teleop is None:
            return
        status_fn = getattr(teleop, "status", None)
        stop_fn = getattr(teleop, "stop", None)
        if not callable(status_fn) or not callable(stop_fn):
            return
        status = status_fn()
        if not isinstance(status, dict):
            return
        raw_sources = status.get("sources")
        sources = [str(source) for source in raw_sources] if isinstance(raw_sources, list) else []
        if not sources and not (bool(status.get("running")) or bool(status.get("armed"))):
            return
        for source in sources or ["teleop-connect"]:
            await stop_fn(source, restart_remaining=False)
        self.logs.info("[HAL]", "native teleop stopped after work origin change")

    async def generic_command(self, request: SettingsCommandRequest) -> dict[str, object]:
        self.logs.append(request.channel, request.level, request.msg)
        return {"accepted": True, "mode": "backend"}

    async def reconnect_hal(self) -> dict[str, object]:
        result = await self.hal.command("hal.reconnect")
        self.logs.info("[HAL]", "HAL reconnect requested")
        return result

    async def emergency_stop(self) -> dict[str, object]:
        # 先更新本地遥测状态，再请求 HAL 急停，让 UI 能立即进入安全态。
        if self._motion_enable_restore_snapshot is None:
            self._motion_enable_restore_snapshot = self._motion_enable_snapshot()
        self.telemetry.emergency_stop()
        result = await self.hal.command("motion.emergency_stop")
        self.logs.error("[SAFETY]", "hardware emergency stop requested")
        return result

    async def home_all(self) -> dict[str, object]:
        motion_state = await self._ensure_motion_return_allowed()
        config = self.settings.get_config()
        op_id = self.logs.new_op_id("work_origin")
        self.logs.event(
            "[HAL]",
            "INFO",
            "work_origin_op",
            component="MOTION",
            op_id=op_id,
            action="home_all",
            phase="start",
        )
        origin = self._normalized_motion_origin(config)
        if not bool(origin["valid"]):
            raise RuntimeError("motion work origin is not captured")
        left_pulse = cast(list[float], origin["leftPulse"])
        right_pulse = cast(list[float], origin["rightPulse"])
        left_enabled_axes = self._home_enabled_axes("left")
        right_enabled_axes = self._home_enabled_axes("right")
        await self._stop_manual_teleop_connect_before_motion_return()
        self._validate_work_origin_target(config, "left", left_enabled_axes)
        self._validate_work_origin_target(config, "right", right_enabled_axes)
        self._validate_motion_axes_enabled_for_work_origin(motion_state, "left", left_enabled_axes)
        self._validate_motion_axes_enabled_for_work_origin(motion_state, "right", right_enabled_axes)
        current_pulses = self._motion_state_pulses(motion_state)
        self._log_work_origin_moves(
            config,
            op_id,
            "home_all",
            "planned",
            "left",
            left_pulse,
            left_enabled_axes,
            current_pulses=current_pulses[:6],
        )
        self._log_work_origin_moves(
            config,
            op_id,
            "home_all",
            "planned",
            "right",
            right_pulse,
            right_enabled_axes,
            current_pulses=current_pulses[6:12],
        )
        result = await self.hal.command(
            "motion.home_all",
            {
                "leftPulse": left_pulse,
                "rightPulse": right_pulse,
                "leftEnabledAxes": left_enabled_axes,
                "rightEnabledAxes": right_enabled_axes,
            },
        )
        self.telemetry.home_all()
        self._log_work_origin_moves(config, op_id, "home_all", "complete", "left", left_pulse, left_enabled_axes)
        self._log_work_origin_moves(config, op_id, "home_all", "complete", "right", right_pulse, right_enabled_axes)
        self.logs.event(
            "[HAL]",
            "INFO",
            "work_origin_op",
            component="MOTION",
            op_id=op_id,
            action="home_all",
            phase="complete",
            summary="accepted",
        )
        return result

    async def return_motion_origin_side(self, side: str) -> dict[str, object]:
        self._validate_side(side)
        motion_state = await self._ensure_motion_return_allowed()
        config = self.settings.get_config()
        op_id = self.logs.new_op_id("work_origin")
        self.logs.event(
            "[HAL]",
            "INFO",
            "work_origin_op",
            component="MOTION",
            op_id=op_id,
            action="home_origin_side",
            phase="start",
            side=side,
        )
        origin = self._normalized_motion_origin(config)
        valid_key = "leftValid" if side == "left" else "rightValid"
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        if not bool(origin[valid_key]):
            raise RuntimeError(f"{side} motion work origin is not captured")
        pulse = cast(list[float], origin[pulse_key])
        enabled_axes = self._home_enabled_axes(side)
        await self._stop_manual_teleop_connect_before_motion_return()
        self._validate_work_origin_target(config, side, enabled_axes)
        self._validate_motion_axes_enabled_for_work_origin(motion_state, side, enabled_axes)
        current_pulses = self._motion_state_pulses(motion_state)
        side_offset = 0 if side == "left" else 6
        self._log_work_origin_moves(
            config,
            op_id,
            "home_origin_side",
            "planned",
            side,
            pulse,
            enabled_axes,
            current_pulses=current_pulses[side_offset : side_offset + 6],
        )
        result = await self.hal.command(
            "motion.home_origin_side",
            {
                "side": side,
                "pulse": pulse,
                "enabledAxes": enabled_axes,
            },
        )
        self.telemetry.home_side(side)
        self._log_work_origin_moves(config, op_id, "home_origin_side", "complete", side, pulse, enabled_axes)
        self.logs.event(
            "[HAL]",
            "INFO",
            "work_origin_op",
            component="MOTION",
            op_id=op_id,
            action="home_origin_side",
            phase="complete",
            side=side,
            summary="accepted",
        )
        return result

    async def enable_motion_side(self, side: str, enabled_axes: list[bool] | None = None) -> dict[str, object]:
        self._validate_side(side)
        enabled_axes = self._home_enabled_axes(side) if enabled_axes is None else enabled_axes
        payload: dict[str, object] = {"side": side}
        payload["enabledAxes"] = list(enabled_axes)
        result = await self.hal.command("motion.enable_side", payload)
        await self._refresh_motion_enabled(side)
        side_label = "left" if side == "left" else "right"
        self.logs.info("[HAL]", f"{side_label} motion axes enable requested")
        return result

    async def disable_motion_side(self, side: str) -> dict[str, object]:
        self._validate_side(side)
        result = await self.hal.command("motion.disable_side", {"side": side})
        await self._refresh_motion_enabled(side)
        side_label = "left" if side == "left" else "right"
        self.logs.info("[HAL]", f"{side_label} motion axes disable requested")
        return result

    async def stop_motion_side(self, side: str) -> dict[str, object]:
        self._validate_side(side)
        result = await self.hal.command("motion.teleop_stop_side", {"side": side})
        side_label = "left" if side == "left" else "right"
        self.logs.warning("[HAL]", f"{side_label} motion stop requested")
        return result

    async def home_motion_side(self, side: str) -> dict[str, object]:
        self._validate_side(side)
        self._ensure_origin_mutation_allowed()
        result = await self.hal.command(
            "motion.home_side",
            {"side": side, "enabledAxes": self._home_enabled_axes(side)},
        )
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        home_reference = self._normalized_home_reference(config)
        work_origin_offset = self._normalized_work_origin_offset(config)
        updated_at = now_ms()
        self._set_home_reference_side(home_reference, side, pulses, updated_at)
        valid_key = "leftValid" if side == "left" else "rightValid"
        if bool(work_origin_offset.get(valid_key)):
            self._apply_home_reference_offset(origin, home_reference, work_origin_offset, side, updated_at)
        config["motion"]["origin"] = origin
        config["motion"]["homeReference"] = home_reference
        config["motion"]["workOriginOffset"] = work_origin_offset
        reanchor_motion_soft_limits_to_current_origin(config, side)
        work_origin_invalidated = False
        if bool(origin.get(valid_key)):
            try:
                self._validate_work_origin_target(config, side, self._home_enabled_axes(side))
            except RuntimeError as exc:
                origin[valid_key] = False
                origin["valid"] = bool(origin["leftValid"] and origin["rightValid"])
                work_origin_offset[valid_key] = False
                work_origin_offset["valid"] = bool(
                    work_origin_offset["leftValid"] and work_origin_offset["rightValid"]
                )
                config["motion"]["origin"] = origin
                config["motion"]["workOriginOffset"] = work_origin_offset
                work_origin_invalidated = True
                self.logs.warning(
                    "[HAL]",
                    f"{side} motion work origin invalidated after hardware zero refresh: {exc}",
                )
        saved = self.settings.save_config(config, emit_log=False)
        self.telemetry.home_side(side)
        side_label = "left" if side == "left" else "right"
        self.logs.info("[HAL]", f"{side_label} motion hardware zero refreshed")
        return {
            **result,
            "origin": saved["motion"]["origin"],
            "homeReference": saved["motion"]["homeReference"],
            "workOriginOffset": saved["motion"]["workOriginOffset"],
            "workOriginInvalidated": work_origin_invalidated,
        }

    def motion_origin_status(self) -> dict[str, object]:
        config = self.settings.get_config()
        return {
            "origin": self._normalized_motion_origin(config),
            "homeReference": self._normalized_home_reference(config),
            "workOriginOffset": self._normalized_work_origin_offset(config),
        }

    async def capture_motion_origin(
        self,
        side: str | None = None,
        *,
        confirm_large_drift: bool = False,
    ) -> dict[str, object]:
        if side is not None:
            self._validate_side(side)
        self._ensure_origin_mutation_allowed()
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        drift = self._motion_origin_capture_drift(config, origin, pulses, side)
        if bool(drift["requiresConfirmation"]) and not confirm_large_drift:
            label = side or "both"
            self.logs.warning("[HAL]", f"{label} motion work origin recording blocked by large drift")
            raise MotionOriginDriftConfirmationRequired(drift)
        if bool(origin["valid"]):
            origin["previousValid"] = True
            origin["previousLeftPulse"] = list(cast(list[float], origin["leftPulse"]))
            origin["previousRightPulse"] = list(cast(list[float], origin["rightPulse"]))
            origin["previousUpdatedAt"] = int(cast(Any, origin["updatedAt"]))
        home_reference = self._normalized_home_reference(config)
        work_origin_offset = self._normalized_work_origin_offset(config)
        captured_at = now_ms()
        if side in {None, "left"}:
            left_pulse = pulses[:6]
            origin["leftPulse"] = left_pulse
            origin["leftValid"] = True
            self._set_work_origin_offset_side(work_origin_offset, home_reference, "left", left_pulse, captured_at)
        if side in {None, "right"}:
            right_pulse = pulses[6:12]
            origin["rightPulse"] = right_pulse
            origin["rightValid"] = True
            self._set_work_origin_offset_side(work_origin_offset, home_reference, "right", right_pulse, captured_at)
        origin["valid"] = bool(origin["leftValid"] and origin["rightValid"])
        origin["updatedAt"] = captured_at
        config["motion"]["origin"] = origin
        config["motion"]["homeReference"] = home_reference
        config["motion"]["workOriginOffset"] = work_origin_offset
        reanchor_motion_soft_limits_to_current_origin(config, side)
        for active_side in ("left", "right") if side is None else (side,):
            self._validate_work_origin_target(config, active_side, self._home_enabled_axes(active_side))
        saved = self.settings.save_config(config, emit_log=False)
        label = side or "both"
        self.logs.info("[HAL]", f"{label} motion work origin recorded")
        await self._stop_native_teleop_after_origin_change()
        return {"origin": saved["motion"]["origin"], "config": saved, "originCaptureDrift": drift}

    async def restore_previous_motion_origin(self) -> dict[str, object]:
        self._ensure_origin_mutation_allowed()
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        if not bool(origin["previousValid"]):
            raise RuntimeError("previous motion work origin is not available")
        current_valid = bool(origin["valid"])
        current_left = list(cast(list[float], origin["leftPulse"]))
        current_right = list(cast(list[float], origin["rightPulse"]))
        current_updated_at = int(cast(Any, origin["updatedAt"]))
        origin["leftPulse"] = list(cast(list[float], origin["previousLeftPulse"]))
        origin["rightPulse"] = list(cast(list[float], origin["previousRightPulse"]))
        origin["updatedAt"] = int(cast(Any, origin["previousUpdatedAt"]))
        origin["leftValid"] = True
        origin["rightValid"] = True
        origin["valid"] = True
        origin["previousValid"] = current_valid
        origin["previousLeftPulse"] = current_left
        origin["previousRightPulse"] = current_right
        origin["previousUpdatedAt"] = current_updated_at if current_valid else 0
        config["motion"]["origin"] = origin
        reanchor_motion_soft_limits_to_current_origin(config)
        saved = self.settings.save_config(config, emit_log=False)
        self.logs.info("[HAL]", "previous motion work origin restored")
        await self._stop_native_teleop_after_origin_change()
        return {"origin": saved["motion"]["origin"], "config": saved}

    def clear_motion_origin(self, side: str | None = None) -> dict[str, object]:
        if side is not None:
            self._validate_side(side)
        self._ensure_origin_mutation_allowed()
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        if side in {None, "left"}:
            origin["leftPulse"] = [0.0] * 6
            origin["leftValid"] = False
        if side in {None, "right"}:
            origin["rightPulse"] = [0.0] * 6
            origin["rightValid"] = False
        origin["valid"] = bool(origin["leftValid"] and origin["rightValid"])
        origin["updatedAt"] = now_ms() if origin["leftValid"] or origin["rightValid"] else 0
        config["motion"]["origin"] = origin
        work_origin_offset = self._normalized_work_origin_offset(config)
        if side in {None, "left"}:
            work_origin_offset["leftPulseDelta"] = [0.0] * 6
            work_origin_offset["leftValid"] = False
        if side in {None, "right"}:
            work_origin_offset["rightPulseDelta"] = [0.0] * 6
            work_origin_offset["rightValid"] = False
        work_origin_offset["valid"] = bool(work_origin_offset["leftValid"] and work_origin_offset["rightValid"])
        work_origin_offset["updatedAt"] = origin["updatedAt"]
        config["motion"]["workOriginOffset"] = work_origin_offset
        saved = self.settings.save_config(config, emit_log=False)
        label = side or "both"
        self.logs.info("[HAL]", f"{label} motion work origin record cleared")
        return {"origin": saved["motion"]["origin"], "config": saved}

    async def acknowledge_safety(self) -> dict[str, object]:
        restored_sides: list[str] = []
        snapshot = self._motion_enable_restore_snapshot
        if snapshot:
            for side in ("left", "right"):
                enabled_axes = snapshot.get(side)
                if enabled_axes is None or not any(enabled_axes):
                    continue
                await self.hal.command("motion.enable_side", {"side": side, "enabledAxes": list(enabled_axes)})
                await self._refresh_motion_enabled(side)
                restored_sides.append(side)
            self._motion_enable_restore_snapshot = None
        self.telemetry.acknowledge_safety()
        restored = ",".join(restored_sides) if restored_sides else "none"
        self.logs.info("[SAFETY]", f"safety state acknowledged; restored_motion_enable={restored}")
        return {"accepted": True, "restoredMotionEnable": restored_sides}

    async def tare_force(self, side: str | None = None) -> dict[str, object]:
        # 真机模式优先执行传感器 tare；测试模式仅重置本地模拟力数据。
        if self.hardware is not None and self._real_hardware_mode(self.settings.get_config()):
            result = self.hardware.force.tare(self.settings.get_config(), side)
            if not result.ok:
                self.logs.error("[FORCE]", result.message)
                raise RuntimeError(result.message)
        self.telemetry.tare_force()
        label = "both sides" if side is None else ("left" if side == "left" else "right")
        self.logs.info("[FORCE]", f"{label} Nano-17 tare requested")
        return {"side": side or "all"}

    async def manual_axis_move(self, request: ManualAxisMoveRequest) -> dict[str, object]:
        config = self.settings.get_config()
        # 所有手动 jog 都先过后端安全边界，再决定发往真机还是本地模拟。
        self._validate_manual_axis_policy(config, request)
        self._validate_manual_axis_safety(config, request)
        effective_direction = self._manual_axis_effective_direction(request.side, request.axis, request.direction)
        op_id = self.logs.new_op_id("manual")
        safe_delta = effective_direction * abs(float(request.step))
        if self._real_hardware_mode(config):
            await self._validate_motion_axis_enabled(request.side, request.axis)
            soft_limit_diag = await self._validate_manual_axis_soft_limit(config, request, effective_direction)
            profile = self._axis_profile(config, request)
            chunk_steps = self._manual_axis_chunk_steps(request)
            hal_result: dict[str, object] = {}
            for index, chunk_step in enumerate(chunk_steps):
                chunk_request = request.model_copy(update={"step": chunk_step})
                if index > 0:
                    current_config = self.settings.get_config()
                    remaining_step = sum(chunk_steps[index:])
                    remaining_request = request.model_copy(update={"step": remaining_step})
                    await self._validate_manual_axis_soft_limit(
                        current_config,
                        remaining_request,
                        effective_direction,
                    )
                hal_result = await self.hal.command(
                    "motion.manual_axis_move",
                    {
                        "side": request.side,
                        "axis": request.axis,
                        "direction": effective_direction,
                        "requestedDirection": request.direction,
                        "step": chunk_request.step,
                        "speedMode": request.speedMode,
                        "maxVelocityUiPerSec": profile["maxVelocity"],
                        "startVelocityUiPerSec": profile["startVelocity"],
                        "accTimeSec": profile["accTime"],
                        "decTimeSec": profile["decTime"],
                    },
                )
                if len(chunk_steps) > 1:
                    await self._wait_manual_axis_idle(request.side, request.axis, profile, chunk_step)
            self.logs.event(
                "[HAL]",
                "INFO",
                "manual_move",
                component="MOTION",
                op_id=op_id,
                **{
                    **self._manual_axis_log_fields(
                        config,
                        request,
                        effective_direction,
                        safe_delta,
                        backend="hal",
                        dmc_ret=self._hal_response_message(hal_result) or "accepted",
                        soft_limit=soft_limit_diag,
                    ),
                    "chunkCount": len(chunk_steps),
                    "chunkSteps": chunk_steps,
                },
            )
            return {"hal": hal_result, "chunkCount": len(chunk_steps), "chunkSteps": chunk_steps}
        applied = self.telemetry.apply_axis_move(request.side, request.axis, effective_direction, request.step)
        self.logs.event(
            "[HAL]",
            "INFO",
            "manual_move",
            component="MOTION",
            op_id=op_id,
            **self._manual_axis_log_fields(
                config,
                request,
                effective_direction,
                applied,
                backend="test",
                dmc_ret="not_called",
            ),
        )
        return {"applied": applied}

    async def gripper_command(self, request: GripperCommandRequest) -> dict[str, object]:
        config = self.settings.get_config()
        gripper_workers = self.gripper_workers
        use_gripper_workers = gripper_workers is not None and gripper_workers.is_enabled(config)
        if (
            self.hardware is not None
            and self._real_hardware_mode(config)
            and self._hal_native_teleop(config)
            and not use_gripper_workers
        ):
            target = self._gripper_command_target(config, request)
            side_label = "left gripper" if request.side == "left" else "right gripper"
            if target is None:
                if request.command == "enable":
                    target_key = "targetLeftMm" if request.side == "left" else "targetRightMm"
                    target = protected_gripper_target_mm(
                        config,
                        float(config.get("gripper", {}).get(target_key, 0.0)),
                    )
                    payload = self._native_gripper_payload(config, request.side, target)
                    try:
                        hal_result = await self.hal.command("teleop.native.gripper_command", payload)
                    except Exception as exc:
                        self._log_gripper_command(
                            config,
                            request,
                            backend="hal_native",
                            target=target,
                            run_ret=False,
                            ipc_ok=False,
                            error=str(exc),
                        )
                        raise
                    self._save_gripper_command_state(config, request, target)
                    self._log_gripper_command(
                        config,
                        request,
                        backend="hal_native",
                        target=target,
                        run_ret=True,
                        ipc_ok=True,
                    )
                    response_message = self._hal_response_message(hal_result) or "HAL-native gripper enable accepted"
                    self.logs.info("[GRIPPER]", f"{side_label} {request.command}: {response_message}")
                    return {
                        "message": response_message,
                        "nativeManaged": True,
                        "targetMm": target,
                        "hal": hal_result,
                    }
                self._save_gripper_command_state(config, request, target)
                message = "HAL-native gripper state updated; no position command required"
                self._log_gripper_command(
                    config,
                    request,
                    backend="hal_native",
                    target=target,
                    run_ret="not_called",
                    ipc_ok=True,
                )
                self.logs.info("[GRIPPER]", f"{side_label} {request.command}: {message}")
                return {"message": message, "nativeManaged": True}
            payload = self._native_gripper_payload(config, request.side, target)
            try:
                hal_result = await self.hal.command("teleop.native.gripper_command", payload)
            except Exception as exc:
                self._log_gripper_command(
                    config,
                    request,
                    backend="hal_native",
                    target=target,
                    run_ret=False,
                    ipc_ok=False,
                    error=str(exc),
                )
                raise
            if request.command in {"open", "close", "home", "target"}:
                config.setdefault("gripper", {})[f"{request.side}Enabled"] = True
            self._save_gripper_command_state(config, request, target)
            self._log_gripper_command(
                config,
                request,
                backend="hal_native",
                target=target,
                run_ret=True,
                ipc_ok=True,
            )
            response_message = self._hal_response_message(hal_result) or "HAL-native gripper command accepted"
            self.logs.info("[GRIPPER]", f"{side_label} {request.command}: {response_message}")
            response: dict[str, object] = {
                "message": response_message,
                "nativeManaged": True,
                "targetMm": target,
                "hal": hal_result,
            }
            return response
        self._validate_gripper_command_enabled(config, request)
        if self.hardware is not None and self._real_hardware_mode(config):
            # 真机成功响应后才保存目标开合度，避免 UI 记住未执行的硬件状态。
            gripper_backend = "dual_worker" if use_gripper_workers else "python_rs485"
            target = self._gripper_command_target(config, request)
            dispatch_command, dispatch_target = self._gripper_dispatch_command(config, request, target)
            if use_gripper_workers:
                if gripper_workers is None:
                    raise RuntimeError("gripper worker service is not available")
                result = gripper_workers.command(config, request.side, dispatch_command, dispatch_target)
            else:
                result = self.hardware.gripper.command(config, request.side, dispatch_command, dispatch_target)
            if not result.ok:
                self._log_gripper_command(
                    config,
                    request,
                    backend=gripper_backend,
                    target=target,
                    run_ret=result.ok,
                    ipc_ok=True,
                    error=result.message,
                )
                self.logs.error("[GRIPPER]", result.message)
                raise RuntimeError(result.message)
            self._save_gripper_command_state(config, request, target)
            self._log_gripper_command(
                config,
                request,
                backend=gripper_backend,
                target=target,
                run_ret=result.ok,
                ipc_ok=True,
            )
            side_label = "left gripper" if request.side == "left" else "right gripper"
            self.logs.info("[GRIPPER]", f"{side_label} {request.command}: {result.message}")
            worker_response: dict[str, object] = {"message": result.message}
            if target is not None:
                worker_response["targetMm"] = target
            return worker_response
        config = self.settings.get_config()
        target = self._gripper_command_target(config, request)
        if target is None:
            target = self.telemetry.apply_gripper(request.side, request.command, request.targetMm)
        else:
            self.telemetry.apply_gripper(request.side, "target", target)
        self._save_gripper_command_state(config, request, target)
        self._log_gripper_command(
            config,
            request,
            backend="test",
            target=target,
            run_ret="not_called",
            ipc_ok=False,
        )
        side_label = "left gripper" if request.side == "left" else "right gripper"
        self.logs.info("[GRIPPER]", f"{side_label} {request.command} -> {target:.1f} mm local fallback")
        return {"targetMm": target}

    def _manual_axis_log_fields(
        self,
        config: dict[str, Any],
        request: ManualAxisMoveRequest,
        effective_direction: int,
        safe_delta: float,
        *,
        backend: str,
        dmc_ret: object,
        busy: bool = False,
        clip: str = "none",
        soft_limit: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        axis_index = AXIS_ORDER.index(request.axis)
        side_offset = 0 if request.side == "left" else 6
        motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
        kinematics = motion.get("kinematics", {}) if isinstance(motion.get("kinematics"), dict) else {}
        axis_map = kinematics.get(
            f"{request.side}PhysicalAxis",
            kinematics.get(f"{request.side}AxisMap", list(range(6))),
        )
        return {
            "axis": f"{request.side}.{request.axis}",
            "side": request.side,
            "axisName": request.axis,
            "card": motion.get(f"{request.side}CardNo", 1 if request.side == "left" else 0),
            "physicalAxis": (
                axis_map[axis_index] if isinstance(axis_map, list) and len(axis_map) > axis_index else axis_index
            ),
            "logicalAxis": side_offset + axis_index,
            "current": soft_limit["current"] if soft_limit is not None else "unknown",
            "requestedDelta": request.direction * request.step,
            "safeDelta": safe_delta,
            "target": soft_limit["target"] if soft_limit is not None else "unknown",
            "limit": (
                f"[{soft_limit['limitMin']:.3f},{soft_limit['limitMax']:.3f}]"
                if soft_limit is not None
                else "unknown"
            ),
            "clip": clip,
            "profileRet": "not_available",
            "sProfileRet": "not_available",
            "dmcRet": dmc_ret,
            "busy": busy,
            "pulsePerUnit": motion_pulse_per_unit(config)[side_offset + axis_index],
            "speedMode": request.speedMode,
            "backend": backend,
        }

    def _log_work_origin_moves(
        self,
        config: dict[str, Any],
        op_id: str,
        action: str,
        phase: str,
        side: str,
        pulses: list[float],
        enabled_axes: list[bool] | None = None,
        current_pulses: list[float] | None = None,
    ) -> None:
        motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
        kinematics = motion.get("kinematics", {}) if isinstance(motion.get("kinematics"), dict) else {}
        axis_map = kinematics.get(f"{side}PhysicalAxis", kinematics.get(f"{side}AxisMap", list(range(6))))
        axes = enabled_axes if isinstance(enabled_axes, list) and len(enabled_axes) >= 6 else [True] * 6
        pulse_per_unit = motion_pulse_per_unit(config)
        side_offset = 0 if side == "left" else 6
        for axis_index, axis_name in enumerate(AXIS_LABELS):
            if not axes[axis_index]:
                continue
            current = (
                float(current_pulses[axis_index])
                if isinstance(current_pulses, list) and len(current_pulses) > axis_index
                else None
            )
            target = float(pulses[axis_index])
            delta_pulse = target - current if current is not None else None
            state_index = side_offset + axis_index
            self.logs.event(
                "[HAL]",
                "INFO",
                "work_origin_move",
                component="MOTION",
                op_id=op_id,
                action=action,
                phase=phase,
                axis=f"{side}.{axis_name}",
                side=side,
                card=motion.get(f"{side}CardNo", 1 if side == "left" else 0),
                physicalAxis=(
                    axis_map[axis_index] if isinstance(axis_map, list) and len(axis_map) > axis_index else axis_index
                ),
                current=current if current is not None else "unknown",
                requestedTarget=target,
                safeTarget=target,
                deltaPulse=delta_pulse if delta_pulse is not None else "unknown",
                deltaUi=(
                    pulse_to_ui(delta_pulse, state_index, pulse_per_unit[state_index])
                    if delta_pulse is not None
                    else "unknown"
                ),
                limit="origin",
                clip="none",
                dmcRet="pending" if phase == "planned" else "accepted",
                busyAxes="unknown",
                summary="planned" if phase == "planned" else "requested",
            )

    def _log_gripper_command(
        self,
        config: dict[str, Any],
        request: GripperCommandRequest,
        *,
        backend: str,
        target: float | None,
        run_ret: object,
        ipc_ok: bool,
        error: str = "",
    ) -> None:
        event = getattr(self.logs, "event", None)
        if not callable(event):
            return
        gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        gripper_teleop = teleop.get("gripperTeleop", {}) if isinstance(teleop.get("gripperTeleop"), dict) else {}
        port_key = "leftPort" if request.side == "left" else "rightPort"
        slave_key = "leftSlaveId" if request.side == "left" else "rightSlaveId"
        event(
            "[GRIPPER]",
            "ERROR" if error else "INFO",
            "gripper_command",
            component="GRIPPER",
            side=request.side,
            backend=backend,
            port=gripper.get(port_key, ""),
            slave=gripper.get(slave_key, ""),
            command=request.command,
            pos=target if target is not None else "",
            minTargetMm=icf_target_min_gap_mm(config),
            minTargetEnabled=icf_target_protection_enabled(config),
            speed=gripper_teleop.get("gripSpeed", gripper.get("speed", "")),
            torque=gripper_teleop.get("gripTorque", gripper.get("commandTorque", 1)),
            runRet=run_ret,
            ipcOk=ipc_ok,
            error=error,
        )

    def _gripper_command_target(self, config: dict[str, Any], request: GripperCommandRequest) -> float | None:
        stroke = float(config["gripper"].get("strokeMm", 26))
        if request.command == "open":
            return stroke
        if request.command == "close":
            return protected_gripper_target_mm(config, 0.0)
        if request.command == "home":
            if self._hal_native_teleop(config):
                return protected_gripper_target_mm(config, 0.0)
            return 0.0
        if request.command == "target":
            return protected_gripper_target_mm(config, float(request.targetMm if request.targetMm is not None else 0.0))
        return None

    def _gripper_dispatch_command(
        self,
        config: dict[str, Any],
        request: GripperCommandRequest,
        target: float | None,
    ) -> tuple[str, float | None]:
        if target is None:
            return request.command, request.targetMm
        if request.command == "target":
            return "target", target
        if request.command == "close" and icf_target_protection_enabled(config):
            return "target", target
        return request.command, request.targetMm

    def _save_gripper_command_state(
        self,
        config: dict[str, Any],
        request: GripperCommandRequest,
        target: float | None,
    ) -> None:
        # 夹爪的启停和目标位置属于操作状态，写回配置后前端刷新仍能复现。
        side_key = "Left" if request.side == "left" else "Right"
        if target is not None:
            config["gripper"][f"target{side_key}Mm"] = target
        if request.command == "enable":
            config["gripper"][f"{request.side}Enabled"] = True
        elif request.command == "disable":
            config["gripper"][f"{request.side}Enabled"] = False
        self.settings.save_config(config, emit_log=False)

    def _native_gripper_payload(self, config: dict[str, Any], side: str, target: float) -> dict[str, object]:
        gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        gripper_teleop = teleop.get("gripperTeleop", {}) if isinstance(teleop.get("gripperTeleop"), dict) else {}
        return {
            "side": side,
            "targetMm": target,
            "leftPort": str(gripper.get("leftPort", "COM8")),
            "rightPort": str(gripper.get("rightPort", "COM9")),
            "leftSlaveId": int(gripper.get("leftSlaveId", 10)),
            "rightSlaveId": int(gripper.get("rightSlaveId", 9)),
            "baudrate": int(gripper.get("baudrate", 115200)),
            "strokeMm": float(gripper.get("strokeMm", 26)),
            "jodellDllPath": str(gripper.get("jodellDllPath", "")),
            "gripSpeed": int(gripper_teleop.get("gripSpeed", 255)),
            "gripTorque": int(gripper_teleop.get("gripTorque", 1)),
            "icfTargetProtectionEnabled": icf_target_protection_enabled(config),
            "icfTargetMinGapMm": icf_target_min_gap_mm(config),
        }

    def _hal_response_message(self, result: dict[str, object]) -> str:
        response = result.get("response")
        if isinstance(response, dict):
            message = response.get("message")
            if message is not None:
                return str(message)
        message = result.get("message")
        return str(message) if message is not None else ""

    def _validate_gripper_command_enabled(self, config: dict[str, Any], request: GripperCommandRequest) -> None:
        if request.command in {"enable", "disable", "stop"}:
            return
        enabled_key = f"{request.side}Enabled"
        if not bool(config.get("gripper", {}).get(enabled_key, False)):
            raise RuntimeError(f"{request.side} gripper is disabled; enable it before motion commands")

    def _validate_manual_axis_safety(self, config: dict[str, Any], request: ManualAxisMoveRequest) -> None:
        limit = self._manual_axis_step_ui_limit(config, request.side, request.axis, request.speedMode)
        if abs(float(request.step)) > limit:
            unit = "um" if request.axis in {"X", "Y", "Z"} else "degree"
            raise RuntimeError(
                f"manual {request.axis} step must be <= {limit:.3f} {unit} "
                f"(HAL single-step / {MANUAL_AXIS_STEP_LIMIT_PULSE:.0f} pulse cap)"
            )
        if self._axis_profile(config, request)["maxVelocity"] <= 0:
            raise RuntimeError("manual axis velocity must be positive")

    def _validate_manual_axis_policy(self, config: dict[str, Any], request: ManualAxisMoveRequest) -> None:
        _ = config
        if request.side == "right" and request.axis == "Yaw":
            raise RuntimeError("right Yaw motion axis is disabled by safety policy")

    def _manual_axis_step_pulse(self, config: dict[str, Any], request: ManualAxisMoveRequest) -> float:
        return abs(float(request.step)) * self._manual_axis_pulse_per_ui_unit(config, request.side, request.axis)

    def _manual_axis_step_ui_limit(
        self,
        config: dict[str, Any],
        side: str,
        axis: str,
        speed_mode: str | None = None,
    ) -> float:
        pulse_per_ui_unit = self._manual_axis_pulse_per_ui_unit(config, side, axis)
        if pulse_per_ui_unit <= 0:
            return 0.0
        pulse_limit = MANUAL_AXIS_STEP_LIMIT_PULSE / pulse_per_ui_unit
        return min(pulse_limit, self._manual_axis_request_step_limit(axis, speed_mode))

    def _manual_axis_request_step_limit(self, axis: str, speed_mode: str | None = None) -> float:
        if axis in {"X", "Y", "Z"}:
            return MANUAL_TRANSLATION_STEP_LIMIT_UM
        if speed_mode == "coarse":
            return MANUAL_COARSE_ROTATION_STEP_LIMIT_DEG
        return MANUAL_ROTATION_STEP_LIMIT_DEG

    def _manual_axis_hal_step_limit(self, axis: str) -> float:
        return MANUAL_TRANSLATION_STEP_LIMIT_UM if axis in {"X", "Y", "Z"} else MANUAL_ROTATION_STEP_LIMIT_DEG

    def _manual_axis_chunk_steps(self, request: ManualAxisMoveRequest) -> list[float]:
        total_step = abs(float(request.step))
        hal_step_limit = self._manual_axis_hal_step_limit(request.axis)
        if request.axis in {"X", "Y", "Z"} or request.speedMode != "coarse" or total_step <= hal_step_limit:
            return [total_step]
        chunks: list[float] = []
        remaining = total_step
        epsilon = 1e-9
        while remaining > hal_step_limit + epsilon:
            chunks.append(hal_step_limit)
            remaining -= hal_step_limit
        if remaining > epsilon:
            chunks.append(round(remaining, 9))
        return chunks

    def _manual_axis_pulse_per_ui_unit(self, config: dict[str, Any], side: str, axis: str) -> float:
        axis_index = AXIS_ORDER.index(axis)
        state_index = (0 if side == "left" else 6) + axis_index
        pulse_per_unit = abs(motion_pulse_per_unit(config)[state_index])
        if axis_index < 3:
            return pulse_per_unit / 1000.0
        return pulse_per_unit

    def _motion_enable_snapshot(self) -> dict[str, list[bool]]:
        snapshot: dict[str, list[bool]] = {}
        for side in ("left", "right"):
            side_enabled = self.telemetry.motion_enabled.get(side)
            raw_axes = list(self.telemetry.motion_axis_enabled.get(side, [None] * 6))[:6]
            while len(raw_axes) < 6:
                raw_axes.append(None)
            enabled_axes = [value is True or (value is None and side_enabled is True) for value in raw_axes]
            if any(enabled_axes):
                snapshot[side] = enabled_axes
        return snapshot

    async def _refresh_motion_enabled(self, side: str) -> None:
        state = await self.hal.motion_state()
        raw_enabled = state.get("enabled")
        if isinstance(raw_enabled, list) and len(raw_enabled) == 12:
            values = raw_enabled[:6] if side == "left" else raw_enabled[6:12]
            self.telemetry.set_motion_axis_enabled(side, self._normalize_motion_axis_enabled(side, values))
            return
        if isinstance(raw_enabled, dict):
            value = raw_enabled.get(side)
            if isinstance(value, bool):
                self.telemetry.set_motion_enabled(side, value)

    async def _validate_motion_axis_enabled(self, side: str, axis: str) -> None:
        state = await self.hal.motion_state()
        raw_enabled = state.get("enabled")
        axis_enabled: bool | None = None
        if isinstance(raw_enabled, list) and len(raw_enabled) == 12:
            axis_index = AXIS_ORDER.index(axis)
            state_index = (0 if side == "left" else 6) + axis_index
            if axis_enabled_feedback_unreadable(side, axis):
                return
            axis_enabled = bool(raw_enabled[state_index])
        elif isinstance(raw_enabled, dict):
            value = raw_enabled.get(side)
            if isinstance(value, bool):
                axis_enabled = value
        if axis_enabled is not True:
            raise RuntimeError(f"{side} {axis} motion axis is disabled; enable the axis before manual jog")

    async def _validate_manual_axis_soft_limit(
        self,
        config: dict[str, Any],
        request: ManualAxisMoveRequest,
        effective_direction: int,
    ) -> dict[str, float]:
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        axis_index = AXIS_ORDER.index(request.axis)
        if axis_index >= 3:
            try:
                limits = effective_limits_ui(config, request.side)
            except WorkOriginMissing as exc:
                raise RuntimeError(str(exc)) from exc
        else:
            limits = manual_axis_limits_ui(config, request.side)
        current = side_positions_ui(config, request.side, pulses)[axis_index]
        target = current + request.step * effective_direction
        limit = limits[axis_index]
        if not target_allowed_with_recovery(current, target, limit):
            raise RuntimeError(
                f"{request.side} {request.axis} target exceeds soft limit: "
                f"{target:.3f} not allowed from {current:.3f} with [{limit.min:.3f}, {limit.max:.3f}]"
            )
        return {
            "current": current,
            "target": target,
            "limitMin": limit.min,
            "limitMax": limit.max,
        }

    async def _wait_manual_axis_idle(
        self,
        side: str,
        axis: str,
        profile: dict[str, float],
        chunk_step: float,
    ) -> None:
        axis_index = AXIS_ORDER.index(axis)
        state_index = (0 if side == "left" else 6) + axis_index
        max_velocity = max(float(profile.get("maxVelocity", 0.0)), 0.001)
        timeout_sec = max(
            MANUAL_AXIS_CHUNK_WAIT_MIN_TIMEOUT_SEC,
            abs(float(chunk_step)) / max_velocity
            + float(profile.get("accTime", 0.0))
            + float(profile.get("decTime", 0.0))
            + MANUAL_AXIS_CHUNK_WAIT_TIMEOUT_MARGIN_SEC,
        )
        deadline = time.monotonic() + timeout_sec
        while True:
            state = await self.hal.motion_state()
            if bool(state.get("estop_active", False)):
                raise RuntimeError(f"manual {side} {axis} chunk aborted: emergency stop active")
            moving = state.get("moving")
            if not isinstance(moving, list) or len(moving) != 12:
                raise RuntimeError("HAL motion state does not include 12 moving values")
            if not bool(moving[state_index]):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"manual {side} {axis} chunk wait timed out")
            await asyncio.sleep(MANUAL_AXIS_CHUNK_WAIT_POLL_SEC)

    def _validate_work_origin_target(
        self,
        config: dict[str, Any],
        side: str,
        enabled_axes: list[bool] | None = None,
    ) -> None:
        origin = side_origin_ui(config, side)
        if origin is None:
            raise RuntimeError(f"{side} motion work origin is not captured")
        try:
            limits = effective_limits_ui(config, side)
        except WorkOriginMissing as exc:
            raise RuntimeError(str(exc)) from exc
        axes = (
            enabled_axes
            if isinstance(enabled_axes, list) and len(enabled_axes) >= 6
            else self._home_enabled_axes(side)
        )
        for axis_index, axis_name in enumerate(AXIS_ORDER):
            if not axes[axis_index]:
                continue
            limit = limits[axis_index]
            target = origin[axis_index]
            if limit.min > limit.max or target < limit.min or target > limit.max:
                raise RuntimeError(
                    f"{side} {axis_name} work origin exceeds soft limit: "
                    f"{target:.3f} not in [{limit.min:.3f}, {limit.max:.3f}]"
                )

    def _validate_motion_axes_enabled_for_work_origin(
        self,
        state: dict[str, Any],
        side: str,
        enabled_axes: list[bool],
    ) -> None:
        raw_enabled = state.get("enabled")
        disabled_axes: list[str] = []
        if isinstance(raw_enabled, list) and len(raw_enabled) == 12:
            offset = 0 if side == "left" else 6
            disabled_axes = [
                axis_name
                for axis_index, axis_name in enumerate(AXIS_ORDER)
                if enabled_axes[axis_index] and not bool(raw_enabled[offset + axis_index])
            ]
        elif isinstance(raw_enabled, dict):
            value = raw_enabled.get(side)
            if value is not True:
                disabled_axes = [
                    axis_name for axis_index, axis_name in enumerate(AXIS_ORDER) if enabled_axes[axis_index]
                ]
        else:
            raise RuntimeError("HAL motion state does not include enabled feedback")
        if disabled_axes:
            raise RuntimeError(
                f"{side} motion axes are disabled; enable required axes before returning to work origin: "
                f"{', '.join(disabled_axes)}"
            )

    def _normalize_motion_axis_enabled(self, side: str, values: list[Any]) -> list[bool | None]:
        return normalize_motion_axis_enabled(side, values)

    def _axis_enabled_feedback_unreadable(self, side: str, axis: str) -> bool:
        return axis_enabled_feedback_unreadable(side, axis)

    def _axis_profile(self, config: dict[str, Any], request: ManualAxisMoveRequest) -> dict[str, float]:
        motion = config["motion"]
        profile_key = f"{request.side}Profile"
        profile = motion[profile_key]
        group = "translation" if request.axis in {"X", "Y", "Z"} else "rotation"
        group_profile = profile.get(group, {})
        # Hardware-side guardrails: leishine + Yamaha stages handle these without
        # complaint, but anything beyond is almost certainly a config typo.
        max_velocity_cap = 20000.0 if group == "translation" else 30.0  # um/s, deg/s
        max_velocity = min(float(group_profile.get("maxSpeed", 1000.0)), max_velocity_cap)
        # speedMode scales the configured max velocity so the UI buttons can
        # still pick "fine" without re-editing settings every time.
        speed_scale = {"coarse": 2.0, "medium": 0.5, "fine": 0.2}.get(request.speedMode, 0.5)
        max_velocity = min(max_velocity_cap, max(0.001, max_velocity * speed_scale))
        start_velocity = min(float(group_profile.get("startSpeed", 0.0)), max_velocity)
        if start_velocity <= 0:
            start_velocity = max(0.0, max_velocity * 0.2)
        acc_time = float(group_profile.get("accTimeSec", 0.05))
        dec_time = float(group_profile.get("decTimeSec", 0.05))
        # Clamp to the LeiShine driver's accepted range. 0.001 is the SDK's
        # minimum non-zero ramp; values above 5s are almost certainly typos.
        acc_time = min(max(acc_time, 0.001), 5.0)
        dec_time = min(max(dec_time, 0.001), 5.0)
        return {
            "maxVelocity": max_velocity,
            "startVelocity": start_velocity,
            "accTime": acc_time,
            "decTime": dec_time,
        }

    def _manual_axis_effective_direction(self, side: str, axis: str, direction: int) -> int:
        axis_index = AXIS_ORDER.index(axis)
        sign = MANUAL_AXIS_DIRECTION_SIGN[side][axis_index]
        return 1 if direction * sign >= 0 else -1

    def _real_hardware_mode(self, config: dict[str, Any]) -> bool:
        mode = os.environ.get("APPSTATION_HAL_MODE") or config["hal"].get("mode", "real")
        return str(mode).lower() == "real"

    def _hal_native_teleop(self, config: dict[str, Any]) -> bool:
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        return str(teleop.get("engine", "")).lower() == "hal_native"

    def _motion_origin_capture_drift(
        self,
        config: dict[str, Any],
        origin: dict[str, object],
        pulses: list[float],
        side: str | None,
    ) -> dict[str, object]:
        sides = ("left", "right") if side is None else (side,)
        pulse_per_unit = motion_pulse_per_unit(config)
        side_details: list[dict[str, object]] = []
        for active_side in sides:
            baseline = self._motion_origin_baseline(origin, active_side)
            if baseline is None:
                continue
            baseline_source, baseline_pulses = baseline
            offset = 0 if active_side == "left" else 6
            new_pulses = pulses[offset : offset + 6]
            exceeded_axes: list[dict[str, object]] = []
            for axis_index, axis in enumerate(AXIS_ORDER):
                logical_axis = offset + axis_index
                delta_pulse = float(new_pulses[axis_index]) - float(baseline_pulses[axis_index])
                delta_ui = pulse_to_ui(delta_pulse, logical_axis, pulse_per_unit[logical_axis])
                threshold = (
                    ORIGIN_DRIFT_TRANSLATION_CONFIRM_UM
                    if axis_index < 3
                    else ORIGIN_DRIFT_ROTATION_CONFIRM_DEG
                )
                if abs(delta_ui) <= threshold:
                    continue
                exceeded_axes.append(
                    {
                        "axis": axis,
                        "deltaPulse": delta_pulse,
                        "deltaUi": delta_ui,
                        "absDeltaUi": abs(delta_ui),
                        "unit": "um" if axis_index < 3 else "deg",
                        "threshold": threshold,
                    }
                )
            if exceeded_axes:
                side_details.append(
                    {
                        "side": active_side,
                        "baseline": baseline_source,
                        "axes": exceeded_axes,
                    }
                )
        return {
            "requiresConfirmation": bool(side_details),
            "thresholds": {
                "translationUm": ORIGIN_DRIFT_TRANSLATION_CONFIRM_UM,
                "rotationDeg": ORIGIN_DRIFT_ROTATION_CONFIRM_DEG,
            },
            "sides": side_details,
        }

    def _motion_origin_baseline(
        self, origin: dict[str, object], side: str
    ) -> tuple[str, list[float]] | None:
        valid_key = "leftValid" if side == "left" else "rightValid"
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        previous_key = "previousLeftPulse" if side == "left" else "previousRightPulse"
        if bool(origin.get(valid_key)):
            return "current", list(cast(list[float], origin[pulse_key]))
        if bool(origin.get("previousValid")):
            return "previous", list(cast(list[float], origin[previous_key]))
        return None

    def _validate_side(self, side: str) -> None:
        if side not in {"left", "right"}:
            raise RuntimeError("side must be left or right")

    def _home_enabled_axes(self, side: str) -> list[bool]:
        return list(RIGHT_YAW_DISABLED_AXES if side == "right" else [True] * 6)

    def _motion_state_pulses(self, state: dict[str, Any]) -> list[float]:
        raw_pulses = state.get("pulses")
        if not isinstance(raw_pulses, list) or len(raw_pulses) != 12:
            raise RuntimeError("HAL motion state does not include 12 pulse values")
        return [float(value) for value in raw_pulses]

    def _normalized_motion_origin(self, config: dict[str, Any]) -> dict[str, object]:
        raw_origin = config.get("motion", {}).get("origin", {})
        origin = raw_origin if isinstance(raw_origin, dict) else {}
        left_pulse = self._six_pulses(origin.get("leftPulse"))
        right_pulse = self._six_pulses(origin.get("rightPulse"))
        previous_left_pulse = self._six_pulses(origin.get("previousLeftPulse"))
        previous_right_pulse = self._six_pulses(origin.get("previousRightPulse"))
        left_valid = bool(origin.get("leftValid", origin.get("valid", False)))
        right_valid = bool(origin.get("rightValid", origin.get("valid", False)))
        updated_at = origin.get("updatedAt", 0)
        try:
            updated_at = int(updated_at)
        except (TypeError, ValueError):
            updated_at = 0
        previous_updated_at = origin.get("previousUpdatedAt", 0)
        try:
            previous_updated_at = int(previous_updated_at)
        except (TypeError, ValueError):
            previous_updated_at = 0
        return {
            "valid": bool(left_valid and right_valid),
            "leftValid": left_valid,
            "rightValid": right_valid,
            "leftPulse": left_pulse,
            "rightPulse": right_pulse,
            "updatedAt": updated_at,
            "previousValid": bool(origin.get("previousValid", False)),
            "previousLeftPulse": previous_left_pulse,
            "previousRightPulse": previous_right_pulse,
            "previousUpdatedAt": previous_updated_at,
        }

    def _normalized_home_reference(self, config: dict[str, Any]) -> dict[str, object]:
        raw_reference = config.get("motion", {}).get("homeReference", {})
        reference = raw_reference if isinstance(raw_reference, dict) else {}
        left_pulse = self._six_pulses(reference.get("leftPulse"))
        right_pulse = self._six_pulses(reference.get("rightPulse"))
        left_valid = bool(reference.get("leftValid", reference.get("valid", False)))
        right_valid = bool(reference.get("rightValid", reference.get("valid", False)))
        updated_at = reference.get("updatedAt", 0)
        try:
            updated_at = int(updated_at)
        except (TypeError, ValueError):
            updated_at = 0
        return {
            "valid": bool(left_valid and right_valid),
            "leftValid": left_valid,
            "rightValid": right_valid,
            "leftPulse": left_pulse,
            "rightPulse": right_pulse,
            "updatedAt": updated_at,
        }

    def _normalized_work_origin_offset(self, config: dict[str, Any]) -> dict[str, object]:
        raw_offset = config.get("motion", {}).get("workOriginOffset", {})
        offset = raw_offset if isinstance(raw_offset, dict) else {}
        left_delta = self._six_pulses(offset.get("leftPulseDelta"))
        right_delta = self._six_pulses(offset.get("rightPulseDelta"))
        left_valid = bool(offset.get("leftValid", offset.get("valid", False)))
        right_valid = bool(offset.get("rightValid", offset.get("valid", False)))
        updated_at = offset.get("updatedAt", 0)
        try:
            updated_at = int(updated_at)
        except (TypeError, ValueError):
            updated_at = 0
        return {
            "valid": bool(left_valid and right_valid),
            "leftValid": left_valid,
            "rightValid": right_valid,
            "leftPulseDelta": left_delta,
            "rightPulseDelta": right_delta,
            "updatedAt": updated_at,
        }

    def _set_home_reference_side(
        self,
        home_reference: dict[str, object],
        side: str,
        pulses: list[float],
        updated_at: int,
    ) -> None:
        offset = 0 if side == "left" else 6
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        valid_key = "leftValid" if side == "left" else "rightValid"
        home_reference[pulse_key] = list(pulses[offset : offset + 6])
        home_reference[valid_key] = True
        home_reference["valid"] = bool(home_reference["leftValid"] and home_reference["rightValid"])
        home_reference["updatedAt"] = updated_at

    def _set_work_origin_offset_side(
        self,
        work_origin_offset: dict[str, object],
        home_reference: dict[str, object],
        side: str,
        work_origin_pulse: list[float],
        updated_at: int,
    ) -> None:
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        delta_key = "leftPulseDelta" if side == "left" else "rightPulseDelta"
        valid_key = "leftValid" if side == "left" else "rightValid"
        reference_valid = bool(home_reference.get(valid_key, home_reference.get("valid", False)))
        reference_pulse = cast(list[float], home_reference[pulse_key])
        work_origin_offset[delta_key] = [
            float(work_origin_pulse[index]) - float(reference_pulse[index])
            for index in range(6)
        ] if reference_valid else [0.0] * 6
        work_origin_offset[valid_key] = reference_valid
        work_origin_offset["valid"] = bool(work_origin_offset["leftValid"] and work_origin_offset["rightValid"])
        work_origin_offset["updatedAt"] = updated_at

    def _apply_home_reference_offset(
        self,
        origin: dict[str, object],
        home_reference: dict[str, object],
        work_origin_offset: dict[str, object],
        side: str,
        updated_at: int,
    ) -> None:
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        delta_key = "leftPulseDelta" if side == "left" else "rightPulseDelta"
        valid_key = "leftValid" if side == "left" else "rightValid"
        reference_pulse = cast(list[float], home_reference[pulse_key])
        delta_pulse = cast(list[float], work_origin_offset[delta_key])
        origin[pulse_key] = [reference_pulse[index] + delta_pulse[index] for index in range(6)]
        origin[valid_key] = True
        origin["valid"] = bool(origin["leftValid"] and origin["rightValid"])
        origin["updatedAt"] = updated_at

    def _six_pulses(self, value: object) -> list[float]:
        if not isinstance(value, list):
            return [0.0] * 6
        pulses = [float(item) for item in value[:6]]
        return pulses + [0.0] * max(0, 6 - len(pulses))
