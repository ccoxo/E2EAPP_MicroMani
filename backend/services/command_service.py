from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any, cast

from backend.core.config import SettingsService, reanchor_motion_soft_limits_to_current_origin
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
    ) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.hal = hal
        self.logs = logs
        self.hardware = hardware
        self.gripper_workers = gripper_workers
        self._origin_mutation_locked = origin_mutation_locked or (lambda: False)

    def set_origin_mutation_lock_checker(self, checker: Callable[[], bool]) -> None:
        self._origin_mutation_locked = checker

    def _ensure_origin_mutation_allowed(self) -> None:
        if self._origin_mutation_locked():
            raise RuntimeError(ORIGIN_MUTATION_LOCKED_MESSAGE)

    async def generic_command(self, request: SettingsCommandRequest) -> dict[str, object]:
        self.logs.append(request.channel, request.level, request.msg)
        return {"accepted": True, "mode": "backend"}

    async def reconnect_hal(self) -> dict[str, object]:
        result = await self.hal.command("hal.reconnect")
        self.logs.info("[HAL]", "HAL reconnect requested")
        return result

    async def emergency_stop(self) -> dict[str, object]:
        # 先更新本地遥测状态，再请求 HAL 急停，让 UI 能立即进入安全态。
        self.telemetry.emergency_stop()
        result = await self.hal.command("motion.emergency_stop")
        self.logs.error("[SAFETY]", "hardware emergency stop requested")
        return result

    async def home_all(self) -> dict[str, object]:
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
        self._validate_work_origin_target(config, "left")
        self._validate_work_origin_target(config, "right")
        await self.enable_motion_side("left")
        await self.enable_motion_side("right")
        result = await self.hal.command(
            "motion.home_all",
            {
                "leftPulse": left_pulse,
                "rightPulse": right_pulse,
            },
        )
        self.telemetry.home_all()
        self._log_work_origin_moves(config, op_id, "home_all", "complete", "left", left_pulse)
        self._log_work_origin_moves(config, op_id, "home_all", "complete", "right", right_pulse)
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
        self._validate_work_origin_target(config, side)
        await self.enable_motion_side(side)
        result = await self.hal.command(
            "motion.home_origin_side",
            {
                "side": side,
                "pulse": pulse,
            },
        )
        self.telemetry.home_side(side)
        self._log_work_origin_moves(config, op_id, "home_origin_side", "complete", side, pulse)
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

    async def enable_motion_side(self, side: str) -> dict[str, object]:
        self._validate_side(side)
        result = await self.hal.command("motion.enable_side", {"side": side})
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
        result = await self.hal.command("motion.home_side", {"side": side})
        self.telemetry.home_side(side)
        side_label = "left" if side == "left" else "right"
        self.logs.info("[HAL]", f"{side_label} motion homing requested")
        return result

    def motion_origin_status(self) -> dict[str, object]:
        config = self.settings.get_config()
        return {"origin": self._normalized_motion_origin(config)}

    async def capture_motion_origin(
        self,
        side: str | None = None,
        *,
        confirm_large_drift: bool = False,
    ) -> dict[str, object]:
        if side is not None:
            self._validate_side(side)
        self._ensure_origin_mutation_allowed()
        for home_side in (("left", "right") if side is None else (side,)):
            await self.hal.command("motion.home_side", {"side": home_side})
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        drift = self._motion_origin_capture_drift(config, origin, pulses, side)
        if bool(drift["requiresConfirmation"]) and not confirm_large_drift:
            label = side or "both"
            self.logs.warning("[HAL]", f"{label} motion hardware zero recording blocked by large drift")
            raise MotionOriginDriftConfirmationRequired(drift)
        if bool(origin["valid"]):
            origin["previousValid"] = True
            origin["previousLeftPulse"] = list(cast(list[float], origin["leftPulse"]))
            origin["previousRightPulse"] = list(cast(list[float], origin["rightPulse"]))
            origin["previousUpdatedAt"] = int(origin["updatedAt"])
        if side in {None, "left"}:
            origin["leftPulse"] = pulses[:6]
            origin["leftValid"] = True
        if side in {None, "right"}:
            origin["rightPulse"] = pulses[6:12]
            origin["rightValid"] = True
        origin["valid"] = bool(origin["leftValid"] and origin["rightValid"])
        origin["updatedAt"] = now_ms()
        config["motion"]["origin"] = origin
        reanchor_motion_soft_limits_to_current_origin(config, side)
        saved = self.settings.save_config(config, emit_log=False)
        label = side or "both"
        self.logs.info("[HAL]", f"{label} motion hardware zero recorded")
        return {"origin": saved["motion"]["origin"], "config": saved, "originCaptureDrift": drift}

    def restore_previous_motion_origin(self) -> dict[str, object]:
        self._ensure_origin_mutation_allowed()
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        if not bool(origin["previousValid"]):
            raise RuntimeError("previous motion work origin is not available")
        current_valid = bool(origin["valid"])
        current_left = list(cast(list[float], origin["leftPulse"]))
        current_right = list(cast(list[float], origin["rightPulse"]))
        current_updated_at = int(origin["updatedAt"])
        origin["leftPulse"] = list(cast(list[float], origin["previousLeftPulse"]))
        origin["rightPulse"] = list(cast(list[float], origin["previousRightPulse"]))
        origin["updatedAt"] = int(origin["previousUpdatedAt"])
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
        saved = self.settings.save_config(config, emit_log=False)
        label = side or "both"
        self.logs.info("[HAL]", f"{label} motion hardware zero record cleared")
        return {"origin": saved["motion"]["origin"], "config": saved}

    async def acknowledge_safety(self) -> dict[str, object]:
        self.telemetry.acknowledge_safety()
        self.logs.info("[SAFETY]", "safety state acknowledged")
        return {"accepted": True}

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
            await self._validate_manual_axis_soft_limit(config, request, effective_direction)
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
        if self.hardware is not None and self._real_hardware_mode(config) and self._hal_native_teleop(config):
            target = self._gripper_command_target(config, request)
            side_label = "left gripper" if request.side == "left" else "right gripper"
            if target is None:
                if request.command == "enable":
                    target_key = "targetLeftMm" if request.side == "left" else "targetRightMm"
                    target = min(
                        max(float(config.get("gripper", {}).get(target_key, 0.0)), 0.0),
                        float(config.get("gripper", {}).get("strokeMm", 26.0)),
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
            use_gripper_workers = self.gripper_workers is not None and self.gripper_workers.is_enabled(config)
            gripper_backend = "dual_worker" if use_gripper_workers else "python_rs485"
            if use_gripper_workers:
                result = self.gripper_workers.command(config, request.side, request.command, request.targetMm)
            else:
                result = self.hardware.gripper.command(config, request.side, request.command, request.targetMm)
            if not result.ok:
                self._log_gripper_command(
                    config,
                    request,
                    backend=gripper_backend,
                    target=self._gripper_command_target(config, request),
                    run_ret=result.ok,
                    ipc_ok=True,
                    error=result.message,
                )
                self.logs.error("[GRIPPER]", result.message)
                raise RuntimeError(result.message)
            target = self._gripper_command_target(config, request)
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
            response: dict[str, object] = {"message": result.message}
            if target is not None:
                response["targetMm"] = target
            return response
        target = self.telemetry.apply_gripper(request.side, request.command, request.targetMm)
        config = self.settings.get_config()
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
    ) -> dict[str, object]:
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
            "current": "unknown",
            "requestedDelta": request.direction * request.step,
            "safeDelta": safe_delta,
            "target": "unknown",
            "limit": "unknown",
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
    ) -> None:
        motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
        kinematics = motion.get("kinematics", {}) if isinstance(motion.get("kinematics"), dict) else {}
        axis_map = kinematics.get(f"{side}PhysicalAxis", kinematics.get(f"{side}AxisMap", list(range(6))))
        for axis_index, axis_name in enumerate(AXIS_LABELS):
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
                current="unknown",
                requestedTarget=pulses[axis_index],
                safeTarget=pulses[axis_index],
                limit="origin",
                clip="none",
                dmcRet="accepted",
                busyAxes="unknown",
                summary="requested",
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
            speed=gripper_teleop.get("gripSpeed", gripper.get("speed", "")),
            torque=gripper_teleop.get("gripTorque", gripper.get("torque", "")),
            runRet=run_ret,
            ipcOk=ipc_ok,
            error=error,
        )

    def _gripper_command_target(self, config: dict[str, Any], request: GripperCommandRequest) -> float | None:
        stroke = float(config["gripper"].get("strokeMm", 26))
        if request.command == "open":
            return stroke
        if request.command in {"close", "home"}:
            return 0.0
        if request.command == "target":
            return min(max(float(request.targetMm if request.targetMm is not None else 0.0), 0.0), stroke)
        return None

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
            "gripTorque": int(gripper_teleop.get("gripTorque", 192)),
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
    ) -> None:
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        axis_index = AXIS_ORDER.index(request.axis)
        limits = manual_axis_limits_ui(config, request.side)
        current = side_positions_ui(config, request.side, pulses)[axis_index]
        target = current + request.step * effective_direction
        limit = limits[axis_index]
        if not target_allowed_with_recovery(current, target, limit):
            raise RuntimeError(
                f"{request.side} {request.axis} target exceeds soft limit: "
                f"{target:.3f} not allowed from {current:.3f} with [{limit.min:.3f}, {limit.max:.3f}]"
            )

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

    def _validate_work_origin_target(self, config: dict[str, Any], side: str) -> None:
        origin = side_origin_ui(config, side)
        if origin is None:
            raise RuntimeError(f"{side} motion work origin is not captured")
        try:
            limits = effective_limits_ui(config, side)
        except WorkOriginMissing as exc:
            raise RuntimeError(str(exc)) from exc
        for axis_index, axis_name in enumerate(AXIS_ORDER):
            limit = limits[axis_index]
            target = origin[axis_index]
            if limit.min > limit.max or target < limit.min or target > limit.max:
                raise RuntimeError(
                    f"{side} {axis_name} work origin exceeds soft limit: "
                    f"{target:.3f} not in [{limit.min:.3f}, {limit.max:.3f}]"
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

    def _six_pulses(self, value: object) -> list[float]:
        if not isinstance(value, list):
            return [0.0] * 6
        pulses = [float(item) for item in value[:6]]
        return pulses + [0.0] * max(0, 6 - len(pulses))
