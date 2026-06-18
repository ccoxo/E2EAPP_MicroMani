from __future__ import annotations

from backend.core.operator_view import (
    hardware_side_for_operator_side,
    operator_gripper_source_for_side,
    operator_side_for_hardware_side,
)


def test_operator_view_maps_left_to_existing_right_hardware() -> None:
    assert hardware_side_for_operator_side("left") == "right"
    assert hardware_side_for_operator_side("right") == "left"
    assert operator_side_for_hardware_side("right") == "left"
    assert operator_side_for_hardware_side("left") == "right"


def test_operator_view_gripper_sources_follow_same_named_operator_hand() -> None:
    assert operator_gripper_source_for_side("left") == "PhysicalLeft"
    assert operator_gripper_source_for_side("right") == "PhysicalRight"
