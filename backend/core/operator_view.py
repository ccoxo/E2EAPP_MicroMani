from __future__ import annotations

from typing import Literal

SideName = Literal["left", "right"]


def hardware_side_for_operator_side(side: SideName) -> SideName:
    return "right" if side == "left" else "left"


def operator_side_for_hardware_side(side: SideName) -> SideName:
    return "right" if side == "left" else "left"


def operator_gripper_source_for_side(side: SideName) -> str:
    return "PhysicalLeft" if side == "left" else "PhysicalRight"


def gripper_source_for_hardware_side(side: SideName) -> str:
    operator_side = operator_side_for_hardware_side(side)
    return operator_gripper_source_for_side(operator_side)
