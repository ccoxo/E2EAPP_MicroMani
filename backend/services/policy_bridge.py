from __future__ import annotations

from typing import Any

from backend.core.motion_limits import effective_limit_arrays
from backend.core.units import ui_to_lerobot_state

AXES = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
MOTION_INDICES = {
    "left": (0, 1, 2, 3, 4, 5),
    "right": (7, 8, 9, 10, 11, 12),
}


def lerobot_state_from_ui(joint_positions: list[float], gripper_positions: list[float]) -> list[float]:
    motion = ui_to_lerobot_state((list(joint_positions) + [0.0] * 12)[:12])
    grippers = (list(gripper_positions) + [0.0] * 2)[:2]
    return motion[:6] + [float(grippers[0])] + motion[6:12] + [float(grippers[1])]


def build_policy_action_plan(
    current_state: list[float],
    action: list[float],
    config: dict[str, Any],
    *,
    max_translation_um: float,
    max_rotation_deg: float,
    max_gripper_mm: float,
) -> dict[str, Any]:
    current = _pad14(current_state)
    target = _pad14(action)
    motion = {
        "left": _side_motion_plan("left", current, target, config, max_translation_um, max_rotation_deg),
        "right": _side_motion_plan("right", current, target, config, max_translation_um, max_rotation_deg),
    }
    return {
        "currentState": current,
        "requestedAction": target,
        "motion": motion,
        "grippers": {
            "leftMm": _clamp_gripper_target(current[6], target[6], config, max_gripper_mm),
            "rightMm": _clamp_gripper_target(current[13], target[13], config, max_gripper_mm),
        },
    }


def _side_motion_plan(
    side: str,
    current: list[float],
    target: list[float],
    config: dict[str, Any],
    max_translation_um: float,
    max_rotation_deg: float,
) -> dict[str, Any]:
    indices = MOTION_INDICES[side]
    deltas: dict[str, float] = {}
    for axis_index, state_index in enumerate(indices):
        raw_delta = float(target[state_index]) - float(current[state_index])
        if axis_index < 3:
            deltas[AXES[axis_index]] = _clamp(raw_delta, max_translation_um)
        else:
            deltas[AXES[axis_index]] = _clamp(raw_delta / 1000.0, max_rotation_deg)
    soft_limit_min, soft_limit_max = effective_limit_arrays(config, side)
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    return {
        "deltas": deltas,
        "softLimitMin": soft_limit_min,
        "softLimitMax": soft_limit_max,
        "enabledAxes": _axis_array(teleop.get(f"{side}EnabledAxes"), True),
    }


def _axis_array(raw: Any, default: bool) -> list[bool]:
    if not isinstance(raw, list) or len(raw) != 6:
        return [default] * 6
    return [bool(value) for value in raw[:6]]


def _clamp_gripper_target(current: float, target: float, config: dict[str, Any], max_step: float) -> float:
    stroke = float(config.get("gripper", {}).get("strokeMm", 26.0))
    limited = float(current) + _clamp(float(target) - float(current), max_step)
    return min(max(limited, 0.0), stroke)


def _pad14(values: list[float]) -> list[float]:
    return [float(value) for value in (list(values) + [0.0] * 14)[:14]]


def _clamp(value: float, limit: float) -> float:
    limit = abs(float(limit))
    return min(max(float(value), -limit), limit)
