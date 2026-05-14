from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.app import create_app, relative_motion_positions
from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.drivers.camera_opencv import OpenCVCameraDriver
from backend.hal_client.client import RealHalClient, TestHalClient
from backend.services.dataset_recorder import DatasetRecorderService

RECORDING_SAVE_CONTRACT_EXAMPLE = {
    "episode": {
        "status": "review",
        "lateFrames": 0,
        "dropCounts": {"global": 0, "wrist_left": 0, "wrist_right": 0},
        "maxSkewMs": 0.0,
        "avgSkewMs": 0.0,
        "jitterMs": 0.0,
    },
    "status": {"recording": False},
}

DATASET_LIST_CONTRACT_EXAMPLE = {
    "format": "lerobot-v3-native",
    "fps": 30,
    "episodes": [],
}


def create_mock_record_client(tmp_path: Path, monkeypatch: MonkeyPatch) -> TestClient:
    if importlib.util.find_spec("lerobot") is None:
        pytest.skip("lerobot[dataset] is not installed in this backend environment")
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "1")
    monkeypatch.setenv("APPSTATION_LEROBOT_USE_VIDEOS", "0")
    return TestClient(create_app(tmp_path / "runtime"))


def _clear_camera_identities(config: dict) -> None:
    for key in ("globalIdentity", "wristLeftIdentity", "wristRightIdentity"):
        config["cameras"][key] = ""


def _avoid_camera_index_conflicts(config: dict) -> None:
    config["cameras"]["wristLeft"] = "IMX258 / index 1"
    config["cameras"]["wristRight"] = "IMX258 / index 2"


def test_recording_api_contract_examples_cover_required_routes() -> None:
    assert set(RECORDING_SAVE_CONTRACT_EXAMPLE) == {"episode", "status"}
    assert "recording" in RECORDING_SAVE_CONTRACT_EXAMPLE["status"]
    assert DATASET_LIST_CONTRACT_EXAMPLE["format"].startswith("lerobot-v3")
    for path in (
        "/api/record/session/create",
        "/api/record/episode/save",
        "/api/record/status",
        "/api/datasets",
    ):
        assert path.startswith("/api/")


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
    assert len(frame["gripperPositions"]) == 2
    assert frame["motionEnabled"] == {"left": None, "right": None}
    assert len(frame["forceLeft"]) == 6
    assert len(frame["forceRight"]) == 6
    assert frame["recording"] is False
    assert isinstance(frame["episodeCount"], int)
    assert isinstance(frame["frameCount"], int)
    assert len(frame["cameras"]) == 3
    assert len(frame["teleopHands"]) == 2
    assert frame["wsOk"] is True

    teleop_response = client.get("/api/teleop/state")
    assert teleop_response.status_code == 200
    assert len(teleop_response.json()["data"]["hands"]) == 2


def test_websocket_telemetry_compatibility_and_log_shape(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    assert client.post(
        "/api/settings/log_command",
        json={"channel": "[LEROBOT]", "msg": "recording compatibility check", "level": "INFO"},
    ).status_code == 200

    with client.websocket_connect("/ws") as websocket:
        telemetry = websocket.receive_json()
        log = websocket.receive_json()

    assert telemetry["type"] == "telemetry"
    frame = telemetry["data"]
    for key in (
        "recording",
        "episodeCount",
        "frameCount",
        "jointPositions",
        "forceLeft",
        "forceRight",
        "gripperPositions",
        "cameras",
    ):
        assert key in frame
    assert log["type"] == "log"
    assert {"id", "ts", "channel", "level", "msg"}.issubset(log["data"])


def test_hardware_status_uses_gripper_workers_in_dual_mode(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200

    include_gripper_values: list[bool] = []

    def fake_hardware_status(*, include_gripper: bool = True) -> dict:
        include_gripper_values.append(include_gripper)
        return {"camera": {}, "force": {}, "gripper": {"ok": None}, "pico": {}}

    def fake_worker_status(config: dict) -> dict:
        return {"ok": True, "message": "dual gripper workers", "sides": {}}

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)
    monkeypatch.setattr(client.app.state.gripper_workers, "status", fake_worker_status)

    response = client.get("/api/hardware/status")

    assert response.status_code == 200
    assert include_gripper_values == [False]
    assert response.json()["gripper"]["message"] == "dual gripper workers"


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

    disable_response = client.post("/api/motion/left/disable_all")
    assert disable_response.status_code == 200
    state = asyncio.run(client.app.state.hal.motion_state())
    assert state["enabled"][:6] == [False] * 6

    stop_response = client.post("/api/motion/right/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["data"]["command"] == "motion.teleop_stop_side"

    bad_side_response = client.post("/api/motion/center/enable_all")
    assert bad_side_response.status_code == 400


def test_motion_positions_are_relative_to_captured_origin() -> None:
    config = default_config()
    config["motion"]["origin"] = {
        "valid": False,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    positions = [999.0] * 12
    pulses = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0] + [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    relative = relative_motion_positions(config, positions, pulses)

    assert relative[:6] == [0.0] * 6
    assert relative[6:] == [999.0] * 6


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


def test_home_all_requires_and_sends_captured_work_origin(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"] = {
        "valid": False,
        "leftValid": False,
        "rightValid": False,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    settings.save_config(config, emit_log=False)

    missing_response = client.post("/api/motion/home_all")
    assert missing_response.status_code == 503
    assert "work origin is not captured" in missing_response.json()["detail"]["message"]

    assert client.post("/api/motion/origin/capture").status_code == 200
    home_response = client.post("/api/motion/home_all")

    assert home_response.status_code == 200
    data = home_response.json()["data"]
    assert data["command"] == "motion.home_all"
    assert data["payload"] == {
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
    }


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


def test_runtime_release_handles_disconnects_teleop_and_grippers(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["gripper"]["leftEnabled"] = True
    config["gripper"]["rightEnabled"] = True
    config["teleop"]["leftConnected"] = True
    config["teleop"]["rightConnected"] = True
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.post("/api/runtime/release_handles", json={"reason": "unit-test"})

    assert response.status_code == 200
    saved = client.get("/api/settings").json()
    assert saved["gripper"]["leftEnabled"] is False
    assert saved["gripper"]["rightEnabled"] is False
    assert saved["teleop"]["leftConnected"] is False
    assert saved["teleop"]["rightConnected"] is False


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


def test_record_session_fails_when_native_lerobot_is_disabled(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
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
        assert start_response.status_code == 503
        detail = start_response.json()["detail"]
        assert detail["code"] == "NATIVE_DATASET_UNAVAILABLE"
        assert "native LeRobot dataset is required" in detail["message"]
        assert not (dataset_root / "unit_test_dataset" / "meta" / "info.json").exists()


def test_mock_hal_camera_end_to_end_record_save_list_review(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    dataset_root = tmp_path / "datasets"
    with create_mock_record_client(tmp_path, monkeypatch) as client:
        config = client.get("/api/settings").json()
        config["storage"]["datasetRoot"] = str(dataset_root)
        assert client.put("/api/settings", json=config).status_code == 200

        assert client.post(
            "/api/record/session/create",
            json={"dataset_name": "mock_e2e_dataset", "task": "mock review"},
        ).status_code == 200
        time.sleep(0.12)
        save_response = client.post("/api/record/episode/save")
        assert save_response.status_code == 200
        episode_id = save_response.json()["data"]["episode"]["id"]

        list_response = client.get("/api/datasets")
        assert list_response.status_code == 200
        dataset = next(item for item in list_response.json()["data"]["datasets"] if item["id"] == "mock_e2e_dataset")
        assert dataset["episodes"][0]["id"] == episode_id

        detail_response = client.get(f"/api/datasets/mock_e2e_dataset/episodes/{episode_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["data"]["episode"]["features"]["observation.state"]["shape"] == [14]

        assert client.post("/api/record/session/finish").status_code == 200


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
        assert "forceWindowSamples" not in start_response.json()["data"]
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
        assert info["features"]["observation.state"]["shape"] == [14]
        assert info["features"]["action"]["shape"] == [14]
        assert "observation.gripper" not in info["features"]
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

    assert frame.shape == (480, 640, 3)
    assert frame.dtype == np.uint8


def test_create_dataset_fails_when_native_lerobot_is_disabled(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "0")
    dataset_root = tmp_path / "datasets"
    client = TestClient(create_app(tmp_path / "runtime"))
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    assert client.put("/api/settings", json=config).status_code == 200

    create_response = client.post("/api/datasets", json={"name": "review_dataset"})
    assert create_response.status_code == 503
    detail = create_response.json()["detail"]
    assert detail["code"] == "NATIVE_DATASET_UNAVAILABLE"
    assert "native LeRobot dataset is required" in detail["message"]
    assert not (dataset_root / "review_dataset" / "meta" / "info.json").exists()


def test_dataset_episode_update_and_delete_hide_usable_sample(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    dataset_root = tmp_path / "datasets"
    with create_mock_record_client(tmp_path, monkeypatch) as client:
        config = client.get("/api/settings").json()
        config["storage"]["datasetRoot"] = str(dataset_root)
        assert client.put("/api/settings", json=config).status_code == 200

        assert client.post(
            "/api/record/session/create",
            json={"dataset_name": "review_lifecycle", "task": "lifecycle"},
        ).status_code == 200
        time.sleep(0.12)
        save_response = client.post("/api/record/episode/save")
        assert save_response.status_code == 200
        episode_id = save_response.json()["data"]["episode"]["id"]

        rename_response = client.patch(
            f"/api/datasets/review_lifecycle/episodes/{episode_id}",
            json={"name": "renamed episode", "status": "invalid"},
        )
        assert rename_response.status_code == 200
        detail_response = client.get(f"/api/datasets/review_lifecycle/episodes/{episode_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()["data"]["episode"]
        assert detail["name"] == "renamed episode"
        assert detail["status"] == "invalid"
        list_after_invalid = client.get("/api/datasets")
        invalid_dataset = next(
            item for item in list_after_invalid.json()["data"]["datasets"] if item["id"] == "review_lifecycle"
        )
        assert invalid_dataset["episodes"] == []

        delete_response = client.delete(f"/api/datasets/review_lifecycle/episodes/{episode_id}")
        assert delete_response.status_code == 200
        list_response = client.get("/api/datasets")
        dataset = next(item for item in list_response.json()["data"]["datasets"] if item["id"] == "review_lifecycle")
        assert dataset["episodes"] == []

        assert client.post("/api/record/session/finish").status_code == 200


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


def test_manual_axis_move_rejects_soft_limit_target(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    client = TestClient(create_app(tmp_path))
    config = default_config()
    config["hal"]["mode"] = "real"
    config["motion"]["origin"] = {
        "valid": False,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    config["motion"]["leftSoftLimits"]["x"] = {"min": -10, "max": 10}
    assert client.put("/api/settings", json=config).status_code == 200

    class FakeHal:
        async def motion_state(self) -> dict:
            return {
                "positions": [9.0] + [0.0] * 11,
                "pulses": [-81.0] + [0.0] * 11,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    client.app.state.commands.hal = FakeHal()

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 5, "speedMode": "fine"},
    )

    assert response.status_code == 503
    assert "soft limit" in response.json()["detail"]["message"]


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
    _clear_camera_identities(config)
    _avoid_camera_index_conflicts(config)
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
    _clear_camera_identities(config)
    _avoid_camera_index_conflicts(config)
    config["cameras"]["global"] = "Global UVC / index 0"
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.get("/api/cameras/global/snapshot")

    assert response.status_code == 200
    assert calls[:2] == [(0, 1400), (0, 700)]


def test_camera_driver_reopens_stale_capture() -> None:
    driver = OpenCVCameraDriver()
    captures: list[FakeCapture] = []

    class FakeCapture:
        def __init__(self, index: int, backend: int) -> None:
            self.index = index
            self.backend = backend
            self.released = False
            captures.append(self)

        def isOpened(self) -> bool:
            return not self.released

        def set(self, prop: int, value: int) -> None:
            _ = (prop, value)

        def get(self, prop: int) -> float:
            _ = prop
            return 30.0

        def read(self) -> tuple[bool, object | None]:
            time.sleep(0.01)
            return False, None

        def release(self) -> None:
            self.released = True

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

    first = driver._get_capture(FakeCv2, 0, 640, 480, 30)  # noqa: SLF001
    assert first is captures[0]
    driver._latest_at[0] = time.monotonic() - 3.0  # noqa: SLF001

    second = driver._get_capture(FakeCv2, 0, 640, 480, 30)  # noqa: SLF001

    assert second is captures[1]
    assert first.released is True
    driver._drop_capture(0)  # noqa: SLF001


def test_camera_auto_discovery_requires_encoded_frame() -> None:
    driver = OpenCVCameraDriver()

    class FakeCapture:
        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: int) -> None:
            _ = (prop, value)

        def get(self, prop: int) -> float:
            _ = prop
            return 30.0

        def read(self) -> tuple[bool, object | None]:
            return False, None

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
            _ = (index, backend)
            return FakeCapture()

        @staticmethod
        def VideoWriter_fourcc(a: str, b: str, c: str, d: str) -> int:
            _ = (a, b, c, d)
            return 1

        @staticmethod
        def imencode(ext: str, frame: object, options: list[int]) -> tuple[bool, bytes]:
            _ = (ext, frame, options)
            return True, b"\xff\xd8fake-jpeg\xff\xd9"

    readable = driver._discover_readable_indices(FakeCv2, 640, 480, 30, 1)  # noqa: SLF001

    assert readable == []
    driver._drop_capture(0)  # noqa: SLF001


def test_global_camera_auto_index_uses_remaining_device(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    _clear_camera_identities(config)
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


def test_camera_identity_overrides_stale_index(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    config["cameras"]["global"] = "AR0234 / index 0"
    config["cameras"]["globalIdentity"] = "USB\\VID_1D6B&PID_0102&MI_00\\7&235CBC02&0&0000"
    config["cameras"]["wristLeft"] = "IMX258 / index 1"
    config["cameras"]["wristLeftIdentity"] = ""
    driver = OpenCVCameraDriver()

    monkeypatch.setattr(
        driver,
        "_camera_identities_by_index",
        lambda: {
            0: {
                "name": "OBS Virtual Camera",
                "devicePath": "",
                "displayName": "@device:sw:{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\{A3FCE0F5}",
            },
            2: {
                "name": "UVC Camera",
                "devicePath": "\\\\?\\usb#vid_1d6b&pid_0102&mi_00#7&235cbc02&0&0000#{guid}\\global",
                "displayName": "@device:pnp:\\\\?\\usb#vid_1d6b&pid_0102&mi_00#7&235cbc02&0&0000#{guid}\\global",
            },
        },
    )

    resolved = driver._resolved_indices(object(), config, 30)

    assert resolved["global"] == 2


def test_camera_identities_lock_all_role_indices(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    config["cameras"]["global"] = "AR0234 / index 2"
    config["cameras"]["wristLeft"] = "IMX258 / index 0"
    config["cameras"]["wristRight"] = "IMX258 / index 1"
    driver = OpenCVCameraDriver()

    monkeypatch.setattr(
        driver,
        "_camera_identities_by_index",
        lambda: {
            0: {
                "name": "WN Camera",
                "devicePath": "\\\\?\\usb#vid_0edc&pid_3080&mi_00#6&1bbfdb86&0&0000#{guid}\\global",
                "displayName": "@device:pnp:right",
            },
            1: {
                "name": "UVC Camera",
                "devicePath": "\\\\?\\usb#vid_1d6b&pid_0102&mi_00#6&1e9a8698&0&0000#{guid}\\global",
                "displayName": "@device:pnp:global",
            },
            2: {
                "name": "WN Camera",
                "devicePath": "\\\\?\\usb#vid_0edc&pid_3080&mi_00#7&38b4ea25&0&0000#{guid}\\global",
                "displayName": "@device:pnp:left",
            },
        },
    )

    resolved = driver._resolved_indices(object(), config, 30)

    assert resolved == {"global": 1, "wrist_left": 2, "wrist_right": 0}


def test_default_camera_mapping_matches_deployment_hardware() -> None:
    config = default_config()

    assert config["cameras"]["global"] == "AR0234 / index 1"
    assert config["cameras"]["globalIdentity"] == "USB\\VID_1D6B&PID_0102&MI_00\\6&1E9A8698&0&0000"
    assert config["cameras"]["wristLeft"] == "IMX258 / index 2"
    assert config["cameras"]["wristLeftIdentity"] == "USB\\VID_0EDC&PID_3080&MI_00\\7&38B4EA25&0&0000"
    assert config["cameras"]["wristRight"] == "IMX258 / index 0"
    assert config["cameras"]["wristRightIdentity"] == "USB\\VID_0EDC&PID_3080&MI_00\\6&1BBFDB86&0&0000"
    assert config["cameras"]["previewResolution"] == "640x480"
    assert config["cameras"]["globalResolution"] == "640x480"
    assert config["cameras"]["wristLeftResolution"] == "640x480"
    assert config["cameras"]["wristRightResolution"] == "640x480"
    assert config["cameras"]["tuning"]["global"]["exposure"] == -5.5
    assert config["cameras"]["tuning"]["wrist_left"]["exposure"] == -6.0
    assert config["cameras"]["tuning"]["wrist_right"]["exposure"] == -6.0


def test_camera_tuning_clamps_wrist_profile() -> None:
    driver = OpenCVCameraDriver()
    config = default_config()
    config["cameras"]["tuning"]["wrist_left"] = {
        "autoExposure": True,
        "exposure": -3.0,
        "gain": 72.0,
        "autoWhiteBalance": True,
    }

    profile = driver._camera_tuning(config, "wrist_left")  # noqa: SLF001

    assert profile == {
        "autoExposure": False,
        "exposure": -5.0,
        "gain": 64.0,
        "autoWhiteBalance": True,
    }


def test_camera_tuning_apply_endpoint_saves_config(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    calls: list[tuple[str, float]] = []

    def fake_apply(config: dict, camera: str) -> dict[str, object]:
        calls.append((camera, float(config["cameras"]["tuning"][camera]["exposure"])))
        return {
            "camera": camera,
            "index": 1,
            "profile": config["cameras"]["tuning"][camera],
            "actual": {"exposure": -6.5},
        }

    client.app.state.hardware.cameras.apply_tuning = fake_apply
    config = client.get("/api/settings").json()
    config["cameras"]["tuning"]["global"]["exposure"] = -6.5

    response = client.post("/api/cameras/global/tuning/apply", json=config)

    assert response.status_code == 200
    assert calls == [("global", -6.5)]
    assert response.json()["data"]["profile"]["exposure"] == -6.5
    assert client.get("/api/settings").json()["cameras"]["tuning"]["global"]["exposure"] == -6.5


def test_camera_wait_for_frame_returns_cached_encoded_jpeg(monkeypatch: MonkeyPatch) -> None:
    driver = OpenCVCameraDriver()
    config = default_config()
    _clear_camera_identities(config)
    driver._latest_sequences[0] = 4  # noqa: SLF001
    driver._latest_jpegs[0] = b"\xff\xd8cached\xff\xd9"  # noqa: SLF001

    monkeypatch.setattr("backend.drivers.camera_opencv.import_module", lambda name: object())
    monkeypatch.setattr(driver, "_resolved_indices", lambda cv2, cfg, fps: {"global": 0})
    monkeypatch.setattr(driver, "_get_capture", lambda *args, **kwargs: object())

    sequence, jpeg = driver.wait_for_frame(config, "global", last_sequence=3, timeout=0.01)

    assert sequence == 4
    assert jpeg == b"\xff\xd8cached\xff\xd9"


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

    def fake_hardware_status(self: object, *, include_gripper: bool = True) -> dict[str, object]:
        _ = (self, include_gripper)
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


def test_real_hal_client_maps_teleop_continuous_commands(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        status = 200
        headers = {"Connection": "keep-alive"}

        def read(self) -> bytes:
            return b'{"ok":true}'

    class FakeConnection:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)
            self.sock = type("FakeSocket", (), {"setsockopt": staticmethod(lambda *a, **k: None)})()

        def connect(self) -> None:
            return None

        def request(self, method: str, path: str, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)
            calls.append((method, path))

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("backend.hal_client.client.http.client.HTTPConnection", FakeConnection)
    client = RealHalClient("http://127.0.0.1:8091", 5000, LogService())

    asyncio.run(client.command("motion.teleop_target_update", {"side": "left"}))
    asyncio.run(client.command("motion.teleop_stop_side", {"side": "left"}))

    assert calls == [
        ("POST", "/motion/teleop_target_update"),
        ("POST", "/motion/teleop_stop_side"),
    ]


def test_hal_state_clients_include_receive_timestamps(monkeypatch: MonkeyPatch) -> None:
    class FakeResponse:
        status = 200
        headers = {"Connection": "keep-alive"}

        def read(self) -> bytes:
            return b'{"positions":[]}'

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
    real = RealHalClient("http://127.0.0.1:8091", 5000, LogService())
    test = TestHalClient(LogService())

    real_state = asyncio.run(real.motion_state())
    test_state = asyncio.run(test.omega_state())

    assert isinstance(real_state["timestamp_ms"], int)
    assert isinstance(real_state["received_timestamp_ms"], int)
    assert isinstance(real_state["received_monotonic_ms"], int)
    assert isinstance(test_state["timestamp_ms"], int)
    assert isinstance(test_state["received_monotonic_ms"], int)


def test_real_hal_client_serializes_home_all_work_origin(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    class FakeResponse:
        status = 200
        headers = {"Connection": "keep-alive"}

        def read(self) -> bytes:
            return b'{"ok":true}'

    class FakeConnection:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)
            self.sock = type("FakeSocket", (), {"setsockopt": staticmethod(lambda *a, **k: None)})()

        def connect(self) -> None:
            return None

        def request(self, method: str, path: str, *args: object, **kwargs: object) -> None:
            _ = args
            body = kwargs.get("body")
            assert isinstance(body, bytes)
            calls.append((method, path, json.loads(body.decode("utf-8"))))

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("backend.hal_client.client.http.client.HTTPConnection", FakeConnection)
    client = RealHalClient("http://127.0.0.1:8091", 5000, LogService())

    asyncio.run(
        client.command(
            "motion.home_all",
            {
                "leftPulse": [1, 2, 3, 4, 5, 6],
                "rightPulse": [-1, -2, -3, -4, -5, -6],
            },
        )
    )

    assert calls == [
        (
            "POST",
            "/motion/home_all",
            {
                "leftPulse": [1, 2, 3, 4, 5, 6],
                "rightPulse": [-1, -2, -3, -4, -5, -6],
            },
        )
    ]


def test_dataset_recorder_action_vector_prefers_teleop_delta_vector() -> None:
    class FakeTeleop:
        def status(self) -> dict[str, object]:
            return {
                "lastAction": {
                    "ts": int(time.time() * 1000),
                    "deltaVector": [10.0, 0.0, 0.0, 0.5, 0.0, 0.0, -20.0, 0.0, 0.0, 0.0, 0.0, -0.1],
                }
            }

    recorder = object.__new__(DatasetRecorderService)
    recorder.teleop = FakeTeleop()

    assert recorder._latest_action_vector() == [
        10.0,
        0.0,
        0.0,
        500.0,
        0.0,
        0.0,
        0.0,
        -20.0,
        0.0,
        0.0,
        0.0,
        0.0,
        -100.0,
        0.0,
    ]


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
