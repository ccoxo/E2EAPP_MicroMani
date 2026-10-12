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


def _async_gripper_action(target_mm: float) -> dict:
    action = lerobot_state_from_ui([0.0] * 12, [4.5, 5.5])
    action[6] = target_mm
    return {"action": action, "dataContract": data_contract_metadata(), "dryRun": False,
            "controlledSides": ["left"], "gripperAsync": True, "maxGripperMm": 10.0}


def test_policy_async_gripper_returns_before_serial_and_keeps_latest_target(app):
    async def exercise():
        await confirm_mock_browser_lease(app.state.control_watchdog)
        config = app.state.settings.get_config()
        config["gripper"]["rightEnabled"] = True
        app.state.settings.save_config(config)
        app.state.telemetry.motion_positions = [0.0] * 12
        app.state.telemetry.gripper_positions = [4.5, 5.5]
        release = asyncio.Event()
        sent: list[float] = []

        async def slow_gripper(request):
            sent.append(request.targetMm)
            await release.wait()
            return {"ok": True}

        commands = app.state.commands
        commands.enable_motion_side = AsyncMock()
        commands.gripper_command = slow_gripper
        app.state.hal.command = AsyncMock(return_value={"ok": True})
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/policy/action")
        first = await endpoint(_async_gripper_action(6.0))
        await asyncio.sleep(0)
        await endpoint(_async_gripper_action(7.0))
        third = await endpoint(_async_gripper_action(8.0))
        # 串口命令仍在进行时，策略请求已返回；中间的 7.0 被最新的 8.0 覆盖。
        assert first.data["results"]["grippers"]["left"]["queued"] is True
        assert third.data["results"]["grippers"]["left"]["replacedPending"] is True
        assert app.state.hal.command.await_count == 3
        assert sent == [6.0]
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert sent == [6.0, 8.0]

    asyncio.run(exercise())


def test_policy_async_gripper_failure_stops_and_blocks_next_action(app):
    async def exercise():
        await confirm_mock_browser_lease(app.state.control_watchdog)
        config = app.state.settings.get_config()
        config["gripper"]["rightEnabled"] = True
        app.state.settings.save_config(config)
        app.state.telemetry.motion_positions = [0.0] * 12
        app.state.telemetry.gripper_positions = [4.5, 5.5]
        commands = app.state.commands
        commands.enable_motion_side = AsyncMock()
        commands.gripper_command = AsyncMock(side_effect=PermissionError("config.json busy"))
        commands.emergency_stop = AsyncMock()
        app.state.hal.command = AsyncMock(return_value={"ok": True})
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/policy/action")
        await endpoint(_async_gripper_action(6.0))
        for _ in range(5):
            await asyncio.sleep(0)
        commands.emergency_stop.assert_awaited_once()
        with pytest.raises(HTTPException) as failed:
            await endpoint(_async_gripper_action(6.0))
        assert failed.value.status_code == 503
        assert failed.value.detail["code"] == "POLICY_GRIPPER_FAILED"
        assert "config.json busy" in failed.value.detail["message"]
        app.state.hal.command.assert_awaited_once()

    asyncio.run(exercise())


def test_policy_async_gripper_failure_of_previous_session_does_not_block_new_session(app):
    from backend.services.control_watchdog import request_control_session

    async def exercise():
        old_session = await confirm_mock_browser_lease(app.state.control_watchdog)
        config = app.state.settings.get_config()
        config["gripper"]["rightEnabled"] = True
        app.state.settings.save_config(config)
        app.state.telemetry.motion_positions = [0.0] * 12
        app.state.telemetry.gripper_positions = [4.5, 5.5]
        commands = app.state.commands
        commands.enable_motion_side = AsyncMock()
        commands.emergency_stop = AsyncMock()
        app.state.hal.command = AsyncMock(return_value={"ok": True})
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/policy/action")
        # 上一轮会话结束后，残留目标被拒绝（会话已失去控制权）。
        commands.gripper_command = AsyncMock(side_effect=RuntimeError("request does not own the current browser control session"))
        token = request_control_session.set(old_session)
        try:
            await endpoint(_async_gripper_action(6.0))
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            request_control_session.reset(token)
        commands.gripper_command = AsyncMock(return_value={"ok": True})
        result = await endpoint(_async_gripper_action(7.0))
        assert result.data["sent"] is True
        for _ in range(5):
            await asyncio.sleep(0)
        assert commands.gripper_command.call_args.args[0].targetMm == 7.0

    asyncio.run(exercise())
