from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.core.data_contract import data_contract_metadata
from backend.core.defaults import default_config
from backend.services.dataset_recorder import DatasetRecorderService


def calibration_state(bias: float, completed_at: int) -> dict:
    return {
        "source": "hkvl_serial",
        "timestamp_ms": completed_at + 100,
        "sides": {
            side: {
                "axisSign": [-1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                "tareBias": [-value, value, value, value, value, value],
                "sensorTareBias": [value] * 6,
            }
            for side, value in (("left", bias), ("right", bias + 1.0))
        },
        "calibration": {
            "state": "ready",
            "completedAtUnixMs": completed_at,
            "sides": {
                "left": {"preMean": [bias] * 6, "residualMean": [0.001] * 6},
                "right": {"preMean": [bias + 1.0] * 6, "residualMean": [0.002] * 6},
            },
        },
    }


def test_resumed_dataset_keeps_each_episode_force_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_case() -> None:
        # 所有采集和 LeRobot 编码均为替身，只验证真实会话和 episode 元数据边界。
        monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
        config = default_config()
        config["storage"]["datasetRoot"] = str(tmp_path)
        hal = SimpleNamespace(force_state=AsyncMock(return_value=calibration_state(0.1, 1000)))
        recorder = DatasetRecorderService(
            SimpleNamespace(get_config=lambda: config),
            SimpleNamespace(),
            hal,
            SimpleNamespace(recording=False, episode_count=0, frame_count=0),
            SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None, error=lambda *_args: None),
            SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), status=lambda: {}),
        )
        dataset_dir = tmp_path / "unit"
        (dataset_dir / "meta").mkdir(parents=True)
        (dataset_dir / "meta" / "info.json").write_text(
            json.dumps({"codebase_version": "v3.0", "dataContract": data_contract_metadata()}),
            encoding="utf-8",
        )
        monkeypatch.setattr(recorder, "_try_begin_native_dataset", AsyncMock(return_value=True))
        monkeypatch.setattr(recorder, "_native_writer_active", lambda: True)
        monkeypatch.setattr(recorder, "_start_sampler_tasks_locked", lambda: None)
        monkeypatch.setattr(recorder, "_record_loop", AsyncMock())
        monkeypatch.setattr(recorder, "_frame_assembler_loop", AsyncMock())
        monkeypatch.setattr(recorder, "_refresh_gripper_cache", AsyncMock())
        monkeypatch.setattr(recorder, "_wait_for_episode_warmup", AsyncMock())

        try:
            await recorder.start_session("unit", "task")
            recorder._episode_frames = 1
            first = (await recorder.save_episode())["episode"]
            first_snapshot = deepcopy(first["forceCalibration"])
            await recorder.finish_session()

            # 新会话模拟再次 Tare，仍然续录同一数据集。
            hal.force_state.return_value = calibration_state(0.7, 2000)
            await recorder.start_session("unit", "task")
            recorder._episode_frames = 1
            second = (await recorder.save_episode())["episode"]
            await recorder.finish_session()
        finally:
            if recorder._session_active:
                await recorder.finish_session()

        episodes = recorder._read_episodes(dataset_dir)
        assert [episode["id"] for episode in episodes] == ["episode_000000", "episode_000001"]
        assert episodes[0]["forceCalibration"] == first_snapshot
        assert episodes[1]["forceCalibration"] == second["forceCalibration"]
        assert episodes[0]["forceCalibration"]["state"]["sides"]["left"]["sensorTareBias"] == [0.1] * 6
        assert episodes[1]["forceCalibration"]["state"]["sides"]["left"]["sensorTareBias"] == [0.7] * 6
        assert episodes[0]["forceCalibration"]["state"]["calibration"]["completedAtUnixMs"] == 1000
        assert episodes[1]["forceCalibration"]["state"]["calibration"]["completedAtUnixMs"] == 2000
        assert first_snapshot["sideSemantics"] == "hardware"
        assert first_snapshot["hardwareSideForDatasetSide"] == {"left": "right", "right": "left"}
        app_info = recorder._read_json(dataset_dir / "meta" / "appstation_info.json")
        assert app_info["hardware"]["force"]["calibration"]["completedAtUnixMs"] == 2000
        first_detail = recorder._episode_for_api(dataset_dir, "unit", episodes[0], include_samples=False)
        assert first_detail["forceCalibration"] == first_snapshot
        first_detail["forceCalibration"]["state"]["sides"]["left"]["sensorTareBias"][0] = 99.0
        assert episodes[0]["forceCalibration"] == first_snapshot

    asyncio.run(run_case())


def test_episode_start_freezes_force_calibration_before_later_telemetry() -> None:
    config = default_config()
    recorder = DatasetRecorderService(
        SimpleNamespace(get_config=lambda: config),
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
    )
    recorder._latest_force_state = calibration_state(0.1, 1000)
    recorder._native_writer_active = lambda: True
    recorder._begin_episode_locked()
    snapshot = deepcopy(recorder._episode_force_calibration)

    recorder._latest_force_state["sides"]["left"]["sensorTareBias"][0] = 99.0
    recorder._latest_force_state["calibration"]["completedAtUnixMs"] = 9999
    config["force"]["serial"]["leftPort"] = "COM99"

    assert recorder._episode_force_calibration == snapshot


@pytest.mark.parametrize("source", ["hkvl_serial", "nidaq"])
def test_missing_force_calibration_is_not_fabricated(source: str) -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._latest_force_state = {}
    config = default_config()
    config["force"]["source"] = source

    snapshot = recorder._force_calibration_snapshot(config)

    assert snapshot["source"] == source
    assert snapshot["stateAvailable"] is False
    assert "calibration" not in snapshot["state"]
    assert all("tareBias" not in side for side in snapshot["state"].get("sides", {}).values())


def test_legacy_episode_does_not_inherit_latest_dataset_calibration(tmp_path: Path) -> None:
    recorder = object.__new__(DatasetRecorderService)
    recorder._write_json(tmp_path / "meta" / "appstation_info.json", {
        "hardware": {"force": {"calibration": calibration_state(0.7, 2000)["calibration"]}},
    })
    legacy = {"id": "episode_000000", "frames": 1}
    recorder._write_episodes(tmp_path, [legacy])

    result = recorder._episode_for_api(tmp_path, "unit", legacy, include_samples=False)

    assert result["forceCalibration"] == {}
    assert recorder._read_episodes(tmp_path) == [legacy]
