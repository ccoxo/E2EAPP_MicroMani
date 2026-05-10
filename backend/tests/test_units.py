from __future__ import annotations

from backend.core.units import pulse_to_lerobot, pulse_to_ui


def test_rotation_ui_uses_degrees_not_millidegrees() -> None:
    assert pulse_to_lerobot(3333.333333, 5, 3333.333333) == 1000.0
    assert pulse_to_ui(3333.333333, 5, 3333.333333) == 1.0


def test_translation_ui_uses_micrometers() -> None:
    assert pulse_to_ui(9000, 0, 9000) == 1000.0
