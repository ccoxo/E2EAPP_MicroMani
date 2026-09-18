from backend.core.defaults import default_config
from backend.drivers.camera_opencv import OpenCVCameraDriver


def test_wrist_candidates_exclude_global_and_virtual_and_use_stable_identity(monkeypatch):
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {
        4: {"parentId": "USB\\VID_TEST\\20250606105", "devicePath": "global"},
        2: {"parentId": "USB\\VID_TEST\\7&generated", "locationPath": "PCIROOT(0)#USB(2)",
            "devicePath": "left", "name": "USB Camera"},
        0: {"parentId": "USB\\VID_TEST\\unique-right", "devicePath": "right"},
        3: {"displayName": "@device:sw:virtual", "devicePath": ""},
    })
    devices = driver.wrist_candidates(default_config())
    assert [d["index"] for d in devices] == [2, 0]
    assert devices[0]["identity"] == "PCIROOT(0)#USB(2)"
    assert devices[1]["identity"] == "USB\\VID_TEST\\unique-right"


def test_binding_rejects_duplicate_missing_or_replaced_candidates(monkeypatch):
    import pytest

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
