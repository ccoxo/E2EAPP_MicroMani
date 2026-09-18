# 阅读导航 03｜后端契约与配置
# 职责：在运动脉冲、界面单位和 LeRobot 状态单位之间换算；旋转角度与毫度须区分。
# 先看：pulse_to_lerobot → pulse_to_ui → ui_to_lerobot_state → lerobot_to_ui_state。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

from typing import Any

from backend.core.defaults import ICF_KINEMATICS_DEFAULTS

ROTATION_AXES = {3, 4, 5, 9, 10, 11}
LEFT_PULSE_PER_UNIT = tuple(float(value) for value in ICF_KINEMATICS_DEFAULTS["leftSignedPulsePerUnit"])
RIGHT_PULSE_PER_UNIT = tuple(float(value) for value in ICF_KINEMATICS_DEFAULTS["rightSignedPulsePerUnit"])
MOTION_PULSE_PER_UNIT = LEFT_PULSE_PER_UNIT + RIGHT_PULSE_PER_UNIT


def pulse_to_lerobot(pulse: float, axis_idx: int, pulse_per_unit: float) -> float:
    """Convert pulse count to LeRobot observation.state units: um or 0.001 degree."""
    return pulse / pulse_per_unit * 1000.0


# 界面平移使用 μm、旋转使用 degree；LeRobot 旋转分量使用 0.001 degree，不能直接混用。
def pulse_to_ui(pulse: float, axis_idx: int, pulse_per_unit: float) -> float:
    """Convert pulse count to frontend TelemetryFrame units: um or degree."""
    value = pulse_to_lerobot(pulse, axis_idx, pulse_per_unit)
    if axis_idx in ROTATION_AXES:
        return value / 1000.0
    return value


def ui_to_lerobot_state(values: list[float]) -> list[float]:
    """Convert UI joint units to LeRobot state units: um and 0.001 degree."""
    return [float(value) * 1000.0 if idx in ROTATION_AXES else float(value) for idx, value in enumerate(values)]


def lerobot_to_ui_state(values: list[float]) -> list[float]:
    """Convert LeRobot state units back to UI units: um and degree."""
    return [float(value) / 1000.0 if idx in ROTATION_AXES else float(value) for idx, value in enumerate(values)]


def motion_pulse_per_unit(
    config: dict[str, Any] | None = None,
    *,
    side_order: str = "hardware",
) -> tuple[float, ...]:
    """Return signed pulse-per-unit in hardware or operator/dataset side order."""
    motion = config.get("motion", {}) if isinstance(config, dict) else {}
    kinematics = motion.get("kinematics", {}) if isinstance(motion, dict) else {}
    if not isinstance(kinematics, dict):
        kinematics = ICF_KINEMATICS_DEFAULTS
    left = _side_pulse_per_unit("left", kinematics)
    right = _side_pulse_per_unit("right", kinematics)
    return left + right if side_order == "hardware" else right + left


def pulses_to_ui_state(pulses: list[float], config: dict[str, Any] | None = None) -> list[float]:
    """Convert signed LTDMC pulse counts to frontend units for all 12 axes."""
    values = (list(pulses) + [0.0] * 12)[:12]
    pulse_per_unit = motion_pulse_per_unit(config)
    return [
        pulse_to_ui(float(pulse), idx, pulse_per_unit[idx])
        for idx, pulse in enumerate(values)
    ]


def dataset_pulses_to_ui_state(pulses: list[float], config: dict[str, Any] | None = None) -> list[float]:
    """Convert dataset/operator-order pulses using the matching hardware calibration."""
    values = (list(pulses) + [0.0] * 12)[:12]
    pulse_per_unit = motion_pulse_per_unit(config, side_order="dataset")
    return [pulse_to_ui(float(pulse), idx, pulse_per_unit[idx]) for idx, pulse in enumerate(values)]


def _side_pulse_per_unit(side: str, kinematics: dict[str, Any]) -> tuple[float, ...]:
    defaults = ICF_KINEMATICS_DEFAULTS
    signed_key = f"{side}SignedPulsePerUnit"
    signed = _coerce_axis_array(kinematics.get(signed_key))
    if signed is not None:
        return signed
    pulse_key = f"{side}PulsePerUnit"
    direction_key = f"{side}DirectionSign"
    pulse_per_unit = _coerce_axis_array(kinematics.get(pulse_key))
    direction = _coerce_axis_array(kinematics.get(direction_key))
    if pulse_per_unit is not None and direction is not None:
        return tuple(pulse_per_unit[index] * direction[index] for index in range(6))
    return tuple(float(value) for value in defaults[signed_key])


def _coerce_axis_array(raw: Any) -> tuple[float, ...] | None:
    if not isinstance(raw, list) or len(raw) != 6:
        return None
    try:
        values = tuple(float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if any(value == 0.0 for value in values):
        return None
    return values
