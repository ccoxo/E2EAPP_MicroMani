from __future__ import annotations

from backend.core.defaults import default_config
from backend.services.gripper_tele_service import FollowGripper, GripperTeleService


def test_gripper_teleop_maps_gap_to_continuous_target() -> None:
    cfg = default_config()["teleop"]["gripperTeleop"]
    follower = FollowGripper("left")

    assert cfg["leftGapMaxMm"] == 25.0
    assert follower.update(0.0, cfg, 26.0, now_ms=0) == (0.0, 255)
    assert follower.update(12.5, cfg, 26.0, now_ms=10) == (13.0, 128)
    assert follower.update(25.0, cfg, 26.0, now_ms=20) == (26.0, 0)


def test_gripper_teleop_respects_raw_position_deadband() -> None:
    cfg = {
        **default_config()["teleop"]["gripperTeleop"],
        "positionDeadbandCounts": 2,
        "minCommandIntervalMs": 50,
    }
    follower = FollowGripper("right")

    assert follower.update(12.5, cfg, 26.0, now_ms=0) == (13.0, 128)
    assert follower.update(12.55, cfg, 26.0, now_ms=10) is None
    assert follower.update(12.55, cfg, 26.0, now_ms=60) == (13.052, 127)


def test_gripper_teleop_button_fallback_is_edge_triggered() -> None:
    cfg = default_config()["teleop"]["gripperTeleop"]
    follower = FollowGripper("right")

    assert follower.update_button(True, cfg, 26.0, now_ms=0) == (0.0, 255)
    assert follower.update_button(True, cfg, 26.0, now_ms=10) is None
    assert follower.update_button(False, cfg, 26.0, now_ms=20) == (26.0, 0)
    assert follower.update_button(False, cfg, 26.0, now_ms=30) is None


def test_gripper_teleop_auto_calibrates_offset_gap_range() -> None:
    cfg = {
        **default_config()["teleop"]["gripperTeleop"],
        "leftGapMinMm": 0.0,
        "leftGapMaxMm": 25.0,
        "autoGapCalibration": True,
        "autoGapMinSpanMm": 2.0,
        "autoGapMarginMm": 1.0,
    }
    follower = FollowGripper("left")

    assert follower.update(-20.0, cfg, 26.0, now_ms=0) == (0.0, 255)
    assert follower.update(-10.0, cfg, 26.0, now_ms=60) == (26.0, 0)
    assert follower.update(-15.0, cfg, 26.0, now_ms=120) == (13.0, 128)


def test_gripper_teleop_selects_configured_source_hand() -> None:
    hands = {
        "left": {"side": "left", "gripperGapMm": 4.0},
        "right": {"side": "right", "gripperGapMm": 20.0},
    }
    cfg = {"leftSourceHand": "PhysicalRight"}

    assert GripperTeleService._select_source_hand(hands, cfg, "left") is hands["right"]
