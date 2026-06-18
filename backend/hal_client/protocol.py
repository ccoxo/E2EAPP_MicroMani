from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HalCommandSpec:
    method: str
    path: str


# 统一维护 backend command name 到 HAL HTTP path 的映射，避免 HTTP client 和 DDS 路径各自复制协议表。
HAL_COMMANDS: dict[str, HalCommandSpec] = {
    "hal.reconnect": HalCommandSpec("GET", "/health"),
    "motion.emergency_stop": HalCommandSpec("POST", "/motion/emergency_stop"),
    "motion.home_all": HalCommandSpec("POST", "/motion/home_all"),
    "motion.home_origin_side": HalCommandSpec("POST", "/motion/home_origin_side"),
    "motion.enable_side": HalCommandSpec("POST", "/motion/enable_side"),
    "motion.disable_side": HalCommandSpec("POST", "/motion/disable_side"),
    "motion.home_side": HalCommandSpec("POST", "/motion/home_side"),
    "motion.manual_axis_move": HalCommandSpec("POST", "/motion/manual_axis_move"),
    "motion.teleop_target_update": HalCommandSpec("POST", "/motion/teleop_target_update"),
    "motion.teleop_stop_side": HalCommandSpec("POST", "/motion/teleop_stop_side"),
    "omega7.gravity_compensation": HalCommandSpec("POST", "/omega7/gravity_compensation"),
    "omega7.zero_force_feedback": HalCommandSpec("POST", "/omega7/zero_force_feedback"),
    "teleop.native.configure": HalCommandSpec("POST", "/teleop/native/configure"),
    "teleop.native.start": HalCommandSpec("POST", "/teleop/native/start"),
    "teleop.native.stop": HalCommandSpec("POST", "/teleop/native/stop"),
    "teleop.native.status": HalCommandSpec("GET", "/teleop/native/status"),
    "teleop.native.gripper_command": HalCommandSpec("POST", "/teleop/native/gripper_command"),
    "gripper.command": HalCommandSpec("POST", "/gripper/command"),
}

_LONG_RUNNING_COMMANDS = {"motion.home_all", "motion.home_origin_side", "motion.home_side"}
_TELEOP_DELTA_AXES = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")


def command_spec(name: str) -> HalCommandSpec:
    spec = HAL_COMMANDS.get(name)
    if spec is None:
        raise RuntimeError(f"HAL command is not mapped: {name}")
    return spec


def command_request_policy(name: str, timeout_s: float, *, long_timeout_s: float = 75.0) -> tuple[float, int]:
    command_spec(name)
    if name in _LONG_RUNNING_COMMANDS:
        # 回零类命令可能跨越多轴运动，DDS/HTTP 两条路径都使用同一套长 timeout 策略。
        return max(timeout_s, long_timeout_s), 1
    return timeout_s, 2


def hal_command_payload(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if name != "motion.teleop_target_update":
        return payload
    deltas = payload.get("deltas")
    if not isinstance(deltas, dict):
        return payload
    # HAL C++ HTTP 端仍接收扁平轴字段；backend 内部可以继续用 deltas 聚合结构。
    request_payload = dict(payload)
    for axis in _TELEOP_DELTA_AXES:
        if axis in deltas:
            request_payload[axis] = deltas[axis]
    return request_payload
