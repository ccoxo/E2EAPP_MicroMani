from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.data_contract import (
    DATA_CONTRACT_VERSION,
    data_contract_metadata,
    dataset_to_hardware_state,
    hardware_to_dataset_motion,
    hardware_to_dataset_state,
    validate_data_contract,
)
from backend.core.defaults import default_config
from backend.core.units import dataset_pulses_to_ui_state, motion_pulse_per_unit
from backend.services.dataset_recorder import DatasetRecorderService


def test_data_contract_round_trips_motion_state_action_and_grippers() -> None:
    hardware_state = [1, 2, 3, 100, 200, 300, 7, 4, 5, 6, -400, -500, -600, 8]

    dataset_state = hardware_to_dataset_state(hardware_state)

    assert dataset_state == [4, 5, 6, -400, -500, -600, 8, 1, 2, 3, 100, 200, 300, 7]
    assert dataset_to_hardware_state(dataset_state) == hardware_state
    assert hardware_to_dataset_motion(list(range(12))) == [6, 7, 8, 9, 10, 11, 0, 1, 2, 3, 4, 5]


def test_dataset_pulses_use_matching_side_specific_kinematics() -> None:
    config = default_config()
    config["motion"]["kinematics"]["leftSignedPulsePerUnit"] = [-2, -3, -4, 5, 6, 7]
    config["motion"]["kinematics"]["rightSignedPulsePerUnit"] = [-11, -13, -17, 19, 23, 29]

    assert motion_pulse_per_unit(config, side_order="dataset") == (
        -11,
        -13,
        -17,
        19,
        23,
        29,
        -2,
        -3,
        -4,
        5,
        6,
        7,
    )
    assert dataset_pulses_to_ui_state([0, -13, 0, 0, 0, 0] + [0] * 6, config)[1] == 1000.0


@pytest.mark.parametrize(
    "metadata",
    [None, {"version": "unknown"}, {"version": DATA_CONTRACT_VERSION, "sideOrder": "left_then_right"}],
)
def test_unknown_or_incomplete_data_contract_is_rejected(metadata: object) -> None:
    with pytest.raises(ValueError):
        validate_data_contract(metadata)


def test_native_dataset_without_contract_is_not_resumeable(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(
        json.dumps({"format": "lerobot-v3-native"}), encoding="utf-8"
    )
    recorder = object.__new__(DatasetRecorderService)

    assert "manual migration" in recorder._dataset_contract_error(dataset_dir)


def test_native_dataset_with_unknown_contract_is_not_resumeable(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(
        json.dumps({"format": "lerobot-v3-native", "dataContract": {"version": "future.v9"}}),
        encoding="utf-8",
    )
    recorder = object.__new__(DatasetRecorderService)

    assert "unsupported data contract version" in recorder._dataset_contract_error(dataset_dir)


def test_native_dataset_with_current_contract_can_resume(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(
        json.dumps({"format": "lerobot-v3-native", "dataContract": data_contract_metadata()}),
        encoding="utf-8",
    )
    recorder = object.__new__(DatasetRecorderService)

    assert recorder._dataset_contract_error(dataset_dir) == ""
