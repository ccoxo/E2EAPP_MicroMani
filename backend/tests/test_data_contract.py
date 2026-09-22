from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
from backend.core.motion_safety import MotionSafetyGate
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


@pytest.mark.parametrize("metadata", [None, {"version": "future.v9"}, {"version": DATA_CONTRACT_VERSION}])
def test_session_rejects_unknown_native_contract_before_starting_writer(tmp_path, monkeypatch, metadata) -> None:
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    info_path = dataset_dir / "meta" / "info.json"
    info_path.write_text(json.dumps({"format": "lerobot-v3-native", "dataContract": metadata}), encoding="utf-8")
    original = info_path.read_bytes()
    config = default_config()
    config["storage"]["datasetRoot"] = str(tmp_path)
    recorder = object.__new__(DatasetRecorderService)
    recorder.safety = MotionSafetyGate()
    recorder._lock = asyncio.Lock()
    recorder._session_active = False
    recorder._session_starting = False
    recorder.settings = SimpleNamespace(get_config=lambda: config)
    recorder.validate_start_origin = AsyncMock()
    writer = Mock(side_effect=AssertionError("不应启动写线程"))
    monkeypatch.setattr("backend.services.dataset_recorder.LeRobotWriterThread", writer)

    with pytest.raises(RuntimeError, match="dataset numeric channel order is not compatible"):
        asyncio.run(recorder.start_session("dataset", "unit task"))

    recorder.validate_start_origin.assert_awaited_once()
    writer.assert_not_called()
    assert recorder._session_starting is False
    assert info_path.read_bytes() == original


def test_native_writer_resumes_current_contract_without_recreating_dataset(tmp_path) -> None:
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(
        json.dumps({"format": "lerobot-v3-native", "dataContract": data_contract_metadata(), "fps": 25}),
        encoding="utf-8",
    )
    (dataset_dir / "meta" / "episodes.jsonl").write_text('{"id":"episode_000000"}\n', encoding="utf-8")
    resumed = object()
    native = SimpleNamespace(resume=Mock(return_value=resumed), create=Mock(side_effect=AssertionError("不应重建已有数据集")))
    recorder = object.__new__(DatasetRecorderService)
    recorder._dataset_dir = dataset_dir
    recorder._dataset_id = "dataset"
    recorder._record_fps_hz = 30
    recorder._recording_config = default_config
    recorder._native_recording_requested = lambda: True
    recorder._native_preflight = lambda: ""
    recorder._native_imports = lambda: (native, None)
    recorder._native_use_videos_requested = lambda: False
    recorder._native_writer_kwargs = lambda: {}
    recorder._configure_native_chunk_settings = lambda _dataset: None

    assert recorder._open_native_dataset_for_writer() is resumed
    native.resume.assert_called_once_with(repo_id="local/dataset", root=dataset_dir)
    native.create.assert_not_called()
    assert recorder._record_fps_hz == 25
