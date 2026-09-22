from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.logging import now_ms
from backend.core.schemas import AppConfig
from backend.services.control_watchdog import ControlLeaseUnavailable


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.delenv("APPSTATION_HKVL_LEFT_PORT", raising=False)
    monkeypatch.delenv("APPSTATION_HKVL_RIGHT_PORT", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    current = client.get("/api/settings").json()
    current["force"]["source"] = "hkvl_serial"
    app.state.settings.save_config(current, emit_log=False)
    app.state.commands.safety.interrupt(emergency=True)
    monkeypatch.setattr(app.state.control_watchdog, "require_ack_ready", lambda: None)
    calls = []

    async def command(name, payload=None):
        calls.append((name, payload))
        return {"response": {"ok": True}}

    async def motion_state():
        return {"timestamp_ms": now_ms(), "moving": [False] * 12, "enabled": [False] * 12}

    monkeypatch.setattr(app.state.hal, "command", command)
    monkeypatch.setattr(app.state.hal, "motion_state", motion_state)
    return app, client, current, calls


def request_change(client, candidate, route):
    if route == "put":
        return client.put("/api/settings", json=candidate)
    if route == "apply":
        return client.post("/api/settings/apply", json=candidate)
    if route == "reapply":
        return client.post("/api/settings/apply")
    snapshot = client.post("/api/settings/snapshots", json={
        "scope": "all", "name": "force recovery", "config": candidate,
    }).json()["data"]["snapshot"]
    return client.post(f"/api/settings/snapshots/{snapshot['id']}/apply")


@pytest.mark.parametrize("route", ["put", "apply", "snapshot"])
@pytest.mark.parametrize("change", ["port", "nidaq", "same"])
def test_force_only_recovery_keeps_latch_and_never_restores_motion(recovery, route, change):
    app, client, current, calls = recovery
    candidate = deepcopy(current)
    if change == "port":
        candidate["force"]["serial"]["leftPort"] = "COM99"
    elif change == "nidaq":
        candidate["force"]["source"] = "nidaq"

    response = request_change(client, candidate, route)

    assert response.status_code == 200, response.text
    assert [name for name, _ in calls] == ["force.configure"]
    assert calls[0][1]["source"] == candidate["force"]["source"]
    assert client.get("/api/settings").json()["force"] == candidate["force"]
    assert app.state.commands.safety.latched


def test_bodyless_reapply_repairs_force_runtime_while_latched(recovery):
    app, client, current, calls = recovery
    assert request_change(client, current, "reapply").status_code == 200
    assert [name for name, _ in calls] == ["force.configure"]
    assert app.state.commands.safety.latched


@pytest.mark.parametrize("route", ["put", "apply", "reapply", "snapshot"])
def test_recovery_requires_current_control_session_and_confirmed_stop(recovery, monkeypatch, route):
    app, client, current, calls = recovery

    def no_lease():
        raise ControlLeaseUnavailable("fresh confirmed control session required")

    monkeypatch.setattr(app.state.control_watchdog, "require_ack_ready", no_lease)
    response = request_change(client, current, route)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CONTROL_LEASE_UNAVAILABLE"
    assert calls == []


@pytest.mark.parametrize("route", ["put", "apply", "snapshot"])
@pytest.mark.parametrize("section,key,value", [
    ("motion", "leftCardNo", 9),
    ("hal", "apiConfirmed", True),
    ("safety", "fxyWarnN", 1.7),
    ("cameras", "global", "different camera"),
])
def test_mixed_configuration_cannot_use_force_recovery(recovery, monkeypatch, route, section, key, value):
    app, client, current, calls = recovery
    monkeypatch.setattr(app.state.commands.safety, "readiness_check", lambda: None)
    candidate = deepcopy(current)
    candidate["force"]["serial"]["leftPort"] = "COM99"
    candidate[section][key] = value

    response = request_change(client, candidate, route)

    assert response.status_code == 409, response.text
    assert "emergency stop active" in response.text
    assert calls == []
    assert client.get("/api/settings").json()["force"] == current["force"]


@pytest.mark.parametrize("condition", [
    "recording", "session_starting", "session_active", "automatic", "queued_action",
    "teleop_armed", "teleop_running", "teleop_transitioning", "teleop_sources",
])
def test_recovery_rejects_active_recording_auto_or_teleop(recovery, monkeypatch, condition):
    app, client, current, calls = recovery
    if condition in {"recording", "session_starting", "session_active"}:
        monkeypatch.setattr(app.state.recorder, f"_{condition}", True)
    elif condition == "automatic":
        monkeypatch.setattr(app.state.policy, "_auto_running", True)
    elif condition == "queued_action":
        monkeypatch.setattr(app.state.policy, "_action_queue", [{"id": "pending"}])
    else:
        status = {"armed": False, "running": False, "transitioning": False, "sources": []}
        status[condition.removeprefix("teleop_")] = ["manual-gripper"] if condition == "teleop_sources" else True
        monkeypatch.setattr(app.state.teleop_mapper, "status", lambda _config: status)

    response = request_change(client, current, "apply")

    assert response.status_code == 409, response.text
    assert calls == []
    assert app.state.commands.safety.latched


@pytest.mark.parametrize("condition", [
    "moving", "stale", "missing_timestamp", "missing_enabled", "short_enabled", "unknown_enabled", "enabled",
])
def test_recovery_requires_fresh_stopped_and_disabled_feedback(recovery, monkeypatch, condition):
    app, client, current, calls = recovery

    async def state():
        result = {"timestamp_ms": now_ms(), "moving": [False] * 12, "enabled": [False] * 12}
        if condition == "moving":
            result["moving"][3] = True
        elif condition == "stale":
            result["timestamp_ms"] -= 1000
        elif condition == "missing_timestamp":
            result.pop("timestamp_ms")
        elif condition == "missing_enabled":
            result.pop("enabled")
        elif condition == "short_enabled":
            result["enabled"] = [False] * 6
        else:
            result["enabled"][3] = None if condition == "unknown_enabled" else True
        return result

    monkeypatch.setattr(app.state.hal, "motion_state", state)
    response = request_change(client, current, "apply")
    assert response.status_code == 409, response.text
    assert calls == []


@pytest.mark.parametrize("route", ["put", "apply", "snapshot"])
def test_new_emergency_stop_during_hal_configuration_prevents_save(recovery, monkeypatch, route):
    app, client, current, calls = recovery
    candidate = deepcopy(current)
    candidate["force"]["serial"]["leftPort"] = "COM99"

    async def configure_then_stop(name, payload):
        calls.append((name, payload))
        app.state.commands.safety.interrupt(emergency=True)
        return {"response": {"ok": True}}

    monkeypatch.setattr(app.state.hal, "command", configure_then_stop)
    response = request_change(client, candidate, route)
    assert response.status_code == 409
    assert "newer safety operation" in response.text
    assert client.get("/api/settings").json()["force"] == current["force"]
    assert app.state.commands.safety.latched


@pytest.mark.parametrize("route", ["put", "apply", "snapshot"])
def test_new_stop_during_disk_staging_prevents_atomic_commit(recovery, monkeypatch, route):
    app, client, current, calls = recovery
    candidate = deepcopy(current)
    candidate["force"]["serial"]["leftPort"] = "COM99"
    original_write = app.state.settings._atomic_write_json

    def stop_before_write(path, payload, **kwargs):
        if path == app.state.settings.config_path:
            original_check = kwargs["before_commit"]

            def stop_before_commit():
                app.state.commands.safety.interrupt(emergency=True)
                original_check()

            kwargs["before_commit"] = stop_before_commit
        return original_write(path, payload, **kwargs)

    monkeypatch.setattr(app.state.settings, "_atomic_write_json", stop_before_write)
    response = request_change(client, candidate, route)
    assert response.status_code == 409, response.text
    assert client.get("/api/settings").json()["force"] == current["force"]
    assert not list(app.state.settings.config_path.parent.glob("config.json.*.tmp"))


def test_recovery_excludes_concurrent_ack_and_other_motion_resource_owners(recovery, monkeypatch):
    app, _client, current, calls = recovery
    apply_endpoint = next(route.endpoint for route in app.routes if route.path == "/api/settings/apply")
    ack_endpoint = next(route.endpoint for route in app.routes if route.path == "/api/motion/safety/acknowledge")

    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def slow_configure(name, payload):
            calls.append((name, payload))
            entered.set()
            await finish.wait()
            return {"response": {"ok": True}}

        monkeypatch.setattr(app.state.hal, "command", slow_configure)
        pending = asyncio.create_task(apply_endpoint(AppConfig.model_validate(current)))
        await entered.wait()
        try:
            with pytest.raises(HTTPException) as acknowledgement:
                await ack_endpoint()
            assert acknowledgement.value.status_code == 409
            assert "already in progress" in str(acknowledgement.value.detail)
            with pytest.raises(HTTPException) as second_recovery:
                await apply_endpoint(AppConfig.model_validate(current))
            assert "already in progress" in str(second_recovery.value.detail)
            assert app.state.commands.safety.latched
        finally:
            finish.set()
            await pending

    asyncio.run(exercise())
    assert [name for name, _ in calls] == ["force.configure"]
