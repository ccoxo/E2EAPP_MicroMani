from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import create_app, hal_capability_status, relative_motion_positions
from backend.core.data_contract import data_contract_metadata, hardware_to_dataset_motion
from backend.services.policy_bridge import lerobot_state_from_ui
from backend.tests.test_control_watchdog import confirm_mock_browser_lease


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_DISABLE_CAMERA_PROBE", "1")
    return create_app(tmp_path)


def test_policy_observation_and_hold_share_operator_contract(app):
    telemetry = app.state.telemetry
    telemetry.motion_positions = [1, 2, 3, 0.1, 0.2, 0.3, 7, 8, 9, 0.4, 0.5, 0.6]
    telemetry.gripper_positions = [4.5, 5.5]
    telemetry.force_left = [1.0] * 6
    telemetry.force_right = [2.0] * 6
    client = TestClient(app)

    observed = client.get("/api/policy/observation").json()["data"]
    assert observed["state"] == [7, 8, 9, 400, 500, 600, 5.5, 1, 2, 3, 100, 200, 300, 4.5]
    assert observed["force_left"] == [2.0] * 6
    assert observed["force_right"] == [1.0] * 6
    response = client.post("/api/policy/action", json={
        "action": observed["state"], "dataContract": observed["dataContract"], "dryRun": True,
    })
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["dataContract"] == data_contract_metadata()
    assert result["sent"] is False
    for side in ("left", "right"):
        assert all(value == 0 for value in result["plan"]["motion"][side]["deltas"].values())
    assert result["plan"]["grippers"] == {"leftMm": 5.5, "rightMm": 4.5}


def test_policy_observation_converts_pulses_after_hardware_origin_math(app, monkeypatch):
    # 使用假 HAL 的原始脉冲验证换算顺序，不连接设备。
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    positions = [0.0] * 12
    pulses = [100.0 * (index + 1) for index in range(12)]
    app.state.hal.motion_state = AsyncMock(return_value={"positions": positions, "pulses": pulses})
    app.state.telemetry.gripper_positions = [4.5, 5.5]
    config = app.state.settings.get_config()
    expected_positions = relative_motion_positions(config, positions, pulses)
    result = TestClient(app).get("/api/policy/observation").json()["data"]
    assert result["pulses"] == hardware_to_dataset_motion(pulses)
    assert result["state"] == lerobot_state_from_ui(expected_positions, [4.5, 5.5])
    assert app.state.telemetry.motion_positions == expected_positions


@pytest.mark.parametrize("contract", [None, {}, {"version": "unknown"}])
def test_policy_rejects_unknown_contract_before_dispatch(app, contract):
    app.state.commands.enable_motion_side = AsyncMock()
    app.state.hal.command = AsyncMock()
    response = TestClient(app).post("/api/policy/action", json={
        "action": [0.0] * 14, "dataContract": contract, "dryRun": False,
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "POLICY_DATA_CONTRACT_MISMATCH"
    app.state.commands.enable_motion_side.assert_not_called()
    app.state.hal.command.assert_not_called()


def test_policy_operator_left_dispatches_only_hardware_right_under_existing_lease(app):
    async def exercise():
        await confirm_mock_browser_lease(app.state.control_watchdog)
        config = app.state.settings.get_config()
        config["gripper"]["leftEnabled"] = True
        config["gripper"]["rightEnabled"] = True
        app.state.settings.save_config(config)
        app.state.telemetry.motion_positions = [0.0] * 12
        app.state.telemetry.gripper_positions = [4.5, 5.5]
        commands = app.state.commands
        commands.enable_motion_side = AsyncMock()
        commands.gripper_command = AsyncMock(return_value={"ok": True})
        app.state.hal.command = AsyncMock(return_value={"ok": True})
        action = lerobot_state_from_ui([0.0] * 12, [4.5, 5.5])
        action[0] = 3.0
        action[6] = 6.0
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/policy/action")
        result = await endpoint({
            "action": action, "dataContract": data_contract_metadata(),
            "dryRun": False, "controlledSides": ["left"],
        })
        commands.enable_motion_side.assert_awaited_once_with("right")
        app.state.hal.command.assert_awaited_once()
        name, payload = app.state.hal.command.call_args.args
        assert name == "motion.teleop_target_update"
        assert payload["side"] == "right"
        assert payload["deltas"]["X"] == 3.0
        request = commands.gripper_command.call_args.args[0]
        assert request.side == "right"
        assert request.targetMm == 6.0
        assert set(result.data["results"]["motion"]) == {"left"}
        assert set(result.data["results"]["grippers"]) == {"left"}

    asyncio.run(exercise())


def test_policy_contract_does_not_bypass_local_control_lease(app):
    async def exercise():
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/policy/action")
        app.state.hal.command = AsyncMock()
        with pytest.raises(HTTPException) as blocked:
            await endpoint({"action": [0.0] * 14, "dataContract": data_contract_metadata(), "dryRun": False})
        assert blocked.value.detail["code"] == "CONTROL_LEASE_UNAVAILABLE"
        app.state.hal.command.assert_not_called()

    asyncio.run(exercise())


def test_hal_version_does_not_imply_unimplemented_calibration_capability():
    unsupported = hal_capability_status(SimpleNamespace(version="hal-real/0.2", capabilities=[]))
    assert unsupported["compatible"] is False
    assert unsupported["missing"] == ["force_calibration_state_v1"]
    assert hal_capability_status(SimpleNamespace(version="hal-real/0.1"))["compatible"] is False
    supported = hal_capability_status(SimpleNamespace(capabilities=["force_calibration_state_v1"]))
    assert supported["compatible"] is True
    assert supported["missing"] == []
