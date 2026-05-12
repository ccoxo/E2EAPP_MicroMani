from __future__ import annotations

from backend.core.defaults import default_config
from backend.services.gripper_tele_service import SchmittGripper


def test_gripper_teleop_default_gap_range_reaches_close_threshold() -> None:
    cfg = default_config()["teleop"]["gripperTeleop"]
    schmitt = SchmittGripper("left")

    assert cfg["leftGapMaxMm"] == 25.0
    assert schmitt.update(18.0, cfg) == "close"
    assert schmitt.update(18.0, cfg) is None
    assert schmitt.update(6.0, cfg) == "open"


def test_gripper_teleop_button_fallback_is_edge_triggered() -> None:
    schmitt = SchmittGripper("right")

    assert schmitt.update_button(True) == "close"
    assert schmitt.update_button(True) is None
    assert schmitt.update_button(False) == "open"
    assert schmitt.update_button(False) is None
