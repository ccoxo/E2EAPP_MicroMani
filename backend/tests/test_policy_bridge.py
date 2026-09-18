# 阅读导航 07｜测试与验证
# 职责：回归验证：LeRobot 14 维状态、动作限幅、预演和控制侧选择。
# 先看：test_lerobot_state_from_ui_inserts_grippers_and_converts_rotation_to_mdeg → test_build_policy_action_plan_clamps_motion_and_gripper_steps → test_policy_observation_endpoint_returns_lerobot_state → test_policy_action_endpoint_is_dry_run_by_default。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.data_contract import data_contract_metadata
from backend.core.defaults import default_config
from backend.core.motion_limits import effective_limit_arrays
from backend.services.dataset_recorder import DatasetRecorderService
from backend.services.policy_bridge import build_policy_action_plan, lerobot_state_from_ui
from backend.tests.test_control_watchdog import confirm_mock_browser_lease


@pytest.fixture
def authorized_policy_client(tmp_path, monkeypatch):
    """通过真实租约应答及显式确认准备 Test HAL，不绕过控制保护。"""
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.telemetry.hardware = None
    watchdog = client.app.state.control_watchdog
    session = asyncio.run(confirm_mock_browser_lease(watchdog))
    client.headers["X-Control-Session"] = session
    response = client.post("/api/motion/safety/acknowledge")
    assert response.status_code == 200, response.text
    try:
        yield client
    finally:
        asyncio.run(watchdog.close())
        client.app.state.telemetry.shutdown()


def test_lerobot_state_from_ui_inserts_grippers_and_converts_rotation_to_mdeg() -> None:
    state = lerobot_state_from_ui(
        [1, 2, 3, 0.1, -0.2, 0.3, 4, 5, 6, -0.4, 0.5, -0.6],
        [7, 8],
    )

    assert state == [4, 5, 6, -400, 500, -600, 8, 1, 2, 3, 100, -200, 300, 7]


def test_policy_hold_position_keeps_dataset_sides_and_grippers() -> None:
    positions = [1, 2, 3, 0.1, -0.2, 0.3, 4, 5, 6, -0.4, 0.5, -0.6]
    grippers = [7, 8]
    current = lerobot_state_from_ui(positions, grippers)
    recorder = object.__new__(DatasetRecorderService)
    recorded_hold = recorder._compose_observation_state(positions, grippers)

    plan = build_policy_action_plan(
        current,
        recorded_hold,
        default_config(),
        max_translation_um=500.0,
        max_rotation_deg=0.2,
        max_gripper_mm=1.0,
    )

    assert plan["motion"]["left"]["deltas"] == {axis: 0.0 for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw")}
    assert plan["motion"]["right"]["deltas"] == {axis: 0.0 for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw")}
    assert plan["grippers"] == {"leftMm": 8.0, "rightMm": 7.0}


def test_policy_plan_uses_corresponding_hardware_side_limits_and_enabled_axes() -> None:
    config = default_config()
    config["teleop"]["leftEnabledAxes"] = [True, False, True, False, True, False]
    config["teleop"]["rightEnabledAxes"] = [False, True, False, True, False, True]
    plan = build_policy_action_plan(
        [0.0] * 14, [0.0] * 14, config,
        max_translation_um=500.0, max_rotation_deg=0.2, max_gripper_mm=1.0,
    )

    for operator_side, hardware_side in (("left", "right"), ("right", "left")):
        lower, upper = effective_limit_arrays(config, hardware_side)
        assert plan["motion"][operator_side]["softLimitMin"] == lower
        assert plan["motion"][operator_side]["softLimitMax"] == upper
        assert plan["motion"][operator_side]["enabledAxes"] == config["teleop"][f"{hardware_side}EnabledAxes"]


def test_build_policy_action_plan_clamps_motion_and_gripper_steps() -> None:
    config = default_config()
    current = [0.0] * 14
    current[6] = 13.0
    current[13] = 13.0
    action = [
        1000.0,
        -1000.0,
        250.0,
        1000.0,
        -1000.0,
        50.0,
        20.0,
        -900.0,
        700.0,
        -250.0,
        -1000.0,
        1000.0,
        -50.0,
        5.0,
    ]

    plan = build_policy_action_plan(
        current,
        action,
        config,
        max_translation_um=500.0,
        max_rotation_deg=0.2,
        max_gripper_mm=1.0,
    )

    assert plan["motion"]["left"]["deltas"] == {
        "X": 500.0,
        "Y": -500.0,
        "Z": 250.0,
        "Roll": 0.2,
        "Pitch": -0.2,
        "Yaw": 0.05,
    }
    assert plan["motion"]["right"]["deltas"] == {
        "X": -500.0,
        "Y": 500.0,
        "Z": -250.0,
        "Roll": -0.2,
        "Pitch": 0.2,
        "Yaw": -0.05,
    }
    assert plan["grippers"] == {"leftMm": 14.0, "rightMm": 12.0}


def test_policy_observation_endpoint_returns_lerobot_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.telemetry.motion_positions = [1, 2, 3, 0.1, -0.2, 0.3, 4, 5, 6, -0.4, 0.5, -0.6]
    client.app.state.telemetry.gripper_positions = [7, 8]
    client.app.state.telemetry.force_left = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    client.app.state.telemetry.force_right = [-0.1, -0.2, -0.3, -0.4, -0.5, -0.6]

    response = client.get("/api/policy/observation")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["state"] == [4, 5, 6, -400, 500, -600, 8, 1, 2, 3, 100, -200, 300, 7]
    assert payload["pulses"] == [0.0] * 12
    assert payload["force_left"] == [-0.1, -0.2, -0.3, -0.4, -0.5, -0.6]
    assert payload["force_right"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]


def test_policy_action_endpoint_is_dry_run_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.telemetry.motion_positions = [0.0] * 12
    client.app.state.telemetry.gripper_positions = [13.0, 13.0]

    response = client.post(
        "/api/policy/action",
        json={"action": [1000.0] * 14, "dataContract": data_contract_metadata()},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["dryRun"] is True
    assert payload["sent"] is False
    assert payload["plan"]["motion"]["left"]["deltas"]["X"] == 500.0


def test_policy_action_endpoint_can_send_through_test_hal(authorized_policy_client) -> None:
    client = authorized_policy_client
    client.app.state.telemetry.motion_positions = [0.0] * 12
    client.app.state.telemetry.gripper_positions = [13.0, 13.0]
    config = client.get("/api/settings").json()
    config["gripper"]["leftEnabled"] = True
    config["gripper"]["rightEnabled"] = True
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.post(
        "/api/policy/action",
        json={"action": [10.0] * 14, "dryRun": False, "dataContract": data_contract_metadata()},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["dryRun"] is False
    assert payload["sent"] is True
    assert set(payload["results"]["motion"]) == {"left", "right"}
    assert set(payload["results"]["grippers"]) == {"left", "right"}


def test_policy_action_endpoint_skips_disabled_grippers_when_sending_motion(authorized_policy_client) -> None:
    client = authorized_policy_client
    client.app.state.telemetry.motion_positions = [0.0] * 12
    client.app.state.telemetry.gripper_positions = [13.0, 13.0]

    response = client.post(
        "/api/policy/action",
        json={"action": [10.0] * 14, "dryRun": False, "dataContract": data_contract_metadata()},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["dryRun"] is False
    assert payload["sent"] is True
    assert set(payload["results"]["motion"]) == {"left", "right"}
    assert payload["results"]["grippers"] == {}


def test_policy_action_endpoint_can_limit_control_to_left_side(authorized_policy_client) -> None:
    client = authorized_policy_client
    client.app.state.telemetry.motion_positions = [0.0] * 12
    client.app.state.telemetry.gripper_positions = [13.0, 13.0]
    config = client.get("/api/settings").json()
    config["gripper"]["leftEnabled"] = True
    config["gripper"]["rightEnabled"] = True
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.post(
        "/api/policy/action",
        json={
            "action": [10.0] * 14,
            "dryRun": False,
            "controlledSides": ["left"],
            "dataContract": data_contract_metadata(),
        },
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["sent"] is True
    assert set(payload["results"]["motion"]) == {"left"}
    assert set(payload["results"]["grippers"]) == {"left"}
    assert payload["results"]["motion"]["left"]["payload"]["side"] == "right"


def test_policy_action_rejects_missing_data_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/policy/action", json={"action": [0.0] * 14})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "POLICY_DATA_CONTRACT_MISMATCH"
