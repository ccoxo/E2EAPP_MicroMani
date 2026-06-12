from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.core.units import motion_pulse_per_unit, pulse_to_ui

AXIS_KEYS = ("x", "y", "z", "roll", "pitch", "yaw")
AXIS_NAMES = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
ROTATION_WORK_DEFAULTS = {
    "roll": {"min": -5.0, "max": 95.0},
    "pitch": {"min": -30.0, "max": 30.0},
    "yaw": {"min": -7.0, "max": 7.0},
}
TRANSLATION_SOFT_LIMIT_DISABLED_MIN = -1_000_000_000.0
TRANSLATION_SOFT_LIMIT_DISABLED_MAX = 1_000_000_000.0


@dataclass(frozen=True)
class AxisLimit:
    min: float
    max: float


class WorkOriginMissing(RuntimeError):
    pass


def side_offset(side: str) -> int:
    return 0 if side == "left" else 6


def config_limit_to_ui(value: Any, axis_index: int) -> float:
    parsed = float(value)
    return parsed / 1000.0 if axis_index >= 3 else parsed


def ui_limit_to_config(value: float, axis_index: int) -> float:
    return float(value) * 1000.0 if axis_index >= 3 else float(value)


def pulse_to_axis_ui(config: dict[str, Any], side: str, axis_index: int, pulse: float) -> float:
    offset = side_offset(side)
    pulse_per_unit = motion_pulse_per_unit(config)[offset + axis_index]
    return pulse_to_ui(float(pulse), offset + axis_index, pulse_per_unit)


def side_positions_ui(config: dict[str, Any], side: str, pulses: list[float]) -> list[float]:
    offset = side_offset(side)
    values = (list(pulses) + [0.0] * 12)[:12]
    return [pulse_to_axis_ui(config, side, axis_index, values[offset + axis_index]) for axis_index in range(6)]


def side_origin_ui(config: dict[str, Any], side: str) -> list[float] | None:
    origin = config.get("motion", {}).get("origin", {})
    if not isinstance(origin, dict):
        return None
    valid_key = "leftValid" if side == "left" else "rightValid"
    if not bool(origin.get(valid_key, origin.get("valid", False))):
        return None
    pulse_key = "leftPulse" if side == "left" else "rightPulse"
    raw = origin.get(pulse_key)
    if not isinstance(raw, list) or len(raw) < 6:
        return None
    try:
        pulses = [float(value) for value in raw[:6]]
    except (TypeError, ValueError):
        return None
    return [pulse_to_axis_ui(config, side, axis_index, pulses[axis_index]) for axis_index in range(6)]


def side_home_reference_ui(config: dict[str, Any], side: str) -> list[float] | None:
    reference = config.get("motion", {}).get("homeReference", {})
    if not isinstance(reference, dict):
        return None
    valid_key = "leftValid" if side == "left" else "rightValid"
    if not bool(reference.get(valid_key, reference.get("valid", False))):
        return None
    pulse_key = "leftPulse" if side == "left" else "rightPulse"
    raw = reference.get(pulse_key)
    if not isinstance(raw, list) or len(raw) < 6:
        return None
    try:
        pulses = [float(value) for value in raw[:6]]
    except (TypeError, ValueError):
        return None
    return [pulse_to_axis_ui(config, side, axis_index, pulses[axis_index]) for axis_index in range(6)]


def mechanical_limits_ui(config: dict[str, Any], side: str) -> list[AxisLimit]:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    raw_limits = motion.get(f"{side}SoftLimits", {}) if isinstance(motion, dict) else {}
    limits: list[AxisLimit] = []
    for axis_index, axis_key in enumerate(AXIS_KEYS):
        raw_axis = raw_limits.get(axis_key, {}) if isinstance(raw_limits, dict) else {}
        min_value = raw_axis.get("min", 0.0) if isinstance(raw_axis, dict) else 0.0
        max_value = raw_axis.get("max", 0.0) if isinstance(raw_axis, dict) else 0.0
        limits.append(
            AxisLimit(
                config_limit_to_ui(min_value, axis_index),
                config_limit_to_ui(max_value, axis_index),
            )
        )
    return limits


def rotation_work_limit_enabled(config: dict[str, Any]) -> bool:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    raw = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    return isinstance(raw, dict) and bool(raw.get("enabled", False))


def rotation_work_limits_ui(config: dict[str, Any], side: str) -> list[AxisLimit]:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    raw_root = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    raw_side = raw_root.get(side, {}) if isinstance(raw_root, dict) else {}
    limits = [AxisLimit(0.0, 0.0), AxisLimit(0.0, 0.0), AxisLimit(0.0, 0.0)]
    for axis_key in AXIS_KEYS[3:]:
        raw_axis = raw_side.get(axis_key, {}) if isinstance(raw_side, dict) else {}
        default_axis = ROTATION_WORK_DEFAULTS[axis_key]
        if side == "right" and axis_key == "roll":
            default_axis = {"min": -95.0, "max": 5.0}
        if side == "right" and axis_key == "pitch":
            default_axis = {"min": -30.0, "max": 30.0}
        min_value = raw_axis.get("min", default_axis["min"]) if isinstance(raw_axis, dict) else default_axis["min"]
        max_value = raw_axis.get("max", default_axis["max"]) if isinstance(raw_axis, dict) else default_axis["max"]
        limits.append(AxisLimit(float(min_value), float(max_value)))
    return limits


def effective_limits_ui(config: dict[str, Any], side: str) -> list[AxisLimit]:
    limits = mechanical_limits_ui(config, side)
    for axis_index in range(3):
        limits[axis_index] = AxisLimit(
            TRANSLATION_SOFT_LIMIT_DISABLED_MIN,
            TRANSLATION_SOFT_LIMIT_DISABLED_MAX,
        )
    if not rotation_work_limit_enabled(config):
        return limits
    home_reference = side_home_reference_ui(config, side)
    if home_reference is None:
        raise WorkOriginMissing(
            f"home_reference_missing: {side} rotation work limit requires captured hardware zero"
        )
    work_limits = rotation_work_limits_ui(config, side)
    effective = list(limits)
    for axis_index in range(3, 6):
        work_limit = work_limits[axis_index]
        effective[axis_index] = AxisLimit(
            max(limits[axis_index].min, home_reference[axis_index] + work_limit.min),
            min(limits[axis_index].max, home_reference[axis_index] + work_limit.max),
        )
    return effective


def mechanical_limit_arrays(config: dict[str, Any], side: str) -> tuple[list[float], list[float]]:
    limits = mechanical_limits_ui(config, side)
    return [limit.min for limit in limits], [limit.max for limit in limits]


def manual_axis_limits_ui(config: dict[str, Any], side: str) -> list[AxisLimit]:
    limits = mechanical_limits_ui(config, side)
    for axis_index in range(3):
        limits[axis_index] = AxisLimit(
            TRANSLATION_SOFT_LIMIT_DISABLED_MIN,
            TRANSLATION_SOFT_LIMIT_DISABLED_MAX,
        )
    return limits


def effective_limit_arrays(config: dict[str, Any], side: str) -> tuple[list[float], list[float]]:
    limits = effective_limits_ui(config, side)
    return [limit.min for limit in limits], [limit.max for limit in limits]


def native_teleop_limit_arrays(config: dict[str, Any], side: str) -> tuple[list[float], list[float]]:
    limits = mechanical_limits_ui(config, side)
    for axis_index in range(3):
        limits[axis_index] = AxisLimit(
            TRANSLATION_SOFT_LIMIT_DISABLED_MIN,
            TRANSLATION_SOFT_LIMIT_DISABLED_MAX,
        )
    return [limit.min for limit in limits], [limit.max for limit in limits]


def rotation_work_limit_arrays(config: dict[str, Any], side: str) -> tuple[list[float], list[float]]:
    limits = rotation_work_limits_ui(config, side)
    return [limit.min for limit in limits], [limit.max for limit in limits]


def target_allowed_with_recovery(current: float, target: float, limit: AxisLimit) -> bool:
    if limit.min > limit.max:
        return False
    if current < limit.min:
        return target > current
    if current > limit.max:
        return target < current
    return limit.min <= target <= limit.max
