from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app, emit_omega_device_logs
from backend.core.defaults import default_config
from backend.core.logging import LogService, PollingErrorLog
from backend.drivers.camera_opencv import OpenCVCameraDriver
from backend.hal_client.client import HalHealth
from backend.services.teleop_mapping import TeleopMappingService


def test_polling_error_keeps_first_counts_changed_error_and_recovery() -> None:
    clock = {"now": 0}
    logs = LogService(emit_startup=False, monotonic_ms=lambda: clock["now"])
    polling = PollingErrorLog(logs, interval_ms=1000)
    polling.failed("motion state", "[HAL]", "state failed: stale")
    for _ in range(19):
        polling.failed("motion state", "[HAL]", "state failed: stale")
    assert len(logs.list_entries()) == 1
    clock["now"] = 1000
    polling.failed("motion state", "[HAL]", "state failed: stale")
    assert logs.list_entries()[-1].msg.endswith("[repeated=20 total=21]")
    polling.failed("motion state", "[HAL]", "state failed: stale")
    polling.failed("motion state", "[HAL]", "state failed: disconnected")
    assert logs.list_entries()[-2].msg.endswith("[repeated=1 total=22]")
    assert logs.list_entries()[-1].msg == "state failed: disconnected"
    polling.failed("motion state", "[HAL]", "state failed: disconnected")
    polling.recovered("motion state")
    assert logs.list_entries()[-2].msg.endswith("[repeated=1 total=2]")
    assert logs.list_entries()[-1].msg == "motion state recovered; failures=2"
    assert all(entry.level == "ERROR" for entry in logs.list_entries()[:-1])
    assert logs.list_entries()[-1].level == "INFO"
    count = len(logs.list_entries())
    polling.recovered("motion state")
    assert len(logs.list_entries()) == count
    polling.failed("motion state", "[HAL]", "state failed: disconnected")
    assert len(logs.list_entries()) == count + 1


def test_polling_error_flush_preserves_pending_counts_without_claiming_recovery() -> None:
    logs = LogService(emit_startup=False)
    polling = PollingErrorLog(logs)
    for _ in range(3):
        polling.failed("force state", "[FORCE]", "force unavailable")
    polling.flush()
    polling.flush()
    assert [entry.msg for entry in logs.list_entries()] == [
        "force unavailable", "force unavailable [repeated=2 total=3]",
    ]
    # 普通用户命令和安全错误不走轮询合并。
    logs.error("[SAFETY]", "emergency requested")
    logs.error("[SAFETY]", "emergency requested")
    assert len(logs.list_entries()) == 4


def test_omega_periodic_identity_snapshot_is_debug() -> None:
    logs = LogService(emit_startup=False)
    emit_omega_device_logs(logs, default_config(), [{"side": "left", "connected": True, "deviceId": 3}])
    assert len(logs.list_entries()) == 1
    assert logs.list_entries()[0].level == "DEBUG"
    assert "event=omega_device" in logs.list_entries()[0].msg


def test_camera_mapping_relogs_only_changed_role(monkeypatch) -> None:
    logs = LogService(emit_startup=False)
    driver = OpenCVCameraDriver(logs)
    config = default_config()
    for key in ("globalIdentity", "wristLeftIdentity", "wristRightIdentity"):
        config["cameras"][key] = ""
    config["cameras"]["global"] = "index 0"
    config["cameras"]["wristLeft"] = "index 1"
    config["cameras"]["wristRight"] = "index 2"
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {})
    monkeypatch.setattr(driver, "_remap_software_sources", lambda resolved, _max_index: resolved)
    driver._resolved_indices(object(), config, 30, max_index=4)
    assert len(logs.list_entries()) == 3
    driver._resolved_cache_key = None
    driver._resolved_indices(object(), config, 30, max_index=4)
    assert len(logs.list_entries()) == 3
    config["cameras"]["global"] = "index 3"
    driver._resolved_indices(object(), config, 30, max_index=4)
    assert len(logs.list_entries()) == 4
    assert "role=global" in logs.list_entries()[-1].msg
    assert "resolvedIndex=3" in logs.list_entries()[-1].msg


def test_camera_runtime_snapshot_is_debug() -> None:
    logs = LogService(emit_startup=False)
    driver = OpenCVCameraDriver(logs)
    driver._record_frame_timestamp(0, 10.0)
    driver._record_frame_timestamp(0, 10.1)
    assert len(logs.list_entries()) == 1
    assert logs.list_entries()[0].level == "DEBUG"
    assert "event=camera_runtime" in logs.list_entries()[0].msg


@pytest.mark.parametrize("fault", [None, "native", "gripper"])
def test_native_status_keeps_faults_visible_and_healthy_snapshots_debug(fault) -> None:
    logs = LogService(emit_startup=False)
    mapper = TeleopMappingService(settings=None, hal=None, logs=logs)
    payload = {"running": True, "inputs": {"left": {"targetSide": "right"}}}
    if fault == "native":
        payload["lastError"] = "native error"
    elif fault == "gripper":
        payload["grippers"] = {"left": {"ok": False, "lastCommandTs": 1, "message": "port error"}}
    mapper._log_native_status_summary(default_config(), payload)
    summary = next(entry for entry in logs.list_entries() if entry.msg.startswith("native status "))
    assert summary.level == ("WARNING" if fault else "DEBUG")


def test_unchanged_pico_network_probe_is_debug(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setattr("backend.app.detect_pico_network", lambda *_args, **_kwargs: {
        "ifIndex": 987, "gateway": "10.90.0.1", "localIp": "10.90.1.42",
        "interfaceAlias": "test", "prefixLength": 17, "selection": "related-address",
    })
    client = TestClient(create_app(tmp_path))
    try:
        assert client.post("/api/pico/network/auto-configure").status_code == 200
        assert client.post("/api/pico/network/auto-configure").status_code == 200
        entries = [entry for entry in client.app.state.logs.list_entries() if entry.msg.startswith("PICO network selected")]
        assert [entry.level for entry in entries] == ["INFO", "DEBUG"]
    finally:
        client.app.state.telemetry.shutdown()


@pytest.mark.parametrize("fault", ["lastError", "updateReturn", "stopReason"])
@pytest.mark.parametrize("event", ["teleop_status", "teleop_axis_trace"])
def test_action_diagnostic_faults_keep_context_without_repeating_each_sample(monkeypatch, fault, event) -> None:
    clock = {"now": 1000}
    monkeypatch.setattr("backend.services.teleop_mapping.now_ms", lambda: clock["now"])
    logs = LogService(emit_startup=False, monotonic_ms=lambda: clock["now"])
    mapper = TeleopMappingService(settings=None, hal=None, logs=logs)
    config = default_config()
    config["teleop"]["diagLog"] = True
    action = {"sourceSide": "left", "side": "right", "axis": "Yaw",
              "requestedDeltas": {"Yaw": 0.5}, "currentPulse": {"Yaw": 1200},
              "updateReturn": {"Yaw": 0}, "stopReason": {"Yaw": 0}, "lastError": ""}
    action[fault] = "driver fault" if fault == "lastError" else {"Yaw": 21}

    def emit():
        if event == "teleop_status":
            mapper._log_diag_action(config, action)
        else:
            mapper._log_native_axis_trace(config, action, {})

    emit()
    assert logs.list_entries()[0].level == "WARNING"
    assert "sideMap=left->right" in logs.list_entries()[0].msg
    assert "currentPulse=[Yaw:1200]" in logs.list_entries()[0].msg
    action["currentPulse"] = {"Yaw": 1500}
    emit()
    assert len(logs.list_entries()) == 1
    clock["now"] += 5000
    emit()
    assert len(logs.list_entries()) == 2
    action[fault] = "different fault" if fault == "lastError" else {"Yaw": 22}
    emit()
    assert len(logs.list_entries()) == 3
    action[fault] = "" if fault == "lastError" else {"Yaw": 0}
    action["clipped"] = {"Yaw": True}
    emit()
    assert logs.list_entries()[-1].level == "DEBUG"
    action[fault] = "different fault" if fault == "lastError" else {"Yaw": 22}
    emit()
    assert logs.list_entries()[-1].level == "WARNING"
    assert len(logs.list_entries()) == 5


def test_native_action_fault_change_is_visible_even_with_unchanged_action_timestamp() -> None:
    logs = LogService(emit_startup=False)
    mapper = TeleopMappingService(settings=None, hal=None, logs=logs)
    config = default_config()
    config["teleop"]["diagLog"] = True
    action = {"ts": 123, "sourceSide": "left", "side": "right", "axis": "Yaw",
              "deltas": [0, 0, 0, 0, 0, 0.5], "updateReturn": [0] * 6, "stopReason": [0] * 6}
    mapper._log_native_diag_action(config, action, {"lastError": "fault"})
    mapper._last_error = "fault"
    mapper._log_native_diag_action(config, action, {"lastError": ""})
    mapper._log_native_diag_action(config, action, {"lastError": "fault"})
    assert [entry.level for entry in logs.list_entries()] == [
        "WARNING", "WARNING", "DEBUG", "DEBUG", "WARNING", "WARNING",
    ]


@pytest.mark.parametrize("source", ["health refresh", "motion state", "omega state", "HAL force state"])
def test_websocket_polling_errors_aggregate_then_report_recovery(tmp_path, monkeypatch, source) -> None:
    class FakeHal:
        calls = 0

        def read(self, key):
            if key == source:
                self.calls += 1
                if self.calls <= 3:
                    raise RuntimeError("test source unavailable")

        async def health(self):
            self.read("health refresh")
            return HalHealth(ltdmc_ok=True, omega7_ok=True, version="fake", uptime_s=1,
                             connected=True, mode="test")

        async def motion_state(self):
            self.read("motion state")
            return {"positions": [0.0] * 12, "pulses": [0.0] * 12,
                    "enabled": [False] * 12, "estop_active": False}

        async def omega_state(self):
            self.read("omega state")
            return {"hands": []}

        async def force_state(self):
            self.read("HAL force state")
            return {"source": "hkvl_serial", "left": [0.0] * 6, "right": [0.0] * 6}

    hal = FakeHal()
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_HEALTH_PERIOD_SEC", "0")
    monkeypatch.setenv("APPSTATION_HAL_STATE_PERIOD_SEC", "0")
    monkeypatch.setenv("APPSTATION_WS_PERIOD_SEC", "0.001")
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: hal)
    # 不运行生命周期；所有读取使用替身，不连接设备。
    client = TestClient(create_app(tmp_path))
    monkeypatch.setattr(client.app.state.gripper_router, "is_native", lambda _config: False)
    try:
        with client.websocket_connect("/ws?mode=observe") as ws:
            for _ in range(100):
                message = ws.receive_json()
                if message["type"] == "log" and message["data"]["msg"] == f"{source} recovered; failures=3":
                    break
            else:
                raise AssertionError("polling recovery was not sent")
        relevant = [entry for entry in client.app.state.logs.list_entries() if entry.msg.startswith(source)]
        assert [entry.level for entry in relevant] == ["ERROR", "ERROR", "INFO"]
        assert relevant[1].msg.endswith("[repeated=2 total=3]")
    finally:
        client.app.state.telemetry.shutdown()
