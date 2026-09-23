import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.logging import now_ms
from backend.hal_client.client import HalHealth


def setup_homing(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    state = {"pulses": list(range(100, 112)), "moving": [False] * 12,
             "enabled": [True] * 12, "enabled_confirmed": [True] * 12,
             "sample_cached": False, "estop_active": False}
    instance = {"id": "test-hal-instance"}
    async def motion_state():
        return {**state, "timestamp_ms": now_ms()}
    async def health():
        return HalHealth(ltdmc_ok=True, omega7_ok=True, version="test", uptime_s=1.0,
                         connected=True, mode="real", instance_id=instance["id"])
    hal = SimpleNamespace(command=AsyncMock(return_value={"response": {"homeCompleted": True}}),
                          motion_state=motion_state, health=health, instance=instance)
    monkeypatch.setattr("backend.app.make_hal_client", lambda *_: hal)
    app = create_app(tmp_path)
    config = app.state.settings.get_config()
    config["motion"]["rotationWorkLimits"]["enabled"] = False
    config["motion"]["homeReference"].update(
        valid=True, leftValid=True, rightValid=True,
        leftAxisConfirmed=[True] * 6, rightAxisConfirmed=[True] * 6,
        leftAxisInstanceId=["test-hal-instance"] * 6, rightAxisInstanceId=["test-hal-instance"] * 6,
        leftPulse=[0.] * 6, rightPulse=[10., 20., 30., 40., 50., 60.])
    config["motion"]["workOriginOffset"].update(
        valid=True, leftValid=True, rightValid=True,
        rightPulseDelta=[1., 2., 3., 4., 5., 6.])
    config["motion"]["origin"].update(
        valid=True, leftValid=True, rightValid=True, rightPulse=[11., 22., 33., 44., 55., 66.])
    app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    from backend.tests.test_control_watchdog import confirm_mock_browser_lease
    hal.command.return_value = {"response": {"ok": True, "leaseFresh": True}}
    session = asyncio.run(confirm_mock_browser_lease(app.state.control_watchdog))
    hal.command.reset_mock()
    hal.command.return_value = {"response": {"homeCompleted": True}}
    # 不启动 lifespan；只验证路由和业务，不连接设备。
    return TestClient(app, headers={"X-Control-Session": session}), hal, state


def test_selected_homing_preserves_other_references(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    client.app.state.telemetry.axis_offsets = list(range(1, 13))
    response = client.post("/api/motion/right/home", json={"axes": ["Roll", "Pitch", "Yaw"]})
    assert response.status_code == 200, response.text
    hal.command.assert_awaited_once()
    command_name, payload = hal.command.await_args.args
    assert command_name == "motion.home_side"
    assert payload["side"] == "right"
    assert payload["enabledAxes"] == [False, False, False, True, True, True]
    assert payload["referenceMode"] == "origin"
    assert payload["homeMaxSearchUi"] == [55000.0, 82500.0, 82500.0, 90.0, 90.0, 90.0]
    after = client.app.state.settings.get_config()["motion"]
    assert after["homeReference"]["rightPulse"] == [10., 20., 30., 109., 110., 111.]
    assert after["origin"]["rightPulse"] == [11., 22., 33., 113., 115., 117.]
    assert after["homeReference"]["leftPulse"] == before["homeReference"]["leftPulse"]
    assert after["workOriginOffset"] == before["workOriginOffset"]
    assert client.app.state.telemetry.axis_offsets == list(range(1, 10)) + [0., 0., 0.]


def test_limit_reference_preserves_work_origin_and_records_source(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    client.app.state.telemetry.axis_offsets = list(range(1, 13))
    hal.command.return_value = {"response": {
        "homeCompleted": True, "limitReferenceAxes": [True, False, True, False, False, False]}}
    response = client.post("/api/motion/right/positive_limit_reference", json={"axes": ["X", "Z"]})
    assert response.status_code == 200, response.text
    after = client.app.state.settings.get_config()["motion"]
    assert after["homeReference"]["rightPulse"] == [106., 20., 108., 40., 50., 60.]
    assert after["homeReference"]["rightAxisLimitReference"] == [True, False, True, False, False, False]
    assert after["origin"] == before["origin"]
    assert after["workOriginOffset"]["rightPulseDelta"] == [-95., 2., -75., 4., 5., 6.]
    assert after["origin"]["leftPulse"] == before["origin"]["leftPulse"]
    assert response.json()["data"]["homeReference"]["rightAxisLimitReference"][0] is True
    assert client.app.state.telemetry.axis_offsets == list(range(1, 13))


def test_limit_only_home_preserves_complete_work_origin(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"]["origin"])
    hal.command.return_value = {"response": {
        "homeCompleted": True, "limitReferenceAxes": [True, False, True, False, False, False]}}
    assert client.post("/api/motion/right/positive_limit_reference", json={"axes": ["X", "Z"]}).status_code == 200
    assert client.app.state.settings.get_config()["motion"]["origin"] == before


@pytest.mark.parametrize("outcome", ["signal", "failure", "stale", "estop"])
def test_limit_reference_source_is_cleared_by_new_seek(tmp_path, monkeypatch, outcome):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["homeReference"]["rightAxisLimitReference"] = [True, False, True, False, False, False]
    settings.save_config(config, emit_log=False, home_reference_update=True)
    if outcome == "failure":
        hal.command.side_effect = RuntimeError("home failed")
    elif outcome in ("stale", "estop"):
        hal.command.return_value = {"response": {
            "homeCompleted": True, "limitReferenceAxes": [False] * 6}}
        if outcome == "stale":
            async def stale():
                return {**state, "timestamp_ms": now_ms() - 5000}
            hal.motion_state = stale
        else:
            state["estop_active"] = True
    response = client.post("/api/motion/right/home", json={"axes": ["X"]})
    assert response.status_code == (200 if outcome == "signal" else 503)
    ref = settings.get_config()["motion"]["homeReference"]
    assert ref["rightAxisLimitReference"] == [False, False, True, False, False, False]
    assert ref["rightAxisConfirmed"][0] is (outcome == "signal")


def test_settings_cannot_forge_or_erase_limit_reference_source(tmp_path, monkeypatch):
    client, _, _ = setup_homing(tmp_path, monkeypatch)
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["homeReference"]["rightAxisLimitReference"] = [True, False, False, False, False, False]
    saved = settings.save_config(config, emit_log=False)
    assert saved["motion"]["homeReference"]["rightAxisLimitReference"] == [False] * 6
    saved["motion"]["homeReference"]["rightAxisLimitReference"][0] = True
    settings.save_config(saved, emit_log=False, home_reference_update=True)
    stale = deepcopy(saved)
    stale["motion"]["homeReference"].pop("rightAxisLimitReference")
    restored = settings.save_config(stale, emit_log=False)
    assert restored["motion"]["homeReference"]["rightAxisLimitReference"][0] is True
    restored["motion"]["homeReference"]["rightPulse"][0] += 1
    changed = settings.save_config(restored, emit_log=False)
    assert changed["motion"]["homeReference"]["rightAxisConfirmed"][0] is False
    assert changed["motion"]["homeReference"]["rightAxisLimitReference"][0] is False


@pytest.mark.parametrize("axes,mask", [(["X"], [True, False, False, False, False, False]),
                                      (["Y"], [False, True, False, False, False, False]),
                                      (["X", "Y"], [True, True, False, False, False, False])])
def test_operator_right_xy_limit_reference_preserves_origin(tmp_path, monkeypatch, axes, mask):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    hal.command.return_value = {"response": {"homeCompleted": True, "limitReferenceAxes": mask}}
    response = client.post("/api/motion/left/positive_limit_reference", json={"axes": axes})
    assert response.status_code == 200, response.text
    after = client.app.state.settings.get_config()["motion"]
    assert after["origin"] == before["origin"]
    assert after["homeReference"]["leftAxisLimitReference"] == mask
    assert after["homeReference"]["rightPulse"] == before["homeReference"]["rightPulse"]
    for index, selected in enumerate(mask):
        assert after["homeReference"]["leftPulse"][index] == (100 + index if selected else 0)
        if selected:
            assert after["workOriginOffset"]["leftPulseDelta"][index] == before["origin"]["leftPulse"][index] - 100 - index
    saved = client.app.state.settings.save_config(client.app.state.settings.get_config(), emit_log=False)
    assert saved["motion"]["homeReference"]["leftAxisLimitReference"] == mask


def test_limit_reference_sources_survive_sequential_axes_and_other_side_home(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    origin_before = deepcopy(client.app.state.settings.get_config()["motion"]["origin"])
    for side, axis, mask, expected_left in [
        ("left", "X", [True, False, False, False, False, False], [True, False, False, False, False, False]),
        ("left", "Y", [False, True, False, False, False, False], [True, True, False, False, False, False]),
        ("right", "X", [True, False, False, False, False, False], [True, True, False, False, False, False]),
    ]:
        hal.command.return_value = {"response": {"homeCompleted": True, "limitReferenceAxes": mask}}
        response = client.post(f"/api/motion/{side}/positive_limit_reference", json={"axes": [axis]})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["homeReference"]["leftAxisLimitReference"] == expected_left
        reference = client.get("/api/motion/origin").json()["data"]["homeReference"]
        assert reference["leftAxisLimitReference"] == expected_left
        motion = client.app.state.settings.get_config()["motion"]
        assert motion["homeReference"]["leftAxisLimitReference"] == expected_left
        assert motion["origin"] == origin_before


def test_operator_right_z_cannot_claim_limit_reference(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    hal.command.return_value = {"response": {
        "homeCompleted": True, "limitReferenceAxes": [False, False, True, False, False, False]}}
    response = client.post("/api/motion/left/positive_limit_reference", json={"axes": ["Z"]})
    assert response.status_code == 503
    assert client.app.state.settings.get_config()["motion"]["homeReference"]["leftAxisConfirmed"][2] is False


@pytest.mark.parametrize("mask", [
    [True], [1, False, False, False, False, False],
    [False, True, False, False, False, False],
    [False, False, True, False, False, False],
])
def test_invalid_limit_reference_reply_does_not_confirm(tmp_path, monkeypatch, mask):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    hal.command.return_value = {"response": {"homeCompleted": True, "limitReferenceAxes": mask}}
    response = client.post("/api/motion/right/positive_limit_reference", json={"axes": ["X"]})
    assert response.status_code == 503
    after = client.app.state.settings.get_config()["motion"]
    assert after["homeReference"]["rightAxisConfirmed"][0] is False
    assert after["homeReference"]["rightPulse"] == before["homeReference"]["rightPulse"]
    assert after["origin"] == before["origin"]


@pytest.mark.parametrize("axes", [[], ["Pitch", "Pitch"], ["pitch"], ["Bogus"]])
def test_invalid_homing_selection_never_dispatches(tmp_path, monkeypatch, axes):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    assert client.post("/api/motion/right/home", json={"axes": axes}).status_code == 422
    hal.command.assert_not_awaited()


def test_legacy_button_without_explicit_axes_cannot_start_mechanical_home(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    assert client.post("/api/motion/right/home").status_code == 422
    hal.command.assert_not_awaited()


def test_partial_homing_can_confirm_one_axis_without_confirming_legacy_axes(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    config["motion"]["homeReference"].update(rightValid=False, valid=False, rightAxisConfirmed=[False] * 6)
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    response = client.post("/api/motion/right/home", json={"axes": ["Pitch"]})
    assert response.status_code == 200, response.text
    reference = client.app.state.settings.get_config()["motion"]["homeReference"]
    assert reference["rightAxisConfirmed"] == [False, False, False, False, True, False]
    assert reference["rightPulse"] == [10., 20., 30., 40., 110., 60.]


@pytest.mark.parametrize("failure", ["rejected", "unconfirmed", "estop"])
def test_failed_homing_does_not_save_reference(tmp_path, monkeypatch, failure):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    if failure == "rejected":
        hal.command.side_effect = RuntimeError("homing did not complete")
    elif failure == "unconfirmed":
        hal.command.return_value = {"response": {"ok": True}}
    else:
        state["estop_active"] = True
    response = client.post("/api/motion/right/home", json={"axes": ["Pitch"]})
    assert response.status_code == 503
    after = client.app.state.settings.get_config()["motion"]
    assert after["homeReference"]["rightPulse"] == before["homeReference"]["rightPulse"]
    assert after["origin"] == before["origin"]
    assert after["homeReference"]["rightAxisConfirmed"] == [True, True, True, True, False, True]


def test_reference_waits_for_settled_post_completion_feedback(tmp_path, monkeypatch):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    reads = []
    async def feedback():
        reads.append(1)
        moving = [False] * 12
        moving[10] = len(reads) < 3
        return {**state, "moving": moving, "timestamp_ms": now_ms(),
                "pulses": [0.] * 12 if moving[10] else state["pulses"]}
    hal.motion_state = feedback
    response = client.post("/api/motion/right/home", json={"axes": ["Pitch"]})
    assert response.status_code == 200, response.text
    assert len(reads) >= 3
    assert client.app.state.settings.get_config()["motion"]["homeReference"]["rightPulse"][4] == 110


def test_return_hardware_reference_never_seeks_or_changes_origin(tmp_path, monkeypatch):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    target = before["homeReference"]["rightPulse"]
    async def command(name, payload):
        assert name == "motion.return_home_reference"
        state["pulses"][6:] = payload["pulse"]
        return {"response": {"referenceReturnCompleted": True}}
    hal.command.side_effect = command
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]})
    assert response.status_code == 200, response.text
    hal.command.assert_awaited_once_with("motion.return_home_reference", {
        "side": "right", "pulse": target, "enabledAxes": [True] * 6})
    assert client.app.state.settings.get_config()["motion"] == before


@pytest.mark.parametrize("failure", ["missing", "large_rotation", "stale", "moving", "disabled", "estop", "limit"])
def test_unsafe_reference_return_never_dispatches(tmp_path, monkeypatch, failure):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    if failure == "missing":
        config["motion"]["homeReference"].update(rightValid=False, valid=False, rightAxisConfirmed=[False] * 6)
    elif failure == "large_rotation":
        state["pulses"][9] = 40. + 558414.
    elif failure == "stale":
        hal.motion_state = AsyncMock(return_value={**state, "timestamp_ms": now_ms() - 5000})
    elif failure == "moving":
        state["moving"][9] = True
    elif failure == "disabled":
        state["enabled"][9] = False
    elif failure == "estop":
        state["estop_active"] = True
    else:
        config["motion"]["rightSoftLimits"]["roll"] = {"min": 1000, "max": 2000}
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    before = deepcopy(client.app.state.settings.get_config()["motion"])
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]})
    assert response.status_code == 503, response.text
    hal.command.assert_not_awaited()
    assert client.app.state.settings.get_config()["motion"] == before


def test_reference_return_requires_matching_hal_completion(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]})
    assert response.status_code == 503
    assert "completion" in response.text


def test_reference_return_stops_manual_teleop_before_reading_position(tmp_path, monkeypatch):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    commands = client.app.state.commands
    events = []
    async def stop(*args, **kwargs):
        events.append("stop")
    commands.teleop = SimpleNamespace(status=lambda: {"sources": ["teleop-connect"]}, stop=stop)
    async def feedback():
        events.append("read")
        return {**state, "timestamp_ms": now_ms()}
    async def command(name, payload):
        events.append("return")
        state["pulses"][6:] = payload["pulse"]
        return {"response": {"referenceReturnCompleted": True}}
    hal.motion_state = feedback
    hal.command.side_effect = command
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]})
    assert response.status_code == 200, response.text
    assert events[:3] == ["stop", "read", "return"]


def test_reference_return_requires_control_lease(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    client.headers.pop("X-Control-Session")
    assert client.post("/api/motion/right/return_home_reference", json={"axes": ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]}).status_code == 409
    hal.command.assert_not_awaited()


def test_legacy_reference_retains_pulses_but_is_not_confirmed(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    reference = config["motion"]["homeReference"]
    reference.pop("rightAxisConfirmed")
    before = reference["rightPulse"][:]
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    reference = client.app.state.settings.get_config()["motion"]["homeReference"]
    assert reference["rightPulse"] == before
    assert reference["rightAxisConfirmed"] == [False] * 6
    assert client.post("/api/motion/right/return_home_reference", json={"axes": ["Pitch"]}).status_code == 503
    hal.command.assert_not_awaited()


def test_selected_return_checks_and_moves_only_selected_axes(tmp_path, monkeypatch):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    config["motion"]["homeReference"]["rightAxisConfirmed"] = [False, False, False, False, True, False]
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    state["enabled"] = [False] * 12
    state["enabled"][10] = True
    state["pulses"][9] = 99999999  # 未选 Roll 的跨圈差不能阻止单独返回 Pitch。
    before = state["pulses"][:]
    async def command(name, payload):
        assert name == "motion.return_home_reference"
        assert payload["enabledAxes"] == [False, False, False, False, True, False]
        state["pulses"][10] = payload["pulse"][4]
        return {"response": {"referenceReturnCompleted": True}}
    hal.command.side_effect = command
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["Pitch"]})
    assert response.status_code == 200, response.text
    assert state["pulses"][:10] == before[:10]
    assert state["pulses"][11] == before[11]


def test_any_unconfirmed_selected_axis_rejects_entire_return(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    config["motion"]["homeReference"]["rightAxisConfirmed"][0] = False
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["Pitch", "X"]})
    assert response.status_code == 503
    hal.command.assert_not_awaited()


@pytest.mark.parametrize("body", [None, {"axes": []}, {"axes": ["X", "X"]}, {"axes": ["bad"]}])
def test_return_requires_explicit_valid_selection(tmp_path, monkeypatch, body):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    assert client.post("/api/motion/right/return_home_reference", json=body).status_code == 422
    hal.command.assert_not_awaited()


def test_all_axes_homing_confirms_all_six_after_completion(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    config["motion"]["homeReference"]["rightAxisConfirmed"] = [False] * 6
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    response = client.post("/api/motion/right/home", json={"axes": ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]})
    assert response.status_code == 200, response.text
    reference = client.app.state.settings.get_config()["motion"]["homeReference"]
    assert reference["rightAxisConfirmed"] == [True] * 6
    assert reference["rightPulse"] == list(range(106, 112))


def test_stale_settings_save_cannot_restore_failed_homing_confirmation(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    stale = client.app.state.settings.get_config()
    hal.command.side_effect = RuntimeError("home interrupted")
    assert client.post("/api/motion/right/home", json={"axes": ["Pitch"]}).status_code == 503
    client.app.state.settings.save_config(stale, emit_log=False)
    reference = client.app.state.settings.get_config()["motion"]["homeReference"]
    assert reference["rightAxisConfirmed"] == [True, True, True, True, False, True]


def test_editing_reference_pulse_revokes_only_changed_axis_confirmation(tmp_path, monkeypatch):
    client, _, _ = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    config["motion"]["homeReference"]["rightPulse"][0] += 1
    client.app.state.settings.save_config(config, emit_log=False)
    assert client.app.state.settings.get_config()["motion"]["homeReference"]["rightAxisConfirmed"] == [False, True, True, True, True, True]


def test_stale_feedback_after_homing_cannot_confirm_reference(tmp_path, monkeypatch):
    client, hal, state = setup_homing(tmp_path, monkeypatch)
    hal.motion_state = AsyncMock(return_value={**state, "timestamp_ms": now_ms() - 5000})
    before = client.app.state.settings.get_config()["motion"]["homeReference"]["rightPulse"]
    response = client.post("/api/motion/right/home", json={"axes": ["Pitch"]})
    assert response.status_code == 503
    reference = client.app.state.settings.get_config()["motion"]["homeReference"]
    assert reference["rightPulse"] == before
    assert reference["rightAxisConfirmed"] == [True, True, True, True, False, True]


def test_positive_limit_reference_sends_explicit_mode_and_requires_all_selected_axes(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    mask = [True, False, True, False, False, False]
    hal.command.return_value = {"response": {"homeCompleted": True, "limitReferenceAxes": mask}}
    response = client.post("/api/motion/right/positive_limit_reference", json={"axes": ["X", "Z"]})
    assert response.status_code == 200, response.text
    name, payload = hal.command.await_args.args
    assert name == "motion.home_side"
    assert payload["referenceMode"] == "positive_limit"
    assert payload["enabledAxes"] == mask


def test_hardware_reference_return_rejects_reference_from_old_hal_instance(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    config = client.app.state.settings.get_config()
    config["motion"]["homeReference"]["rightAxisInstanceId"][4] = "old-hal-instance"
    client.app.state.settings.save_config(config, emit_log=False, home_reference_update=True)
    response = client.post("/api/motion/right/return_home_reference", json={"axes": ["Pitch"]})
    assert response.status_code == 503
    assert "another HAL/controller instance" in response.json()["detail"]["message"]
    hal.command.assert_not_awaited()


def test_hardware_home_rejects_instance_change_before_reference_commit(tmp_path, monkeypatch):
    client, hal, _ = setup_homing(tmp_path, monkeypatch)
    async def change_instance(name, payload):
        assert name == "motion.home_side"
        hal.instance["id"] = "replacement-hal-instance"
        return {"response": {"homeCompleted": True, "limitReferenceAxes": [False] * 6}}
    hal.command.side_effect = change_instance
    response = client.post("/api/motion/right/home", json={"axes": ["Pitch"]})
    assert response.status_code == 503
    reference = client.app.state.settings.get_config()["motion"]["homeReference"]
    assert reference["rightAxisConfirmed"][4] is False
    assert reference["rightAxisInstanceId"][4] == ""
