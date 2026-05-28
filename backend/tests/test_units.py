from __future__ import annotations

from backend.core.defaults import default_config
from backend.core.units import motion_pulse_per_unit, pulse_to_lerobot, pulse_to_ui, pulses_to_ui_state


def test_rotation_ui_uses_degrees_not_millidegrees() -> None:
    assert pulse_to_lerobot(3333.333333, 5, 3333.333333) == 1000.0
    assert pulse_to_ui(3333.333333, 5, 3333.333333) == 1.0


def test_translation_ui_uses_micrometers() -> None:
    assert pulse_to_ui(9000, 0, 9000) == 1000.0


def test_motion_pulse_per_unit_uses_runtime_kinematics_config() -> None:
    config = default_config()
    config["motion"]["kinematics"]["leftSignedPulsePerUnit"][0] = -5000.0
    config["motion"]["kinematics"]["rightSignedPulsePerUnit"][2] = -5000.0

    values = motion_pulse_per_unit(config)

    assert values[0] == -5000.0
    assert values[8] == -5000.0
    assert pulses_to_ui_state([-5000.0] + [0.0] * 11, config)[0] == 1000.0
