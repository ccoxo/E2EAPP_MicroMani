from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.defaults import default_config
from backend.services.policy_bridge import build_policy_action_plan, lerobot_state_from_ui


def test_lerobot_state_from_ui_inserts_grippers_and_converts_rotation_to_mdeg() -> None:
    state = lerobot_state_from_ui(
        [1, 2, 3, 0.1, -0.2, 0.3, 4, 5, 6, -0.4, 0.5, -0.6],
        [7, 8],
    )

    assert state == [1, 2, 3, 100, -200, 300, 7, 4, 5, 6, -400, 500, -600, 8]


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

    response = client.get("/api/policy/observation")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == [1, 2, 3, 100, -200, 300, 7, 4, 5, 6, -400, 500, -600, 8]


def test_policy_action_endpoint_is_dry_run_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.telemetry.motion_positions = [0.0] * 12
    client.app.state.telemetry.gripper_positions = [13.0, 13.0]

    response = client.post("/api/policy/action", json={"action": [1000.0] * 14})

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["dryRun"] is True
    assert payload["sent"] is False
    assert payload["plan"]["motion"]["left"]["deltas"]["X"] == 500.0


def test_policy_action_endpoint_can_send_through_test_hal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.telemetry.motion_positions = [0.0] * 12
    client.app.state.telemetry.gripper_positions = [13.0, 13.0]
    config = client.get("/api/settings").json()
    config["gripper"]["leftEnabled"] = True
    config["gripper"]["rightEnabled"] = True
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.post("/api/policy/action", json={"action": [10.0] * 14, "dryRun": False})

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["dryRun"] is False
    assert payload["sent"] is True
    assert set(payload["results"]["motion"]) == {"left", "right"}
    assert set(payload["results"]["grippers"]) == {"left", "right"}
