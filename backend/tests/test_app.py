from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.app import create_app
from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.drivers.camera_opencv import OpenCVCameraDriver
from backend.hal_client.client import RealHalClient


def test_settings_round_trip(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/settings")
    assert response.status_code == 200
    config = response.json()
    assert config["motion"]["leftCardNo"] == 1

    config["hal"]["apiConfirmed"] = True
    put_response = client.put("/api/settings", json=config)
    assert put_response.status_code == 200
    assert put_response.json()["hal"]["apiConfirmed"] is True

    assert client.get("/api/settings").json()["hal"]["apiConfirmed"] is True


def test_motion_snapshot_create_apply_delete(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    snapshot_config = {
        "cardNo": 7,
        "motionThreadHz": 1000,
        "yawSoftLimitDeg": 7.5,
        "positionSource": "dmc_get_position",
        "profile": config["motion"]["leftProfile"],
        "softLimits": config["motion"]["leftSoftLimits"],
    }

    create_response = client.post(
        "/api/settings/snapshots",
        json={"scope": "motion-left", "name": "left-default", "config": snapshot_config},
    )
    assert create_response.status_code == 200
    snapshot = create_response.json()["data"]["snapshot"]

    config["motion"]["leftCardNo"] = 2
    client.put("/api/settings", json=config)

    apply_response = client.post(f"/api/settings/snapshots/{snapshot['id']}/apply")
    assert apply_response.status_code == 200
    assert apply_response.json()["data"]["config"]["motion"]["leftCardNo"] == 7

    delete_response = client.delete(f"/api/settings/snapshots/{snapshot['id']}")
    assert delete_response.status_code == 200
    assert client.get("/api/settings/snapshots?scope=motion-left").json() == []


def test_command_envelope_and_telemetry_ws(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    command_response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 100, "speedMode": "fine"},
    )
    assert command_response.status_code == 200
    assert command_response.json()["ok"] is True

    with client.websocket_connect("/ws") as websocket:
        message = websocket.receive_json()
    assert message["type"] == "telemetry"
    frame = message["data"]
    assert len(frame["jointPositions"]) == 12
    assert frame["motionEnabled"] == {"left": None, "right": None}
    assert len(frame["forceLeft"]) == 6
    assert len(frame["teleopHands"]) == 2
    assert frame["wsOk"] is True

    teleop_response = client.get("/api/teleop/state")
    assert teleop_response.status_code == 200
    assert len(teleop_response.json()["data"]["hands"]) == 2


def test_motion_side_enable_and_home_routes(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    enable_response = client.post("/api/motion/left/enable_all")
    assert enable_response.status_code == 200
    assert enable_response.json()["data"]["command"] == "motion.enable_side"

    motion_state = client.app.state.hal.motion_state()
    state = asyncio.run(motion_state)
    assert state["enabled"][:6] == [True] * 6
    assert state["enabled"][6:] == [False] * 6

    home_response = client.post("/api/motion/right/home")
    assert home_response.status_code == 200
    assert home_response.json()["data"]["command"] == "motion.home_side"

    bad_side_response = client.post("/api/motion/center/enable_all")
    assert bad_side_response.status_code == 400


def test_motion_origin_capture_clear_and_per_side_config(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    capture_response = client.post("/api/motion/origin/capture")
    assert capture_response.status_code == 200
    origin = capture_response.json()["data"]["origin"]
    assert origin["valid"] is True
    assert origin["leftValid"] is True
    assert origin["rightValid"] is True
    assert origin["leftPulse"] == [0.0] * 6
    assert origin["rightPulse"] == [0.0] * 6

    clear_response = client.post("/api/motion/origin/clear")
    assert clear_response.status_code == 200
    cleared = clear_response.json()["data"]["origin"]
    assert cleared["valid"] is False
    assert cleared["leftValid"] is False
    assert cleared["rightValid"] is False

    left_response = client.post("/api/motion/left/origin/capture")
    assert left_response.status_code == 200
    left_origin = left_response.json()["data"]["origin"]
    assert left_origin["leftValid"] is True
    assert left_origin["rightValid"] is False
    assert left_origin["valid"] is False


def test_motion_origin_relative_positions_are_applied_per_side(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    recorder = client.app.state.recorder
    origin = {
        "valid": False,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": [100.0, -200.0, 300.0, 0.0, 0.0, 0.0],
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    positions = [42.0] * 12
    pulses = [
        -8900.0,
        -10200.0,
        -9700.0,
        1666.666667,
        2500.0,
        3333.333333,
        10.0,
        20.0,
        30.0,
        40.0,
        50.0,
        60.0,
    ]

    relative = recorder._recording_motion_positions({"motion": {"origin": origin}}, positions, pulses)

    assert relative[0] == pytest.approx(1000.0)
    assert relative[1] == pytest.approx(1000.0)
    assert relative[2] == pytest.approx(1000.0)
    assert relative[3] == pytest.approx(1.0)
    assert relative[6] == 42.0


def test_websocket_reconnect_cancels_runtime_shutdown(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    shutdown_response = client.post("/api/runtime/shutdown", json={"reason": "reload"})
    assert shutdown_response.status_code == 200
    assert client.app.state.shutdown_task is not None

    with client.websocket_connect("/ws") as websocket:
        message = websocket.receive_json()

    assert message["type"] == "telemetry"
    assert client.app.state.shutdown_task is None


def test_teleop_logical_connect_disconnect_does_not_touch_motion(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    connect_response = client.post("/api/teleop/left/connect")
    assert connect_response.status_code == 200
    assert connect_response.json()["data"]["connected"] is True
    assert client.get("/api/settings").json()["teleop"]["leftConnected"] is True

    with client.websocket_connect("/ws") as websocket:
        frame = websocket.receive_json()["data"]
    left_hand = next(hand for hand in frame["teleopHands"] if hand["side"] == "left")
    assert left_hand["connected"] is True
    assert left_hand["lastReadOk"] is True

    disconnect_response = client.post("/api/teleop/left/disconnect")
    assert disconnect_response.status_code == 200
    assert disconnect_response.json()["data"]["connected"] is False
    assert client.get("/api/settings").json()["teleop"]["leftConnected"] is False

    command_response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 100, "speedMode": "fine"},
    )
    assert command_response.status_code == 200


def test_record_session_writes_lerobot_fallback_dataset(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "0")
    dataset_root = tmp_path / "datasets"
    with TestClient(create_app(tmp_path / "runtime")) as client:
        config = client.get("/api/settings").json()
        config["storage"]["datasetRoot"] = str(dataset_root)
        config["force"]["sampleHz"] = 4000
        assert client.put("/api/settings", json=config).status_code == 200

        start_response = client.post(
            "/api/record/session/create",
            json={"dataset_name": "unit_test_dataset", "task": "pytest capture"},
        )
        assert start_response.status_code == 200
        assert start_response.json()["data"]["recording"] is True
        assert start_response.json()["data"]["forceSampleHz"] == 4000
        assert start_response.json()["data"]["forceWindowSamples"] == 134
        time.sleep(0.2)

        save_response = client.post("/api/record/episode/save")
        assert save_response.status_code == 200
        episode = save_response.json()["data"]["episode"]
        assert episode["frames"] > 0

        datasets_response = client.get("/api/datasets")
        assert datasets_response.status_code == 200
        datasets = datasets_response.json()["data"]["datasets"]
        dataset = next(item for item in datasets if item["id"] == "unit_test_dataset")
        assert dataset["format"] == "lerobot-compatible-jsonl-fallback"
        assert dataset["episodes"][0]["samples"]

        data_path = dataset_root / "unit_test_dataset" / episode["dataPath"]
        assert data_path.exists()
        first_line = data_path.read_text(encoding="utf-8").splitlines()[0]
        frame = json.loads(first_line)
        assert len(frame["observation.state"]) == 12
        assert len(frame["observation.force_left"]) == 6
        assert len(frame["observation.force_left_window"]) == 134
        assert len(frame["observation.force_left_window"][0]) == 6
        assert len(frame["observation.force_window_dt"]) == 134
        assert len(frame["action"]) == 12

        finish_response = client.post("/api/record/session/finish")
        assert finish_response.status_code == 200


def test_record_session_writes_native_lerobot_dataset_when_available(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    if importlib.util.find_spec("lerobot") is None:
        pytest.skip("lerobot[dataset] is not installed in this backend environment")
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "1")
    monkeypatch.setenv("APPSTATION_LEROBOT_USE_VIDEOS", "0")
    dataset_root = tmp_path / "datasets"
    with TestClient(create_app(tmp_path / "runtime")) as client:
        config = client.get("/api/settings").json()
        config["storage"]["datasetRoot"] = str(dataset_root)
        config["cameras"]["previewResolution"] = "160x120"
        config["force"]["sampleHz"] = 1000
        assert client.put("/api/settings", json=config).status_code == 200

        start_response = client.post(
            "/api/record/session/create",
            json={"dataset_name": "native_unit_test_dataset", "task": "pytest native capture"},
        )
        assert start_response.status_code == 200
        assert start_response.json()["data"]["format"] == "lerobot-v3-native"
        assert start_response.json()["data"]["forceWindowSamples"] == 34
        time.sleep(0.2)

        save_response = client.post("/api/record/episode/save")
        assert save_response.status_code == 200
        episode = save_response.json()["data"]["episode"]
        assert episode["native"] is True
        assert episode["frames"] > 0

        datasets_response = client.get("/api/datasets")
        assert datasets_response.status_code == 200
        dataset = next(
            item
            for item in datasets_response.json()["data"]["datasets"]
            if item["id"] == "native_unit_test_dataset"
        )
        assert dataset["format"] == "lerobot-v3-native"
        assert dataset["episodes"][0]["samples"]

        frame_response = client.get(
            "/api/datasets/native_unit_test_dataset/frame_image",
            params={"episode_id": episode["id"], "camera": "global", "frame": 0},
        )
        assert frame_response.status_code == 200
        assert frame_response.headers["content-type"] == "image/jpeg"
        assert frame_response.content.startswith(b"\xff\xd8")
        info_path = dataset_root / "native_unit_test_dataset" / "meta" / "info.json"
        assert info_path.exists()
        info = json.loads(info_path.read_text(encoding="utf-8"))
        assert info["features"]["observation.force_left_window"]["shape"] == [34, 6]
        assert info["features"]["observation.force_window_dt"]["shape"] == [34]
        assert (dataset_root / "native_unit_test_dataset" / "data" / "chunk-000" / "file-000.parquet").exists()

        finish_response = client.post("/api/record/session/finish")
        assert finish_response.status_code == 200


def test_native_recording_resizes_camera_frames_to_feature_shape(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    client = TestClient(create_app(tmp_path))
    recorder = client.app.state.recorder
    config = client.get("/api/settings").json()
    config["cameras"]["previewResolution"] = "160x120"
    config["cameras"]["globalResolution"] = "160x120"
    source = np.zeros((480, 640, 3), dtype=np.uint8)
    source[:, :, 0] = 30
    source[:, :, 1] = 90
    source[:, :, 2] = 150
    ok, buffer = cv2.imencode(".jpg", source)
    assert ok
    monkeypatch.setattr(recorder, "_native_imports", lambda: (object, np))

    frame = recorder._decode_jpeg_to_rgb(bytes(buffer), config)

    assert frame.shape == (120, 160, 3)
    assert frame.dtype == np.uint8


def test_dataset_review_and_export_endpoints(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "0")
    dataset_root = tmp_path / "datasets"
    client = TestClient(create_app(tmp_path / "runtime"))
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    assert client.put("/api/settings", json=config).status_code == 200

    create_response = client.post("/api/datasets", json={"name": "review_dataset"})
    assert create_response.status_code == 200
    assert create_response.json()["data"]["dataset"]["id"] == "review_dataset"

    save_response = client.post("/api/datasets/review_dataset/review/save")
    assert save_response.status_code == 200
    assert save_response.json()["data"]["saved"] == "review_dataset"

    export_response = client.post("/api/datasets/review_dataset/export")
    assert export_response.status_code == 200
    assert export_response.json()["data"]["pushToHub"] is False

    stats_response = client.get("/api/datasets/review_dataset/stats")
    assert stats_response.status_code == 200
    assert stats_response.json()["data"]["episodes"] == 0

    split_response = client.post(
        "/api/datasets/review_dataset/split",
        json={"ratios": {"train": 0.7, "val": 0.2, "test": 0.1}},
    )
    assert split_response.status_code == 200
    assert set(split_response.json()["data"]["splits"]) == {"train", "val", "test"}

    clean_response = client.post("/api/datasets/review_dataset/clean", json={"apply": False, "minFrames": 2})
    assert clean_response.status_code == 200
    assert clean_response.json()["data"]["checked"] == 0

    push_response = client.post("/api/datasets/review_dataset/push", json={"repoId": "local/test", "dryRun": True})
    assert push_response.status_code == 200
    assert push_response.json()["data"]["pushed"] is False


def test_create_dataset_can_resume_native_lerobot_recording(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    if importlib.util.find_spec("lerobot") is None:
        pytest.skip("lerobot[dataset] is not installed in this backend environment")
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "1")
    monkeypatch.setenv("APPSTATION_LEROBOT_USE_VIDEOS", "0")
    dataset_root = tmp_path / "datasets"
    with TestClient(create_app(tmp_path / "runtime")) as client:
        config = client.get("/api/settings").json()
        config["storage"]["datasetRoot"] = str(dataset_root)
        config["cameras"]["previewResolution"] = "160x120"
        assert client.put("/api/settings", json=config).status_code == 200

        create_response = client.post("/api/datasets", json={"name": "created_native_dataset"})
        assert create_response.status_code == 200
        info = json.loads(
            (dataset_root / "created_native_dataset" / "meta" / "info.json").read_text(encoding="utf-8")
        )
        assert str(info["codebase_version"]).startswith("v3.")

        rename_response = client.patch("/api/datasets/created_native_dataset", json={"name": "Created Native"})
        assert rename_response.status_code == 200
        save_review_response = client.post("/api/datasets/created_native_dataset/review/save")
        assert save_review_response.status_code == 200

        start_response = client.post(
            "/api/record/session/create",
            json={"dataset_name": "created_native_dataset", "task": "pytest resume native"},
        )
        assert start_response.status_code == 200
        assert start_response.json()["data"]["format"] == "lerobot-v3-native"
        time.sleep(0.2)

        save_response = client.post("/api/record/episode/save")
        assert save_response.status_code == 200
        assert save_response.json()["data"]["episode"]["native"] is True

        datasets_response = client.get("/api/datasets")
        assert datasets_response.status_code == 200
        dataset = next(
            item
            for item in datasets_response.json()["data"]["datasets"]
            if item["id"] == "created_native_dataset"
        )
        assert dataset["name"] == "Created Native"
        assert dataset["format"] == "lerobot-v3-native"
        assert dataset["episodes"][0]["samples"]

        finish_response = client.post("/api/record/session/finish")
        assert finish_response.status_code == 200


def test_manual_axis_move_rejects_unsafe_step(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 5001, "speedMode": "fine"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MOTION_UNAVAILABLE"


def test_camera_snapshot_endpoint_returns_jpeg(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    class FakeCapture:
        def __init__(self, index: int, backend: int) -> None:
            self.index = index
            self.backend = backend

        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: int) -> None:
            _ = (prop, value)

        def read(self) -> tuple[bool, object]:
            return True, object()

        def release(self) -> None:
            return None

    class FakeCv2:
        CAP_DSHOW = 700
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        IMWRITE_JPEG_QUALITY = 1

        @staticmethod
        def VideoCapture(index: int, backend: int) -> FakeCapture:
            return FakeCapture(index, backend)

        @staticmethod
        def imencode(ext: str, frame: object, options: list[int]) -> tuple[bool, bytes]:
            _ = (ext, frame, options)
            return True, b"\xff\xd8fake-jpeg\xff\xd9"

    monkeypatch.setattr("backend.drivers.camera_opencv.import_module", lambda name: FakeCv2)
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["cameras"]["global"] = "Global UVC / index 0"
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.get("/api/cameras/global/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_camera_driver_falls_back_when_first_windows_backend_fails(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []

    class FakeCapture:
        def __init__(self, index: int, backend: int) -> None:
            self.index = index
            self.backend = backend
            calls.append((index, backend))

        def isOpened(self) -> bool:
            return self.backend == 700

        def set(self, prop: int, value: int) -> None:
            _ = (prop, value)

        def get(self, prop: int) -> float:
            _ = prop
            return 30.0

        def read(self) -> tuple[bool, object]:
            return True, object()

        def release(self) -> None:
            return None

    class FakeCv2:
        CAP_ANY = 0
        CAP_MSMF = 1400
        CAP_DSHOW = 700
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5
        CAP_PROP_FOURCC = 6
        CAP_PROP_BUFFERSIZE = 7
        IMWRITE_JPEG_QUALITY = 1

        @staticmethod
        def VideoCapture(index: int, backend: int) -> FakeCapture:
            return FakeCapture(index, backend)

        @staticmethod
        def VideoWriter_fourcc(a: str, b: str, c: str, d: str) -> int:
            _ = (a, b, c, d)
            return 1

        @staticmethod
        def imencode(ext: str, frame: object, options: list[int]) -> tuple[bool, bytes]:
            _ = (ext, frame, options)
            return True, b"\xff\xd8fake-jpeg\xff\xd9"

    monkeypatch.setenv("APPSTATION_CAMERA_BACKEND", "msmf")
    monkeypatch.setattr("backend.drivers.camera_opencv.import_module", lambda name: FakeCv2)
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["cameras"]["global"] = "Global UVC / index 0"
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.get("/api/cameras/global/snapshot")

    assert response.status_code == 200
    assert calls[:2] == [(0, 1400), (0, 700)]


def test_global_camera_auto_index_uses_remaining_device(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    config["cameras"]["global"] = "Global UVC / index -1"
    config["cameras"]["wristLeft"] = "IMX258 / index 2"
    config["cameras"]["wristRight"] = "IMX258 / index 0"
    driver = OpenCVCameraDriver()

    def fake_discover_readable_indices(
        cv2: object, width: int, height: int, fps: float, max_index: int
    ) -> list[int]:
        _ = (cv2, width, height, fps, max_index)
        return [0, 1, 2]

    monkeypatch.setattr(driver, "_discover_readable_indices", fake_discover_readable_indices)

    resolved = driver._resolved_indices(object(), config, 30)

    assert resolved == {"global": 1, "wrist_left": 2, "wrist_right": 0}


def test_default_camera_mapping_matches_deployment_hardware() -> None:
    config = default_config()

    assert config["cameras"]["global"] == "AR0234 / index 1"
    assert config["cameras"]["wristLeft"] == "IMX258 / index 2"
    assert config["cameras"]["wristRight"] == "IMX258 / index 0"
    assert config["cameras"]["previewResolution"] == "640x480"
    assert config["cameras"]["globalResolution"] == "640x480"
    assert config["cameras"]["wristLeftResolution"] == "640x480"
    assert config["cameras"]["wristRightResolution"] == "640x480"


def test_camera_reconnect_releases_selected_capture(monkeypatch: MonkeyPatch) -> None:
    class FakeCapture:
        released = False

        def release(self) -> None:
            self.released = True

    driver = OpenCVCameraDriver()
    config = default_config()
    capture = FakeCapture()
    driver._captures[2] = capture
    driver._capture_sizes[2] = (320, 240, 30.0)
    driver._cached = driver._store(True, "cached", [])
    driver._resolved_cache_key = ("old",)
    driver._resolved_cache = {"global": 1, "wrist_left": 2, "wrist_right": 0}

    monkeypatch.setattr("backend.drivers.camera_opencv.import_module", lambda name: object())
    monkeypatch.setattr(driver, "_resolved_indices", lambda cv2, cfg, fps: {"global": 2})
    monkeypatch.setattr(driver, "probe", lambda cfg: driver._store(True, "ok", []))

    result = driver.reconnect(config, "global")

    assert result.ok is True
    assert capture.released is True
    assert 2 not in driver._captures
    assert driver._resolved_cache_key is None


def test_real_hal_mode_reports_unavailable_without_service(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    monkeypatch.setenv("APPSTATION_HAL_BASE_URL", "http://127.0.0.1:65530")

    def fake_hardware_status(self: object) -> dict[str, object]:
        _ = self
        return {}

    monkeypatch.setattr("backend.services.hardware_service.HardwareService.status", fake_hardware_status)
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "real"
    assert payload["hal"]["connected"] is False
    assert payload["hal"]["ltdmc_ok"] is False


def test_real_hal_http_error_preserves_hal_message(monkeypatch: MonkeyPatch) -> None:
    class FakeResponse:
        status = 500
        headers = {"Connection": "close"}

        def read(self) -> bytes:
            return b'{"ok":false,"message":"dmc_pmove failed ret=7 card=1 axis=0"}'

    class FakeConnection:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)
            self.sock = type("FakeSocket", (), {"setsockopt": staticmethod(lambda *a, **k: None)})()

        def connect(self) -> None:
            return None

        def request(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("backend.hal_client.client.http.client.HTTPConnection", FakeConnection)
    client = RealHalClient("http://127.0.0.1:8091", 5000, LogService())

    try:
        asyncio.run(client.command("motion.manual_axis_move", {"side": "left"}))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "HTTP 500" in message
    assert "dmc_pmove failed ret=7 card=1 axis=0" in message


def test_runtime_shutdown_endpoint_schedules_stop_stack(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        pass

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        _ = kwargs
        calls.append(args)
        return FakeProcess()

    monkeypatch.setenv("APPSTATION_CLOSE_SHUTDOWN_DELAY_SEC", "0.01")
    monkeypatch.setattr("backend.app.subprocess.Popen", fake_popen)

    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/runtime/shutdown", json={"reason": "test-close"})
        assert response.status_code == 200
        assert response.json()["data"]["scheduled"] is True
        time.sleep(0.1)

    assert calls
    assert calls[0][-1].endswith("stop-stack.ps1")


def test_stability_monitor_runs_short_non_motion_check(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/stability/start",
        json={"durationS": 0.08, "samplePeriodS": 0.02, "includeCameras": False, "includeForce": True},
    )
    assert response.status_code == 200
    assert response.json()["data"]["active"] is True
    time.sleep(0.2)

    status_response = client.get("/api/stability/status")
    assert status_response.status_code == 200
    status = status_response.json()["data"]
    assert status["active"] is False
    assert status["hal"]["connectedSamples"] > 0
    assert status["force"]["skippedTestMode"] > 0


def test_policy_model_auto_and_fine_tune_endpoints_are_conservative(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    models_response = client.get("/api/models")
    assert models_response.status_code == 200
    assert any(model["id"] == "act" for model in models_response.json()["data"]["models"])

    start_response = client.post("/api/auto/start", json={"modelId": "act"})
    assert start_response.status_code == 200
    assert start_response.json()["data"]["running"] is True
    assert start_response.json()["data"]["dispatchEnabled"] is False

    action_response = client.post(
        "/api/auto/action",
        json={
            "side": "left",
            "axis": "X",
            "direction": 1,
            "step": 50,
            "speedMode": "fine",
            "maxVelocityUiPerSec": 50,
        },
    )
    assert action_response.status_code == 200

    dispatch_response = client.post("/api/auto/dispatch_next")
    assert dispatch_response.status_code == 200
    assert dispatch_response.json()["data"]["dispatched"] is False
    assert dispatch_response.json()["data"]["reason"] == "hardware dispatch disabled"

    rejected_response = client.post(
        "/api/auto/action",
        json={"side": "left", "axis": "X", "direction": 1, "step": 1000, "maxVelocityUiPerSec": 50},
    )
    assert rejected_response.status_code == 400

    fine_tune_response = client.post("/api/fine_tune/jobs", json={"datasetId": "unit", "baseModel": "act"})
    assert fine_tune_response.status_code == 200
    assert fine_tune_response.json()["data"]["job"]["status"] == "planned"
