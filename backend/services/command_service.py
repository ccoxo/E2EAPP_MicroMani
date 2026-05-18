from __future__ import annotations

import os
from typing import Any, cast

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.core.schemas import GripperCommandRequest, ManualAxisMoveRequest, SettingsCommandRequest
from backend.core.units import motion_pulse_per_unit, pulses_to_ui_state
from backend.hal_client.client import HalClient
from backend.services.hardware_service import HardwareService
from backend.services.telemetry_hub import TelemetryHub

AXIS_ORDER = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
MANUAL_AXIS_STEP_LIMIT_PULSE = 100000.0
UNREADABLE_SEVON_FEEDBACK_AXES: set[tuple[str, str]] = {("right", axis) for axis in AXIS_ORDER}
MANUAL_AXIS_DIRECTION_SIGN: dict[str, list[int]] = {
    "left": [-1, -1, -1, -1, 1, -1],
    "right": [-1, -1, -1, -1, -1, 1],
}


def axis_enabled_feedback_unreadable(side: str, axis: str) -> bool:
    return (side, axis) in UNREADABLE_SEVON_FEEDBACK_AXES


def normalize_motion_axis_enabled(side: str, values: list[Any]) -> list[bool | None]:
    normalized: list[bool | None] = [bool(value) for value in values[:6]]
    for axis_index, axis in enumerate(AXIS_ORDER[: len(normalized)]):
        if axis_enabled_feedback_unreadable(side, axis) and normalized[axis_index] is not True:
            normalized[axis_index] = None
    return normalized


class CommandService:
    def __init__(
        self,
        settings: SettingsService,
        telemetry: TelemetryHub,
        hal: HalClient,
        logs: LogService,
        hardware: HardwareService | None = None,
        gripper_workers: Any | None = None,
    ) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self.hal = hal
        self.logs = logs
        self.hardware = hardware
        self.gripper_workers = gripper_workers

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
        origin = self._normalized_motion_origin(config)
        if not bool(origin["valid"]):
            raise RuntimeError("motion work origin is not captured")
        left_pulse = cast(list[float], origin["leftPulse"])
        right_pulse = cast(list[float], origin["rightPulse"])
        result = await self.hal.command(
            "motion.home_all",
            {
                "leftPulse": left_pulse,
                "rightPulse": right_pulse,
            },
        )
        self.telemetry.home_all()
        self.logs.info("[HAL]", "home all requested")
        return result

    async def return_motion_origin_side(self, side: str) -> dict[str, object]:
        self._validate_side(side)
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
        valid_key = "leftValid" if side == "left" else "rightValid"
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        if not bool(origin[valid_key]):
            raise RuntimeError(f"{side} motion work origin is not captured")
        pulse = cast(list[float], origin[pulse_key])
        result = await self.hal.command(
            "motion.home_origin_side",
            {
                "side": side,
                "pulse": pulse,
            },
        )
        self.telemetry.home_side(side)
        side_label = "left" if side == "left" else "right"
        self.logs.info("[HAL]", f"{side_label} return-to-work-origin requested")
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

    async def capture_motion_origin(self, side: str | None = None) -> dict[str, object]:
        if side is not None:
            self._validate_side(side)
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        config = self.settings.get_config()
        origin = self._normalized_motion_origin(config)
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
        saved = self.settings.save_config(config, emit_log=False)
        label = side or "both"
        self.logs.info("[HAL]", f"{label} motion software origin captured")
        return {"origin": saved["motion"]["origin"], "config": saved}

    def restore_previous_motion_origin(self) -> dict[str, object]:
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
        saved = self.settings.save_config(config, emit_log=False)
        self.logs.info("[HAL]", "previous motion work origin restored")
        return {"origin": saved["motion"]["origin"], "config": saved}

    def clear_motion_origin(self, side: str | None = None) -> dict[str, object]:
        if side is not None:
            self._validate_side(side)
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
        self.logs.info("[HAL]", f"{label} motion software origin cleared")
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
        self._validate_manual_axis_safety(config, request)
        effective_direction = self._manual_axis_effective_direction(request.side, request.axis, request.direction)
        if self._real_hardware_mode(config):
            await self._validate_motion_axis_enabled(request.side, request.axis)
            await self._validate_manual_axis_soft_limit(config, request, effective_direction)
            profile = self._axis_profile(config, request)
            hal_result = await self.hal.command(
                "motion.manual_axis_move",
                {
                    "side": request.side,
                    "axis": request.axis,
                    "direction": effective_direction,
                    "requestedDirection": request.direction,
                    "step": request.step,
                    "speedMode": request.speedMode,
                    "maxVelocityUiPerSec": profile["maxVelocity"],
                    "startVelocityUiPerSec": profile["startVelocity"],
                    "accTimeSec": profile["accTime"],
                    "decTimeSec": profile["decTime"],
                },
            )
            side_label = "left" if request.side == "left" else "right"
            self.logs.info(
                "[HAL]",
                f"{side_label} {request.axis} {effective_direction * request.step:+.3f} "
                f"{request.speedMode} HAL accepted",
            )
            return {"hal": hal_result}
        applied = self.telemetry.apply_axis_move(request.side, request.axis, effective_direction, request.step)
        side_label = "left" if request.side == "left" else "right"
        self.logs.info(
            "[HAL]",
            f"{side_label} {request.axis} {applied:+.3f} {request.speedMode} local fallback",
        )
        return {"applied": applied}

    async def gripper_command(self, request: GripperCommandRequest) -> dict[str, object]:
        config = self.settings.get_config()
        self._validate_gripper_command_enabled(config, request)
        if self.hardware is not None and self._real_hardware_mode(config):
            # 真机成功响应后才保存目标开合度，避免 UI 记住未执行的硬件状态。
            if self.gripper_workers is not None and self.gripper_workers.is_enabled(config):
                result = self.gripper_workers.command(config, request.side, request.command, request.targetMm)
            else:
                result = self.hardware.gripper.command(config, request.side, request.command, request.targetMm)
            if not result.ok:
                self.logs.error("[GRIPPER]", result.message)
                raise RuntimeError(result.message)
            target = self._gripper_command_target(config, request)
            self._save_gripper_command_state(config, request, target)
            side_label = "left gripper" if request.side == "left" else "right gripper"
            self.logs.info("[GRIPPER]", f"{side_label} {request.command}: {result.message}")
            response: dict[str, object] = {"message": result.message}
            if target is not None:
                response["targetMm"] = target
            return response
        target = self.telemetry.apply_gripper(request.side, request.command, request.targetMm)
        config = self.settings.get_config()
        self._save_gripper_command_state(config, request, target)
        side_label = "left gripper" if request.side == "left" else "right gripper"
        self.logs.info("[GRIPPER]", f"{side_label} {request.command} -> {target:.1f} mm local fallback")
        return {"targetMm": target}

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

    def _validate_gripper_command_enabled(self, config: dict[str, Any], request: GripperCommandRequest) -> None:
        if request.command in {"enable", "disable", "stop"}:
            return
        enabled_key = f"{request.side}Enabled"
        if not bool(config.get("gripper", {}).get(enabled_key, False)):
            raise RuntimeError(f"{request.side} gripper is disabled; enable it before motion commands")

    def _validate_manual_axis_safety(self, config: dict[str, Any], request: ManualAxisMoveRequest) -> None:
        step_pulse = self._manual_axis_step_pulse(config, request)
        if step_pulse > MANUAL_AXIS_STEP_LIMIT_PULSE:
            limit = self._manual_axis_step_ui_limit(config, request.side, request.axis)
            unit = "um" if request.axis in {"X", "Y", "Z"} else "degree"
            raise RuntimeError(
                f"manual {request.axis} step must be <= {limit:.3f} {unit} "
                f"({MANUAL_AXIS_STEP_LIMIT_PULSE:.0f} pulse cap)"
            )
        if self._axis_profile(config, request)["maxVelocity"] <= 0:
            raise RuntimeError("manual axis velocity must be positive")

    def _manual_axis_step_pulse(self, config: dict[str, Any], request: ManualAxisMoveRequest) -> float:
        return abs(float(request.step)) * self._manual_axis_pulse_per_ui_unit(config, request.side, request.axis)

    def _manual_axis_step_ui_limit(self, config: dict[str, Any], side: str, axis: str) -> float:
        pulse_per_ui_unit = self._manual_axis_pulse_per_ui_unit(config, side, axis)
        if pulse_per_ui_unit <= 0:
            return 0.0
        return MANUAL_AXIS_STEP_LIMIT_PULSE / pulse_per_ui_unit

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
        origin = self._normalized_motion_origin(config)
        origin_valid = bool(origin["leftValid"] if request.side == "left" else origin["rightValid"])
        if not origin_valid:
            return
        state = await self.hal.motion_state()
        pulses = self._motion_state_pulses(state)
        side_offset = 0 if request.side == "left" else 6
        origin_pulses = cast(list[float], origin["leftPulse"] if request.side == "left" else origin["rightPulse"])
        relative_pulses = list(pulses)
        for index in range(6):
            relative_pulses[side_offset + index] = float(pulses[side_offset + index]) - origin_pulses[index]
        axis_order = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
        axis_index = axis_order.index(request.axis)
        state_index = side_offset + axis_index
        current = pulses_to_ui_state(relative_pulses, config)[state_index]
        target = current + request.step * effective_direction
        limit_key = request.axis.lower()
        limits_key = "leftSoftLimits" if request.side == "left" else "rightSoftLimits"
        limits = config["motion"][limits_key][limit_key]
        min_limit = float(limits["min"])
        max_limit = float(limits["max"])
        if axis_index >= 3:
            min_limit /= 1000.0
            max_limit /= 1000.0
        if target < min_limit or target > max_limit:
            raise RuntimeError(
                f"{request.side} {request.axis} target exceeds soft limit: "
                f"{target:.3f} not in [{min_limit:.3f}, {max_limit:.3f}]"
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
        speed_scale = {"coarse": 1.0, "medium": 0.5, "fine": 0.2}.get(request.speedMode, 0.5)
        max_velocity = max(0.001, max_velocity * speed_scale)
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
