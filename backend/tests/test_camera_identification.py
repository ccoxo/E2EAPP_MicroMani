import pytest

from backend.core.defaults import default_config
from backend.drivers.camera_opencv import OpenCVCameraDriver


def test_top_camera_defaults_use_serial_and_leave_wrists_for_identification():
    cameras = default_config()["cameras"]
    assert cameras["globalIdentity"] == "20250606105"
    assert cameras["wristLeftIdentity"] == cameras["wristRightIdentity"] == ""
    assert cameras["wristLeft"] == cameras["wristRight"] == "index -1"


def test_old_top_binding_is_migrated_without_overwriting_custom_wrists(tmp_path):
    from backend.core.config import SettingsService
    from backend.core.logging import LogService
    settings = SettingsService(tmp_path, LogService(emit_startup=False))
    config = settings.get_config()
    config["cameras"].update(globalIdentity="USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000",
                             wristLeftIdentity="USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000",
                             wristRightIdentity="custom-confirmed-right")
    settings.save_config(config, emit_log=False)
    loaded = SettingsService(tmp_path, LogService(emit_startup=False)).get_config()["cameras"]
    assert loaded["globalIdentity"] == "20250606105"
    assert loaded["wristLeftIdentity"] == "" and loaded["wristLeft"] == "index -1"
    assert loaded["wristRightIdentity"] == "custom-confirmed-right"


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_DISABLE_CAMERA_PROBE", "1")


def test_wrist_candidates_exclude_global_and_virtual_and_use_stable_identity(monkeypatch):
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {
        4: {"parentId": "USB\\VID_TEST\\20250606105", "devicePath": "global"},
        2: {"parentId": "USB\\VID_TEST\\7&generated", "locationPath": "PCIROOT(0)#USB(2)",
            "devicePath": "left", "name": "USB Camera"},
        0: {"parentId": "USB\\VID_TEST\\unique-right", "devicePath": "right"},
        3: {"displayName": "@device:sw:virtual", "devicePath": ""},
    })
    config = default_config()
    config["cameras"]["globalIdentity"] = "20250606105"
    devices = driver.wrist_candidates(config)
    assert [d["index"] for d in devices] == [2, 0]
    assert devices[0]["identity"] == "PCIROOT(0)#USB(2)"
    assert devices[1]["identity"] == "USB\\VID_TEST\\unique-right"


@pytest.mark.parametrize("identity", ["", "missing", "USB Camera"])
def test_candidates_require_a_unique_online_global_identity(monkeypatch, identity):
    driver = OpenCVCameraDriver()
    config = default_config()
    config["cameras"]["globalIdentity"] = identity
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {
        0: {"name": "USB Camera", "devicePath": "first", "parentId": "serial-a"},
        1: {"name": "USB Camera", "devicePath": "second", "parentId": "serial-b"},
    })
    with pytest.raises(ValueError):
        driver.wrist_candidates(config)


def test_binding_rejects_two_interfaces_with_the_same_stable_identity(monkeypatch):
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "wrist_candidates", lambda config: [
        {"devicePath": "first", "identity": "same-camera", "index": 0},
        {"devicePath": "second", "identity": "same-camera", "index": 1},
    ])
    with pytest.raises(ValueError):
        driver.wrist_binding(default_config(), "first", "second")


def test_binding_rejects_duplicate_missing_or_replaced_candidates(monkeypatch):
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "wrist_candidates", lambda config: [
        {"devicePath": "left", "identity": "port-left", "index": 2},
        {"devicePath": "right", "identity": "port-right", "index": 0},
    ])
    config = default_config()
    for left, right in [("left", "left"), ("left", "removed")]:
        with pytest.raises(ValueError):
            driver.wrist_binding(config, left, right)
    result = driver.wrist_binding(config, "left", "right")
    assert result["wristLeftIdentity"] == "port-left"
    assert result["wristRightIdentity"] == "port-right"
    assert result["globalIdentity"] == config["cameras"]["globalIdentity"]


def test_saved_parent_serial_and_usb_location_resolve_after_reenumeration(monkeypatch):
    config = default_config()
    config["cameras"].update({
        "globalIdentity": "global-serial",
        "wristLeftIdentity": "PCIROOT(0)#USB(3)",
        "wristRightIdentity": "USB\\VID_TEST\\right-serial",
    })
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {
        2: {"devicePath": "new-global", "parentId": "USB\\VID_TEST\\global-serial"},
        3: {"devicePath": "new-left", "parentId": "USB\\VID_TEST\\7&generated", "locationPath": "PCIROOT(0)#USB(3)"},
        0: {"devicePath": "new-right", "parentId": "USB\\VID_TEST\\right-serial"},
    })
    assert driver._resolved_indices(object(), config, 30) == {"global": 2, "wrist_left": 3, "wrist_right": 0}


def test_missing_bound_camera_does_not_fall_back_to_another_device(monkeypatch):
    config = default_config()
    config["cameras"].update({
        "global": "IMX335 / index 0", "globalIdentity": "missing-global",
        "wristLeft": "IMX335 / index 1", "wristLeftIdentity": "left",
        "wristRight": "IMX335 / index 2", "wristRightIdentity": "right",
    })
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {
        0: {"devicePath": "left"}, 1: {"devicePath": "unrelated"}, 2: {"devicePath": "right"},
    })
    monkeypatch.setattr(driver, "_discover_readable_indices", lambda *args: pytest.fail("must not open fallback devices"))
    assert driver._resolved_indices(object(), config, 30) == {"global": -1, "wrist_left": 0, "wrist_right": 2}


def test_legacy_camera_labels_without_custom_identity_still_migrate(tmp_path):
    from backend.core.config import SettingsService
    from backend.core.logging import LogService

    settings = SettingsService(tmp_path, LogService())
    config = default_config()
    config["cameras"].update({"global": "IMX335 / index 1", "wristLeft": "IMX335 / index 2", "wristRight": "IMX335 / index 0"})
    settings.save_config(config)
    cameras = settings.get_config()["cameras"]
    expected = default_config()["cameras"]
    for field in ("global", "wristLeft", "wristRight", "globalIdentity", "wristLeftIdentity", "wristRightIdentity"):
        assert cameras[field] == expected[field]


def test_explicit_identity_binding_survives_reload_with_legacy_camera_labels(tmp_path):
    from backend.core.config import SettingsService
    from backend.core.logging import LogService

    settings = SettingsService(tmp_path, LogService())
    config = default_config()
    config["cameras"].update({
        "wristLeft": "IMX335 / index 2", "wristRight": "IMX335 / index 0",
        "wristLeftIdentity": "USB\\VID_TEST\\left-serial", "wristRightIdentity": "PCIROOT(0)#USB(4)",
    })
    settings.save_config(config)
    assert SettingsService(tmp_path, LogService()).get_config()["cameras"] == config["cameras"]


def test_identification_api_and_binding_save(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.drivers.camera_opencv import CameraProbeResult

    app = create_app(tmp_path)
    client = TestClient(app)
    driver = app.state.hardware.cameras
    monkeypatch.setattr(driver, "identify_wrists", lambda config: [{"devicePath": "left", "preview": "image"}])
    monkeypatch.setattr(driver, "wrist_candidates", lambda config: [
        {"devicePath": "left", "identity": "port-left", "index": 2},
        {"devicePath": "right", "identity": "port-right", "index": 0},
    ])
    reconnects = []
    monkeypatch.setattr(driver, "reconnect", lambda config: (
        reconnects.append(config["cameras"]) or CameraProbeResult(True, "connected", [])
    ))
    original = client.get("/api/settings").json()
    assert client.post("/api/cameras/wrists/identify").json()["data"]["devices"][0]["preview"] == "image"
    assert client.post("/api/cameras/wrists/bind", json={"left": "left", "right": "left"}).status_code == 400
    assert client.get("/api/settings").json()["cameras"] == original["cameras"]
    response = client.post("/api/cameras/wrists/bind", json={"left": "left", "right": "right"})
    assert response.status_code == 200
    saved = client.get("/api/settings").json()
    assert saved["cameras"]["wristLeftIdentity"] == "port-left"
    assert saved["cameras"]["wristRightIdentity"] == "port-right"
    assert saved["cameras"]["globalIdentity"] == original["cameras"]["globalIdentity"]
    assert len(reconnects) == 1


def test_scan_keeps_config_and_reports_individual_preview_failure(monkeypatch):
    from copy import deepcopy

    driver = OpenCVCameraDriver()
    config = default_config()
    original = deepcopy(config)
    monkeypatch.setattr(driver, "wrist_candidates", lambda config: [
        {"index": 0, "devicePath": "left"}, {"index": 2, "devicePath": "right"},
    ])

    def snapshot(candidate, role):
        assert role == "wrist_left"
        if candidate["cameras"]["wristLeftIdentity"] == "right":
            raise RuntimeError("camera unavailable")
        return b"jpeg"

    monkeypatch.setattr(driver, "snapshot", snapshot)
    result = driver.identify_wrists(config)
    assert result[0]["preview"].startswith("data:image/jpeg;base64,")
    assert result[1]["error"] == "camera unavailable"
    assert config == original


def test_recording_blocks_identify_and_bind(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.services.dataset_recorder import DatasetRecorderService

    monkeypatch.setattr(DatasetRecorderService, "status", lambda self: {"active": True})
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/cameras/wrists/identify").status_code == 409
    assert client.post("/api/cameras/wrists/bind", json={"left": "left", "right": "right"}).status_code == 409


@pytest.mark.parametrize("busy_field,busy_value", [("_session_starting", True), ("_writer_thread", object())])
def test_startup_and_writer_finalization_block_camera_changes(tmp_path, monkeypatch, busy_field, busy_value):
    from fastapi.testclient import TestClient

    from backend.app import create_app

    app = create_app(tmp_path)
    recorder = app.state.recorder
    monkeypatch.setattr(recorder, "status", lambda: {"active": False, "recording": False})
    monkeypatch.setattr(recorder, busy_field, busy_value)
    monkeypatch.setattr(app.state.hardware.cameras, "identify_wrists", lambda config: pytest.fail("must not open cameras"))
    monkeypatch.setattr(app.state.hardware.cameras, "wrist_binding", lambda *args: pytest.fail("must not bind cameras"))
    client = TestClient(app)
    assert client.post("/api/cameras/wrists/identify").status_code == 409
    assert client.post("/api/cameras/wrists/bind", json={"left": "left", "right": "right"}).status_code == 409


def test_scan_releases_temporary_camera_before_opening_next(monkeypatch):
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "wrist_candidates", lambda config: [
        {"index": 0, "devicePath": "first"}, {"index": 2, "devicePath": "second"},
    ])
    opened = []
    released = []

    def snapshot(config, role):
        assert not opened, "previous temporary camera still occupies USB bandwidth"
        index = 0 if config["cameras"]["wristLeftIdentity"] == "first" else 2
        opened.append(index)
        return b"jpeg"

    def drop(index):
        released.append(index)
        opened.remove(index)

    monkeypatch.setattr(driver, "snapshot", snapshot)
    monkeypatch.setattr(driver, "_drop_capture", drop)
    result = driver.identify_wrists(default_config())
    assert all("preview" in device for device in result)
    assert released == [0, 2]


def test_scan_preserves_existing_preview_capture(monkeypatch):
    driver = OpenCVCameraDriver()
    existing = object()
    driver._captures[2] = existing
    monkeypatch.setattr(driver, "wrist_candidates", lambda config: [{"index": 2, "devicePath": "existing"}])
    monkeypatch.setattr(driver, "snapshot", lambda config, role: b"jpeg")
    result = driver.identify_wrists(default_config())
    assert "preview" in result[0]
    assert driver._captures[2] is existing
