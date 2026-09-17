"""Numeric side-order contract shared by recording and policy boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.core.operator_view import hardware_side_for_operator_side, operator_side_for_hardware_side

DATA_CONTRACT_VERSION = "appstation.dual_arm.operator_sides.v2"
DATASET_SIDE_ORDER = "operator_left_then_operator_right"
DATASET_CONTRACT_METADATA: dict[str, Any] = {
    "version": DATA_CONTRACT_VERSION,
    "sideOrder": DATASET_SIDE_ORDER,
    "hardwareSideForDatasetSide": {"left": "right", "right": "left"},
    "stateOrder": [
        "operator_left.x_um",
        "operator_left.y_um",
        "operator_left.z_um",
        "operator_left.roll_mdeg",
        "operator_left.pitch_mdeg",
        "operator_left.yaw_mdeg",
        "operator_left.gripper_gap_mm",
        "operator_right.x_um",
        "operator_right.y_um",
        "operator_right.z_um",
        "operator_right.roll_mdeg",
        "operator_right.pitch_mdeg",
        "operator_right.yaw_mdeg",
        "operator_right.gripper_gap_mm",
    ],
    "actionOrder": [
        "operator_left.x_um",
        "operator_left.y_um",
        "operator_left.z_um",
        "operator_left.roll_mdeg",
        "operator_left.pitch_mdeg",
        "operator_left.yaw_mdeg",
        "operator_left.gripper_gap_mm",
        "operator_right.x_um",
        "operator_right.y_um",
        "operator_right.z_um",
        "operator_right.roll_mdeg",
        "operator_right.pitch_mdeg",
        "operator_right.yaw_mdeg",
        "operator_right.gripper_gap_mm",
    ],
    "pulsesOrder": [
        "operator_left.x_pulse",
        "operator_left.y_pulse",
        "operator_left.z_pulse",
        "operator_left.roll_pulse",
        "operator_left.pitch_pulse",
        "operator_left.yaw_pulse",
        "operator_right.x_pulse",
        "operator_right.y_pulse",
        "operator_right.z_pulse",
        "operator_right.roll_pulse",
        "operator_right.pitch_pulse",
        "operator_right.yaw_pulse",
    ],
    "forceOrder": ["operator_left", "operator_right"],
    "forceSideSemantics": "observation.force_left/right use dataset operator side names",
    "units": {"translation": "um", "rotation": "mdeg", "gripper": "mm", "force": "SI"},
}


def data_contract_metadata() -> dict[str, Any]:
    return deepcopy(DATASET_CONTRACT_METADATA)


def validate_data_contract(metadata: Any) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("data contract metadata is missing; manual migration or confirmation is required")
    if metadata.get("version") != DATA_CONTRACT_VERSION:
        version = metadata.get("version")
        if version is None:
            raise ValueError("data contract version is missing; manual migration or confirmation is required")
        raise ValueError(f"unsupported data contract version: {version}")
    for key in (
        "sideOrder",
        "hardwareSideForDatasetSide",
        "stateOrder",
        "actionOrder",
        "pulsesOrder",
        "forceOrder",
        "forceSideSemantics",
        "units",
    ):
        if metadata.get(key) != DATASET_CONTRACT_METADATA[key]:
            raise ValueError(f"data contract {key} does not match {DATA_CONTRACT_VERSION}")


def hardware_to_dataset_motion(values: list[float]) -> list[float]:
    padded = (list(values) + [0.0] * 12)[:12]
    return padded[6:12] + padded[0:6]


def dataset_to_hardware_motion(values: list[float]) -> list[float]:
    return hardware_to_dataset_motion(values)


def hardware_to_dataset_grippers(values: list[float]) -> list[float]:
    padded = (list(values) + [0.0] * 2)[:2]
    return [float(padded[1]), float(padded[0])]


def dataset_to_hardware_state(values: list[float]) -> list[float]:
    padded = (list(values) + [0.0] * 14)[:14]
    return [*padded[7:13], padded[13], *padded[0:6], padded[6]]


def hardware_to_dataset_state(values: list[float]) -> list[float]:
    return dataset_to_hardware_state(values)


def dataset_side_to_hardware_side(side: str) -> str:
    return hardware_side_for_operator_side(side)  # type: ignore[arg-type]


def hardware_side_to_dataset_side(side: str) -> str:
    return operator_side_for_hardware_side(side)  # type: ignore[arg-type]
