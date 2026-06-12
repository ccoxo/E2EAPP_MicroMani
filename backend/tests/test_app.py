from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event, Thread
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.app import create_app, relative_motion_positions
from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.core.motion_limits import effective_limits_ui, side_home_reference_ui
from backend.core.schemas import GripperCommandRequest
from backend.drivers.camera_opencv import OpenCVCameraDriver
from backend.drivers.pico_adb import PicoResult
from backend.hal_client.client import HalHealth, RealHalClient, TestHalClient
from backend.services.dataset_recorder import DatasetRecorderService, DatasetSaveError

REPO_ROOT = Path(__file__).resolve().parents[2]

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


def _app_state(client: TestClient) -> Any:
    return cast(Any, client.app).state


def _write_dataset_fixture(dataset_root: Path, dataset_id: str = "unit_dataset") -> Path:
    dataset_dir = dataset_root / dataset_id
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(
        json.dumps(
            {
                "name": "Unit Dataset",
                "format": "lerobot-v3-native",
                "fps": 30,
                "createdAt": 1000,
                "updatedAt": 2000,
            }
        ),
        encoding="utf-8",
    )
    episode = {
        "id": "episode_000001",
        "name": "Episode 1",
        "task": "assembly",
        "status": "review",
        "frames": 12,
        "fps": 30,
        "durationS": 0.4,
        "createdAt": 1500,
        "native": True,
    }
    (dataset_dir / "meta" / "episodes.jsonl").write_text(json.dumps(episode) + "\n", encoding="utf-8")
    return dataset_dir


def test_backend_app_import_does_not_create_runtime_services(tmp_path: Path) -> None:
    import subprocess
    import sys

    env = os.environ.copy()
    env["APPSTATION_HAL_MODE"] = "test"
    env["APPSTATION_RUNTIME_DIR"] = str(tmp_path / "runtime")

    result = subprocess.run(
        [sys.executable, "-c", "import backend.app"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "runtime" / "logs").glob("*.log"))


def _wide_motion_soft_limits() -> dict[str, dict[str, float]]:
    return {
        "x": {"min": -1_000_000.0, "max": 1_000_000.0},
        "y": {"min": -1_000_000.0, "max": 1_000_000.0},
        "z": {"min": -1_000_000.0, "max": 1_000_000.0},
        "roll": {"min": -360_000.0, "max": 360_000.0},
        "pitch": {"min": -360_000.0, "max": 360_000.0},
        "yaw": {"min": -360_000.0, "max": 360_000.0},
    }


def _disable_rotation_work_limits(config: dict[str, Any]) -> None:
    config["motion"]["rotationWorkLimits"]["enabled"] = False


def _set_home_reference_to_origin(config: dict[str, Any]) -> None:
    origin = config["motion"]["origin"]
    left_valid = bool(origin.get("leftValid", origin.get("valid", False)))
    right_valid = bool(origin.get("rightValid", origin.get("valid", False)))
    config["motion"]["homeReference"] = {
        "valid": bool(left_valid and right_valid),
        "leftValid": left_valid,
        "rightValid": right_valid,
        "leftPulse": list(origin.get("leftPulse", [0.0] * 6)),
        "rightPulse": list(origin.get("rightPulse", [0.0] * 6)),
        "updatedAt": int(origin.get("updatedAt", 0)),
    }


def _set_zero_work_origin_offset(config: dict[str, Any]) -> None:
    origin = config["motion"]["origin"]
    left_valid = bool(origin.get("leftValid", origin.get("valid", False)))
    right_valid = bool(origin.get("rightValid", origin.get("valid", False)))
    config["motion"]["workOriginOffset"] = {
        "valid": bool(left_valid and right_valid),
        "leftValid": left_valid,
        "rightValid": right_valid,
        "leftPulseDelta": [0.0] * 6,
        "rightPulseDelta": [0.0] * 6,
        "updatedAt": int(origin.get("updatedAt", 0)),
    }


def _assert_rotation_window_from_home_reference(config: dict[str, Any], side: str) -> None:
    home_reference = side_home_reference_ui(config, side)
    assert home_reference is not None
    limits = effective_limits_ui(config, side)
    expected_span = {
        "roll": (-95.0, 5.0) if side == "right" else (-5.0, 95.0),
        "pitch": (-30.0, 30.0),
        "yaw": (-7.0, 7.0),
    }
    for axis_index, axis in enumerate(("roll", "pitch", "yaw"), start=3):
        assert limits[axis_index].min - home_reference[axis_index] == pytest.approx(
            expected_span[axis][0],
            abs=1e-6,
        )
        assert limits[axis_index].max - home_reference[axis_index] == pytest.approx(
            expected_span[axis][1],
            abs=1e-6,
        )


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


def test_settings_save_and_apply_run_config_methods_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    calls: list[str] = []

    def assert_off_event_loop(method_name: str) -> None:
        calls.append(method_name)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError(f"{method_name} ran on the event loop")

    def fake_save_config(next_config: dict[str, Any], emit_log: bool = True) -> dict[str, Any]:
        _ = emit_log
        assert_off_event_loop("save_config")
        return next_config

    def fake_apply_config(next_config: dict[str, Any] | None = None) -> dict[str, Any]:
        assert_off_event_loop("apply_config")
        return next_config or config

    app_state = _app_state(client)
    monkeypatch.setattr(app_state.settings, "save_config", fake_save_config)
    monkeypatch.setattr(app_state.settings, "apply_config", fake_apply_config)

    put_response = client.put("/api/settings", json=config)
    apply_response = client.post("/api/settings/apply", json=config)

    assert put_response.status_code == 200
    assert apply_response.status_code == 200
    assert calls == ["save_config", "apply_config"]


def test_settings_snapshot_endpoints_run_snapshot_methods_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    snapshot = {"id": "snap-1", "name": "Unit", "scope": "all", "config": config, "createdAt": 1}
    calls: list[str] = []

    def assert_off_event_loop(method_name: str) -> None:
        calls.append(method_name)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError(f"{method_name} ran on the event loop")

    def fake_list_snapshots(scope: str | None = None) -> list[dict[str, Any]]:
        _ = scope
        assert_off_event_loop("list_snapshots")
        return [snapshot]

    def fake_create_snapshot(request: object) -> dict[str, Any]:
        _ = request
        assert_off_event_loop("create_snapshot")
        return snapshot

    def fake_apply_snapshot(snapshot_id: str) -> dict[str, Any]:
        _ = snapshot_id
        assert_off_event_loop("apply_snapshot")
        return config

    def fake_delete_snapshot(snapshot_id: str) -> None:
        _ = snapshot_id
        assert_off_event_loop("delete_snapshot")

    app_state = _app_state(client)
    monkeypatch.setattr(app_state.settings, "list_snapshots", fake_list_snapshots)
    monkeypatch.setattr(app_state.settings, "create_snapshot", fake_create_snapshot)
    monkeypatch.setattr(app_state.settings, "apply_snapshot", fake_apply_snapshot)
    monkeypatch.setattr(app_state.settings, "delete_snapshot", fake_delete_snapshot)

    list_response = client.get("/api/settings/snapshots")
    create_response = client.post("/api/settings/snapshots", json={"scope": "all", "name": "Unit"})
    apply_response = client.post("/api/settings/snapshots/snap-1/apply")
    delete_response = client.delete("/api/settings/snapshots/snap-1")

    assert list_response.status_code == 200
    assert create_response.status_code == 200
    assert apply_response.status_code == 200
    assert delete_response.status_code == 200
    assert calls == [
        "list_snapshots",
        "create_snapshot",
        "list_snapshots",
        "apply_snapshot",
        "list_snapshots",
        "delete_snapshot",
        "list_snapshots",
    ]


def test_dataset_hub_toggle_updates_upload_switch(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.patch("/api/datasets/hub", json={"pushToHub": True})

    assert response.status_code == 200
    assert response.json()["data"]["pushToHub"] is True
    assert client.get("/api/settings").json()["storage"]["pushToHub"] is True


@pytest.mark.parametrize(
    ("method_name", "http_method", "path", "payload"),
    [
        ("create_dataset", "post", "/api/datasets", {"name": "unit"}),
        ("update_hub_settings", "patch", "/api/datasets/hub", {"pushToHub": True}),
        ("update_dataset", "patch", "/api/datasets/unit_dataset", {"name": "Unit"}),
        ("delete_dataset", "delete", "/api/datasets/unit_dataset", None),
        ("save_review", "post", "/api/datasets/unit_dataset/review/save", None),
        ("export_dataset", "post", "/api/datasets/unit_dataset/export", None),
        ("dataset_stats", "get", "/api/datasets/unit_dataset/stats", None),
        ("split_dataset", "post", "/api/datasets/unit_dataset/split", {"ratios": {"train": 1.0}}),
        ("clean_dataset", "post", "/api/datasets/unit_dataset/clean", {"apply": False}),
        ("push_dataset", "post", "/api/datasets/unit_dataset/push", {"dryRun": True}),
        ("update_episode", "patch", "/api/datasets/unit_dataset/episodes/episode_000001", {"status": "review"}),
        ("delete_episode", "delete", "/api/datasets/unit_dataset/episodes/episode_000001", None),
    ],
)
def test_dataset_management_endpoints_run_recorder_methods_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    method_name: str,
    http_method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    client = TestClient(create_app(tmp_path))
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_recorder_method(*args: Any, **kwargs: Any) -> dict[str, object]:
        calls.append((args, kwargs))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return {"method": method_name}
        raise AssertionError(f"{method_name} ran on the event loop")

    monkeypatch.setattr(client.app.state.recorder, method_name, fake_recorder_method)
    request = getattr(client, http_method)
    response = request(path, json=payload) if payload is not None else request(path)

    assert response.status_code == 200
    assert response.json()["data"]["method"] == method_name
    assert len(calls) == 1


def test_dataset_file_endpoint_resolves_path_off_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path))
    target = tmp_path / "download.txt"
    target.write_text("ok", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    def fake_resolve_file(dataset_id: str, path: str) -> Path:
        calls.append((dataset_id, path))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return target
        raise AssertionError("resolve_file ran on the event loop")

    monkeypatch.setattr(client.app.state.recorder, "resolve_file", fake_resolve_file)

    response = client.get("/api/datasets/unit_dataset/file", params={"path": "download.txt"})

    assert response.status_code == 200
    assert response.text == "ok"
    assert calls == [("unit_dataset", "download.txt")]


def test_dataset_push_uses_request_token_without_persisting(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    dataset_root = tmp_path / "datasets"
    dataset_dir = dataset_root / "unit_dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v3.0"}), encoding="utf-8")
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    config["storage"]["pushToHub"] = True
    assert client.put("/api/settings", json=config).status_code == 200
    calls: list[dict[str, Any]] = []

    def fake_start_job(dataset_id: str, repo_id: str, dataset_dir: Path, *, token: str, private: bool) -> str:
        calls.append(
            {
                "datasetId": dataset_id,
                "repoId": repo_id,
                "datasetDir": str(dataset_dir),
                "token": token,
                "private": private,
            }
        )
        return "job-1"

    monkeypatch.setattr(client.app.state.recorder, "_start_hub_push_job", fake_start_job)

    response = client.post(
        "/api/datasets/unit_dataset/push",
        json={"repoId": "org/unit_dataset", "token": "hf_transient_secret", "private": True, "dryRun": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["queued"] is True
    assert payload["data"]["jobId"] == "job-1"
    assert payload["data"]["pushed"] is False
    assert payload["data"]["localPath"] == str(dataset_dir)
    assert "hf_transient_secret" not in json.dumps(payload)
    assert calls == [
        {
            "datasetId": "unit_dataset",
            "repoId": "org/unit_dataset",
            "datasetDir": str(dataset_dir),
            "token": "hf_transient_secret",
            "private": True,
        }
    ]
    assert "hf_transient_secret" not in (tmp_path / "runtime" / "config.json").read_text(encoding="utf-8")


def test_dataset_push_dry_run_returns_local_path(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    dataset_root = tmp_path / "datasets"
    dataset_dir = dataset_root / "unit_dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v3.0"}), encoding="utf-8")
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    config["storage"]["pushToHub"] = True
    assert client.put("/api/settings", json=config).status_code == 200

    response = client.post(
        "/api/datasets/unit_dataset/push",
        json={"repoId": "org/unit_dataset", "private": True, "dryRun": True},
    )

    assert response.status_code == 200
    assert response.json()["data"]["localPath"] == str(dataset_dir)


def test_dataset_push_uses_request_local_path_override(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    dataset_root = tmp_path / "datasets"
    dataset_dir = dataset_root / "unit_dataset"
    override_dir = tmp_path / "override_dataset"
    (dataset_dir / "meta").mkdir(parents=True)
    (dataset_dir / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v3.0"}), encoding="utf-8")
    (override_dir / "meta").mkdir(parents=True)
    (override_dir / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v3.0"}), encoding="utf-8")
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    config["storage"]["pushToHub"] = True
    assert client.put("/api/settings", json=config).status_code == 200
    calls: list[dict[str, Any]] = []

    def fake_start_job(dataset_id: str, repo_id: str, dataset_dir: Path, *, token: str, private: bool) -> str:
        calls.append({"datasetId": dataset_id, "repoId": repo_id, "datasetDir": str(dataset_dir), "private": private})
        return "job-2"

    monkeypatch.setattr(client.app.state.recorder, "_start_hub_push_job", fake_start_job)

    response = client.post(
        "/api/datasets/unit_dataset/push",
        json={
            "repoId": "org/unit_dataset",
            "localPath": str(override_dir),
            "private": True,
            "dryRun": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["queued"] is True
    assert data["jobId"] == "job-2"
    assert data["localPath"] == str(override_dir)
    assert calls == [
        {
            "datasetId": "unit_dataset",
            "repoId": "org/unit_dataset",
            "datasetDir": str(override_dir),
            "private": True,
        }
    ]


def test_dataset_push_script_failure_is_recorded_on_job(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    recorder = client.app.state.recorder
    dataset_dir = tmp_path / "datasets" / "unit_dataset"
    dataset_dir.mkdir(parents=True)
    calls: list[dict[str, Any]] = []

    class FakeCompletedProcess:
        returncode = 7
        stdout = "partial stdout"
        stderr = "hf upload rejected"

    def fake_run(command: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append({"command": command, "envToken": kwargs["env"].get("HF_TOKEN")})
        return FakeCompletedProcess()

    monkeypatch.setattr("backend.services.dataset_recorder.subprocess.run", fake_run)

    recorder._hub_push_jobs["job-3"] = {"status": "queued"}
    recorder._run_hub_push_script(
        "job-3",
        "unit_dataset",
        "org/unit_dataset",
        dataset_dir,
        token="hf_transient_secret",
        private=True,
    )

    assert calls[0]["envToken"] == "hf_transient_secret"
    assert "hf_transient_secret" not in " ".join(calls[0]["command"])
    assert "--private" in calls[0]["command"]
    assert recorder._hub_push_jobs["job-3"]["status"] == "failed"
    assert "hf upload rejected" in recorder._hub_push_jobs["job-3"]["stderr"]


def test_dataset_list_returns_metadata_without_loading_episode_samples(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    dataset_root = tmp_path / "datasets"
    _write_dataset_fixture(dataset_root)
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    assert client.put("/api/settings", json=config).status_code == 200

    def fail_if_samples_are_loaded(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("dataset list must not load episode samples")

    monkeypatch.setattr(client.app.state.recorder, "_episode_samples", fail_if_samples_are_loaded)

    response = client.get("/api/datasets")

    assert response.status_code == 200
    episode = response.json()["data"]["datasets"][0]["episodes"][0]
    assert episode["id"] == "episode_000001"
    assert episode["samples"] == []


def test_dataset_episode_detail_loads_samples_on_demand(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    dataset_root = tmp_path / "datasets"
    _write_dataset_fixture(dataset_root)
    config = client.get("/api/settings").json()
    config["storage"]["datasetRoot"] = str(dataset_root)
    assert client.put("/api/settings", json=config).status_code == 200
    calls: list[str] = []

    def fake_samples(_dataset_dir: Path, dataset_id: str, episode: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(f"{dataset_id}:{episode['id']}")
        return [{"frame": 0}]

    monkeypatch.setattr(client.app.state.recorder, "_episode_samples", fake_samples)

    response = client.get("/api/datasets/unit_dataset/episodes/episode_000001")

    assert response.status_code == 200
    episode = response.json()["data"]["episode"]
    assert episode["samples"] == [{"frame": 0}]
    assert calls == ["unit_dataset:episode_000001"]


def test_dataset_heavy_read_routes_use_worker_thread(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path / "runtime"))
    calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((func.__name__, args))
        return func(*args, **kwargs)

    def list_datasets() -> list[dict[str, Any]]:
        return []

    def episode_detail(dataset_id: str, episode_id: str) -> dict[str, Any]:
        return {"id": episode_id, "datasetId": dataset_id}

    def resolve_frame_image(dataset_id: str, episode_id: str, camera: str, frame: int) -> bytes:
        return f"{dataset_id}:{episode_id}:{camera}:{frame}".encode()

    def record_status() -> dict[str, Any]:
        return {"active": False, "recording": False}

    monkeypatch.setattr(client.app.state.recorder, "list_datasets", list_datasets)
    monkeypatch.setattr(client.app.state.recorder, "episode_detail", episode_detail)
    monkeypatch.setattr(client.app.state.recorder, "resolve_frame_image", resolve_frame_image)
    monkeypatch.setattr(client.app.state.recorder, "status", record_status)
    monkeypatch.setattr("backend.app.asyncio.to_thread", fake_to_thread)

    assert client.get("/api/record/status").status_code == 200
    assert client.get("/api/datasets").status_code == 200
    assert client.get("/api/datasets/unit_dataset/episodes/episode_000001").status_code == 200
    image_response = client.get(
        "/api/datasets/unit_dataset/frame_image",
        params={"episode_id": "episode_000001", "camera": "global", "frame": 3},
    )

    assert image_response.status_code == 200
    assert [name for name, _ in calls] == ["record_status", "list_datasets", "episode_detail", "resolve_frame_image"]
    assert calls[2][1] == ("unit_dataset", "episode_000001")
    assert calls[3][1] == ("unit_dataset", "episode_000001", "global", 3)


def test_device_routes_read_config_on_worker_thread(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path / "runtime"))
    calls: list[str] = []

    async def fake_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr("backend.app.asyncio.to_thread", fake_to_thread)

    assert client.post("/api/pico/status/check").status_code == 200
    assert calls[:2] == ["get_config", "status"]


def test_teleop_mapping_status_reads_config_off_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path / "runtime"), raise_server_exceptions=False)
    app_state = _app_state(client)
    original_get_config = app_state.settings.get_config
    calls: list[str] = []

    def guarded_get_config() -> dict[str, Any]:
        calls.append("get_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_get_config()
        raise AssertionError("teleop mapping status read config on the event loop")

    monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)

    response = client.get("/api/teleop/mapping/status")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == ["get_config"]


def test_startup_emits_session_and_axis_config_logs(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    entries = client.app.state.logs.list_entries()
    messages = [entry.msg for entry in entries]

    session = next(message for message in messages if "event=session_start" in message)
    assert "component=BACKEND" in session
    assert "logSchemaVersion=e2e-diagnostics-v1" in session
    assert "configPath=" in session
    assert "configHash=" in session

    axis = next(
        message for message in messages if "event=axis_config_snapshot" in message and "axisName=Yaw" in message
    )
    assert "component=MOTION" in axis
    assert "side=left" in axis or "side=right" in axis
    assert "physicalAxis=" in axis
    assert "pulsePerUnit=" in axis
    assert "softLimitMin=" in axis
    assert "softLimitMax=" in axis

    right_yaw = next(
        message
        for message in messages
        if "event=axis_config_snapshot" in message and "side=right" in message and "axisName=Yaw" in message
    )
    assert "pulsePerUnit=333.333" in right_yaw
    assert "sourceSide=left" in right_yaw
    assert "impulseCoeff=-333.333" in right_yaw
    assert "targetImpulseCoeff=3333.33" in right_yaw


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


def test_websocket_telemetry_frame_runs_off_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path / "runtime"))
    app_state = _app_state(client)
    original_next_frame = app_state.telemetry.next_frame
    contexts: list[str] = []

    def guarded_next_frame(*args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            contexts.append("worker")
        else:
            contexts.append("event_loop")
        return original_next_frame(*args, **kwargs)

    monkeypatch.setattr(app_state.telemetry, "next_frame", guarded_next_frame)

    with client.websocket_connect("/ws") as websocket:
        message = websocket.receive_json()

    assert message["type"] == "telemetry"
    assert contexts[:1] == ["worker"]


def test_teleop_force_controls_forward_to_hal(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    gravity_response = client.post("/api/teleop/right/gravity_compensation", json={"enabled": False})
    assert gravity_response.status_code == 200
    assert gravity_response.json()["data"]["enabled"] is False
    config = client.get("/api/settings").json()
    assert config["teleop"]["rightGravityCompensation"] is False
    assert config["teleop"]["rightForceFeedback"] is False

    zero_response = client.post("/api/teleop/right/zero_force_feedback")
    assert zero_response.status_code == 200
    assert zero_response.json()["data"]["openId"] == 1


def test_teleop_gravity_compensation_saves_config_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    calls: list[str] = []

    def fake_save_config(config: dict[str, Any], emit_log: bool = True) -> dict[str, Any]:
        _ = emit_log
        calls.append("save_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return config
        raise AssertionError("teleop gravity save_config ran on the event loop")

    app_state = _app_state(client)
    monkeypatch.setattr(app_state.settings, "save_config", fake_save_config)

    response = client.post("/api/teleop/right/gravity_compensation", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["data"]["enabled"] is False
    assert calls == ["save_config"]


def test_motion_and_gripper_command_routes_use_config_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    app_state = _app_state(client)
    config = client.get("/api/settings").json()
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    _disable_rotation_work_limits(config)
    assert client.put("/api/settings", json=config).status_code == 200
    original_get_config = app_state.settings.get_config
    original_save_config = app_state.settings.save_config
    calls: list[str] = []

    def guarded_get_config() -> dict[str, Any]:
        calls.append("get_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_get_config()
        raise AssertionError("command route read config on the event loop")

    def guarded_save_config(config: dict[str, Any], emit_log: bool = True, **kwargs: Any) -> dict[str, Any]:
        calls.append("save_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_save_config(config, emit_log=emit_log, **kwargs)
        raise AssertionError("command route wrote config on the event loop")

    monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)
    monkeypatch.setattr(app_state.settings, "save_config", guarded_save_config)

    move_response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 100, "speedMode": "fine"},
    )
    gripper_response = client.post(
        "/api/gripper/left/command",
        json={"side": "left", "command": "enable"},
    )
    tare_response = client.post("/api/sensors/tare")

    assert move_response.status_code == 200
    assert gripper_response.status_code == 200
    assert tare_response.status_code == 200
    assert "get_config" in calls
    assert "save_config" in calls


def test_motion_origin_routes_use_config_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    app_state = _app_state(client)
    config = client.get("/api/settings").json()
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    _disable_rotation_work_limits(config)
    assert client.put("/api/settings", json=config).status_code == 200
    original_get_config = app_state.settings.get_config
    original_save_config = app_state.settings.save_config
    calls: list[str] = []

    def guarded_get_config() -> dict[str, Any]:
        calls.append("get_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_get_config()
        raise AssertionError("motion origin route read config on the event loop")

    def guarded_save_config(config: dict[str, Any], emit_log: bool = True, **kwargs: Any) -> dict[str, Any]:
        calls.append("save_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_save_config(config, emit_log=emit_log, **kwargs)
        raise AssertionError("motion origin route wrote config on the event loop")

    monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)
    monkeypatch.setattr(app_state.settings, "save_config", guarded_save_config)

    status_response = client.get("/api/motion/origin")
    clear_response = client.post("/api/motion/origin/clear")

    assert status_response.status_code == 200
    assert clear_response.status_code == 200
    assert "get_config" in calls
    assert "save_config" in calls


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


def test_websocket_reports_card0_dmc5c10_enabled_feedback_as_unknown(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=False,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 6 + [False] * 6,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    with TestClient(create_app(tmp_path)) as client:
        client.app.state.hardware.cameras.probe = lambda _config: type("Probe", (), {"cameras": []})()

        with client.websocket_connect("/ws") as websocket:
            frame = websocket.receive_json()["data"]

    assert frame["motionAxisEnabled"]["right"] == [None, None, None, None, None, None]
    assert frame["motionEnabled"]["right"] is None


def test_startup_home_can_be_skipped_by_environment(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    def write_startup_home_config(runtime_dir: Path) -> None:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        config = default_config()
        config["motion"]["homeOnStartup"]["enabled"] = True
        (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    runtime_without_skip = tmp_path / "without-skip"
    write_startup_home_config(runtime_without_skip)
    fake_without_skip = FakeHal()
    monkeypatch.delenv("APPSTATION_SKIP_STARTUP_HOME", raising=False)
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_without_skip)
    with TestClient(create_app(runtime_without_skip)):
        pass
    assert any(name == "motion.home_all" for name, _payload in fake_without_skip.commands)

    runtime_with_skip = tmp_path / "with-skip"
    write_startup_home_config(runtime_with_skip)
    fake_with_skip = FakeHal()
    monkeypatch.setenv("APPSTATION_SKIP_STARTUP_HOME", "true")
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_with_skip)
    with TestClient(create_app(runtime_with_skip)):
        pass

    assert not any(name == "motion.home_all" for name, _payload in fake_with_skip.commands)


def test_hardware_status_uses_gripper_workers_in_dual_mode(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "python_mapper"
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


def test_hardware_status_reads_gripper_worker_status_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200

    def fake_hardware_status(*, include_gripper: bool = True) -> dict[str, object]:
        _ = include_gripper
        return {"camera": {}, "force": {}, "gripper": {"ok": None}, "pico": {}}

    def fake_worker_status(config: dict[str, Any]) -> dict[str, object]:
        _ = config
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return {"ok": True, "message": "dual gripper workers", "sides": {}}
        raise AssertionError("gripper worker status ran on the event loop")

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)
    monkeypatch.setattr(client.app.state.gripper_workers, "status", fake_worker_status)

    response = client.get("/api/hardware/status")

    assert response.status_code == 200
    assert response.json()["gripper"]["message"] == "dual gripper workers"


def test_health_reads_gripper_worker_status_off_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200

    def fake_hardware_status(*, include_gripper: bool = True) -> dict[str, object]:
        _ = include_gripper
        return {"camera": {}, "force": {}, "gripper": {"ok": None}, "pico": {}}

    def fake_worker_status(config: dict[str, Any]) -> dict[str, object]:
        _ = config
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return {"ok": True, "message": "dual gripper workers", "sides": {}}
        raise AssertionError("gripper worker status ran on the event loop")

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)
    monkeypatch.setattr(client.app.state.gripper_workers, "status", fake_worker_status)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["hardware"]["gripper"]["message"] == "dual gripper workers"


def test_hardware_status_uses_gripper_workers_in_native_mode_when_configured(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200

    include_gripper_values: list[bool] = []
    worker_status_calls: list[str] = []

    def fake_hardware_status(*, include_gripper: bool = True) -> dict:
        include_gripper_values.append(include_gripper)
        return {"camera": {}, "force": {}, "gripper": {"ok": None}, "pico": {}}

    def fake_worker_status(config: dict) -> dict:
        worker_status_calls.append(config["teleop"]["engine"])
        return {"ok": True, "message": "dual gripper workers", "sides": {}}

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)
    monkeypatch.setattr(client.app.state.gripper_workers, "status", fake_worker_status)

    response = client.get("/api/hardware/status")

    assert response.status_code == 200
    assert include_gripper_values == [False]
    assert worker_status_calls == ["hal_native"]
    assert response.json()["gripper"]["message"] == "dual gripper workers"


def test_hardware_status_native_mode_does_not_probe_python_gripper_serial(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    assert client.put("/api/settings", json=config).status_code == 200

    include_gripper_values: list[bool] = []

    def fake_hardware_status(*, include_gripper: bool = True) -> dict:
        include_gripper_values.append(include_gripper)
        gripper_status = (
            {
                "ok": False,
                "message": "right COM9: serialOperation open ret=-1",
                "details": {
                    "ports": [
                        {"side": "left", "port": "COM8", "slaveId": 10, "baudrate": 115200, "openRet": 0, "ok": True},
                        {"side": "right", "port": "COM9", "slaveId": 9, "baudrate": 115200, "openRet": -1, "ok": False},
                    ]
                },
                "ports": [
                    {"side": "left", "port": "COM8", "slaveId": 10, "baudrate": 115200, "openRet": 0, "ok": True},
                    {"side": "right", "port": "COM9", "slaveId": 9, "baudrate": 115200, "openRet": -1, "ok": False},
                ],
            }
            if include_gripper
            else {"ok": None, "message": "Python gripper probe skipped"}
        )
        return {
            "camera": {},
            "force": {},
            "gripper": gripper_status,
            "pico": {},
        }

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)

    response = client.get("/api/hardware/status")

    assert response.status_code == 200
    body = response.json()
    assert include_gripper_values == [False]
    assert body["gripper"]["nativeManaged"] is True
    assert body["gripper"]["ok"] is True
    assert body["gripper"]["message"] == "managed by HAL-native teleop"
    assert body["gripper"]["ports"][1]["port"] == "COM9"


def test_native_gripper_status_reuses_supplied_config_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    app_state = _app_state(client)
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    assert client.put("/api/settings", json=config).status_code == 200

    def fake_hardware_status(*, include_gripper: bool = True) -> dict[str, object]:
        _ = include_gripper
        return {"camera": {}, "force": {}, "gripper": {"ok": None}, "pico": {}}

    original_get_config = app_state.settings.get_config
    calls: list[str] = []

    def guarded_get_config() -> dict[str, Any]:
        calls.append("get_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_get_config()
        raise AssertionError("native gripper status read config on the event loop")

    monkeypatch.setattr(app_state.hardware, "status", fake_hardware_status)
    monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)

    response = client.get("/api/hardware/status")

    assert response.status_code == 200
    assert response.json()["gripper"]["nativeManaged"] is True
    assert calls == ["get_config"]


def test_health_native_mode_does_not_probe_python_gripper_serial(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    assert client.put("/api/settings", json=config).status_code == 200

    include_gripper_values: list[bool] = []

    def fake_hardware_status(*, include_gripper: bool = True) -> dict:
        include_gripper_values.append(include_gripper)
        return {
            "camera": {},
            "force": {},
            "gripper": (
                {"ok": False, "message": "right COM9: serialOperation open ret=-1", "ports": []}
                if include_gripper
                else {"ok": None, "message": "Python gripper probe skipped"}
            ),
            "pico": {},
        }

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert include_gripper_values == [False]
    assert body["hardware"]["gripper"]["nativeManaged"] is True
    assert body["hardware"]["gripper"]["message"] == "managed by HAL-native teleop"


def test_hardware_status_reports_omega7_and_gripper_serial_identity(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {
                        "side": "left",
                        "connected": True,
                        "lastReadOk": True,
                        "deviceId": 0,
                        "serial": "OMEGA-L",
                        "leftHanded": True,
                    },
                    {
                        "side": "right",
                        "connected": True,
                        "lastReadOk": True,
                        "deviceId": 1,
                        "serial": "OMEGA-R",
                        "leftHanded": False,
                    },
                ]
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    monkeypatch.setattr(
        client.app.state.hardware,
        "status",
        lambda *, include_gripper=True: {"camera": {}, "force": {}, "gripper": {"ok": None}, "pico": {}},
    )

    response = client.get("/api/hardware/status")

    assert response.status_code == 200
    body = response.json()
    assert body["omega7"]["ok"] is True
    assert body["omega7"]["hands"][0]["serial"] == "OMEGA-L"
    assert body["omega7"]["hands"][1]["requestedId"] == 1
    assert body["gripper"]["ports"] == [
        {"side": "left", "port": "COM8", "slaveId": 10, "baudrate": 115200},
        {"side": "right", "port": "COM9", "slaveId": 9, "baudrate": 115200},
    ]


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

    right_enable_response = client.post("/api/motion/right/enable_all")
    assert right_enable_response.status_code == 200

    origin_response = client.post("/api/motion/right/return_origin")
    assert origin_response.status_code == 200
    assert origin_response.json()["data"]["command"] == "motion.home_origin_side"
    assert origin_response.json()["data"]["payload"]["side"] == "right"

    disable_response = client.post("/api/motion/left/disable_all")
    assert disable_response.status_code == 200
    state = asyncio.run(client.app.state.hal.motion_state())
    assert state["enabled"][:6] == [False] * 6

    stop_response = client.post("/api/motion/right/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["data"]["command"] == "motion.teleop_stop_side"

    bad_side_response = client.post("/api/motion/center/enable_all")
    assert bad_side_response.status_code == 400


def test_manual_axis_positive_buttons_forward_positive_physical_direction(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=False,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["motion"]["origin"]["valid"] = False
    config["motion"]["origin"]["leftValid"] = False
    config["motion"]["origin"]["rightValid"] = False
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["workOriginOffset"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulseDelta": [0.0] * 6,
        "rightPulseDelta": [0.0] * 6,
        "updatedAt": 0,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    _disable_rotation_work_limits(config)
    assert client.put("/api/settings", json=config).status_code == 200

    cases = [
        ("left", "X"),
        ("left", "Y"),
        ("left", "Z"),
        ("left", "Roll"),
        ("left", "Yaw"),
        ("right", "X"),
        ("right", "Y"),
        ("right", "Z"),
        ("right", "Roll"),
        ("right", "Pitch"),
    ]
    for side, axis in cases:
        step = 1 if axis in {"Roll", "Pitch", "Yaw"} else 10
        response = client.post(
            "/api/motion/manual_axis_move",
            json={"side": side, "axis": axis, "direction": 1, "step": step, "speedMode": "fine"},
        )
        assert response.status_code == 200, f"{side}.{axis}: {response.text}"

    manual_commands = [payload for name, payload in fake_hal.commands if name == "motion.manual_axis_move"]
    expected_direction = {
        ("left", "Y"): -1,
        ("right", "X"): -1,
        ("right", "Y"): -1,
        ("right", "Z"): -1,
    }
    assert [(payload["side"], payload["axis"], payload["direction"]) for payload in manual_commands] == [
        (side, axis, expected_direction.get((side, axis), 1)) for side, axis in cases
    ]


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
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"] = {
        "valid": False,
        "leftValid": False,
        "rightValid": False,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)

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


def test_motion_origin_mutations_are_blocked_during_record_session(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    original_origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 100,
        "previousValid": True,
        "previousLeftPulse": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
        "previousRightPulse": [107.0, 108.0, 109.0, 110.0, 111.0, 112.0],
        "previousUpdatedAt": 50,
    }
    config["motion"]["origin"] = original_origin
    _set_home_reference_to_origin(config)
    _set_zero_work_origin_offset(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)
    client.app.state.recorder._session_active = True

    responses = [
        client.post("/api/motion/origin/capture"),
        client.post("/api/motion/left/origin/capture"),
        client.post("/api/motion/origin/clear"),
        client.post("/api/motion/right/origin/clear"),
        client.post("/api/motion/origin/restore_previous"),
    ]

    for response in responses:
        assert response.status_code == 503
        assert response.json()["detail"]["message"] == (
            "motion work origin cannot be changed while recording session is active"
        )
    assert settings.get_config()["motion"]["origin"] == original_origin


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
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)

    missing_response = client.post("/api/motion/home_all")
    assert missing_response.status_code == 503
    assert "work origin is not captured" in missing_response.json()["detail"]["message"]

    assert client.post("/api/motion/origin/capture").status_code == 200
    assert client.post("/api/motion/left/enable_all").status_code == 200
    assert client.post("/api/motion/right/enable_all").status_code == 200
    home_response = client.post("/api/motion/home_all")

    assert home_response.status_code == 200
    data = home_response.json()["data"]
    assert data["command"] == "motion.home_all"
    assert data["payload"] == {
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "leftEnabledAxes": [True] * 6,
        "rightEnabledAxes": [True, True, True, True, True, False],
    }


def test_home_all_sends_work_origin_without_auto_enabling_motion_sides(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
    }
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = origin
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/home_all")

    assert response.status_code == 200
    assert fake_hal.commands == [
        (
            "motion.home_all",
            {
                "leftPulse": origin["leftPulse"],
                "rightPulse": origin["rightPulse"],
                "leftEnabledAxes": [True] * 6,
                "rightEnabledAxes": [True, True, True, True, True, False],
            },
        ),
    ]


def test_home_all_refuses_disabled_side_without_auto_enable(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 6 + [False] * 6,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
    }
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = origin
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/home_all")

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]["message"]
    assert fake_hal.commands == []


def test_return_origin_side_sends_work_origin_without_auto_enabling_side(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
    }
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = origin
    _set_home_reference_to_origin(config)
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/right/return_origin")

    assert response.status_code == 200
    assert fake_hal.commands == [
        (
            "motion.home_origin_side",
            {
                "side": "right",
                "pulse": origin["rightPulse"],
                "enabledAxes": [True, True, True, True, True, False],
            },
        ),
    ]


def test_return_origin_side_marks_record_reset_origin_ready(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    recorder = client.app.state.recorder
    recorder._reset_pending = True
    recorder._reset_required_sides = {"left"}
    recorder._reset_returned_sides = set()
    calls: list[str] = []

    async def fake_return_motion_origin_side(side: str) -> dict[str, object]:
        calls.append(side)
        return {"command": "motion.home_origin_side", "side": side}

    monkeypatch.setattr(client.app.state.commands, "return_motion_origin_side", fake_return_motion_origin_side)

    response = client.post("/api/motion/left/return_origin")

    assert response.status_code == 200
    assert calls == ["left"]
    status = recorder.status()
    assert status["resetPending"] is True
    assert status["resetRequiredSides"] == ["left"]
    assert status["resetReturnedSides"] == ["left"]
    assert status["resetReady"] is True


def test_return_origin_side_refuses_disabled_side_without_auto_enable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 6 + [False] * 6,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
    }
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = origin
    _set_home_reference_to_origin(config)
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/right/return_origin")

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]["message"]
    assert fake_hal.commands == []


def test_return_origin_side_ignores_disabled_right_yaw_soft_limit(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0, 0.0, 0.0, 0.0, 0.0, 1_000_000.0],
        "updatedAt": 123,
    }
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = origin
    _set_home_reference_to_origin(config)
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"]["yaw"] = {"min": -1000.0, "max": 1000.0}
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/right/return_origin")

    assert response.status_code == 200
    assert fake_hal.commands == [
        (
            "motion.home_origin_side",
            {
                "side": "right",
                "pulse": origin["rightPulse"],
                "enabledAxes": [True, True, True, True, True, False],
            },
        ),
    ]


def test_return_origin_and_home_all_are_blocked_during_estop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": True,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
    }
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = origin
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    client.app.state.settings.save_config(config, emit_log=False)
    client.app.state.telemetry.emergency_stop()

    home_response = client.post("/api/motion/home_all")
    right_response = client.post("/api/motion/right/return_origin")

    assert home_response.status_code == 503
    assert right_response.status_code == 503
    assert "emergency stop active" in home_response.json()["detail"]["message"]
    assert "emergency stop active" in right_response.json()["detail"]["message"]
    assert fake_hal.commands == []


def test_home_motion_side_refreshes_home_reference_and_shifts_work_origin(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    old_left_ref = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
    new_left_ref = [1010.0, 2020.0, 3030.0, 4050.0, 5060.0, 6070.0]
    left_offset = [11.0, 22.0, 33.0, 44.0, 55.0, 66.0]
    old_origin = [old_left_ref[index] + left_offset[index] for index in range(6)]

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []
            self.motion_state_calls = 0

        async def motion_state(self) -> dict[str, Any]:
            self.motion_state_calls += 1
            return {
                "positions": [0.0] * 12,
                "pulses": new_left_ref + [0.0] * 6,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": old_left_ref,
        "rightPulse": [0.0] * 6,
        "updatedAt": 123,
    }
    config["motion"]["workOriginOffset"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": False,
        "leftPulseDelta": left_offset,
        "rightPulseDelta": [0.0] * 6,
        "updatedAt": 123,
    }
    config["motion"]["origin"]["leftPulse"] = old_origin
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["valid"] = False
    settings.save_config(config, emit_log=False)
    baseline = settings.get_config()
    left_soft_limits_before = json.loads(json.dumps(baseline["motion"]["leftSoftLimits"]))

    response = client.post("/api/motion/left/home")

    assert response.status_code == 200
    assert response.json()["data"]["command"] == "motion.home_side"
    assert fake_hal.commands == [("motion.home_side", {"side": "left", "enabledAxes": [True] * 6})]
    assert fake_hal.motion_state_calls == 1
    saved = settings.get_config()
    assert saved["motion"]["homeReference"]["leftPulse"] == new_left_ref
    assert saved["motion"]["workOriginOffset"]["leftPulseDelta"] == left_offset
    assert saved["motion"]["origin"]["leftPulse"] == [
        new_left_ref[index] + left_offset[index] for index in range(6)
    ]
    assert saved["motion"]["leftSoftLimits"] != left_soft_limits_before
    _assert_rotation_window_from_home_reference(saved, "left")


def test_settings_invalidates_work_origin_when_offset_exceeds_hardware_zero_limit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    left_ref = [0.0] * 6
    left_offset = [0.0, 0.0, 0.0, -20_000.0, 0.0, 0.0]

    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": left_ref,
        "rightPulse": [0.0] * 6,
        "updatedAt": 123,
    }
    config["motion"]["workOriginOffset"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": False,
        "leftPulseDelta": left_offset,
        "rightPulseDelta": [0.0] * 6,
        "updatedAt": 123,
    }
    config["motion"]["origin"]["leftPulse"] = left_offset
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["valid"] = False
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)

    saved = settings.get_config()
    assert saved["motion"]["origin"]["leftValid"] is False
    assert saved["motion"]["workOriginOffset"]["leftValid"] is False
    assert saved["motion"]["origin"]["leftPulse"] == left_offset
    assert saved["motion"]["workOriginOffset"]["leftPulseDelta"] == left_offset


def test_motion_origin_capture_rejects_work_origin_outside_hardware_zero_limit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0, 0.0, 0.0, -20_000.0, 0.0, 0.0] + [0.0] * 6,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"]["leftValid"] = False
    config["motion"]["origin"]["valid"] = False
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 123,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/left/origin/capture", json={"confirmLargeDrift": True})

    assert response.status_code == 503
    assert "left Roll work origin exceeds soft limit" in response.json()["detail"]["message"]


def test_motion_origin_capture_records_work_origin_without_changing_home_reference(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    work_origin_pulses = [
        53026.0,
        277839.0,
        -323796.0,
        10142.0,
        84625.0,
        -12820.0,
        46474.0,
        49416.0,
        366208.0,
        -159813.0,
        -680804.0,
        -694.0,
    ]
    home_left = [50000.0, 277000.0, -323000.0, 10000.0, 84000.0, -12000.0]
    home_right = [46000.0, 49000.0, 366000.0, -159000.0, -680000.0, -600.0]

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": work_origin_pulses,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": home_left,
        "rightPulse": home_right,
        "updatedAt": 123,
    }
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/origin/capture", json={"confirmLargeDrift": True})

    assert response.status_code == 200
    assert fake_hal.commands == []
    saved = response.json()["data"]["config"]
    assert saved["motion"]["origin"]["leftPulse"] == work_origin_pulses[:6]
    assert saved["motion"]["origin"]["rightPulse"] == work_origin_pulses[6:12]
    assert saved["motion"]["homeReference"]["leftPulse"] == home_left
    assert saved["motion"]["homeReference"]["rightPulse"] == home_right
    assert saved["motion"]["homeReference"]["leftValid"] is True
    assert saved["motion"]["homeReference"]["rightValid"] is True
    assert saved["motion"]["workOriginOffset"]["leftPulseDelta"] == [
        work_origin_pulses[index] - home_left[index] for index in range(6)
    ]
    assert saved["motion"]["workOriginOffset"]["rightPulseDelta"] == [
        work_origin_pulses[index + 6] - home_right[index] for index in range(6)
    ]
    assert saved["motion"]["workOriginOffset"]["leftValid"] is True
    assert saved["motion"]["workOriginOffset"]["rightValid"] is True


def test_motion_origin_capture_preserves_previous_work_origin(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [float(value) for value in range(1, 13)],
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "rightPulse": [70.0, 80.0, 90.0, 100.0, 110.0, 120.0],
        "updatedAt": 1234,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/origin/capture")

    assert response.status_code == 200
    assert fake_hal.commands == []
    origin = response.json()["data"]["origin"]
    assert origin["leftPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert origin["rightPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    assert origin["previousValid"] is True
    assert origin["previousLeftPulse"] == [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    assert origin["previousRightPulse"] == [70.0, 80.0, 90.0, 100.0, 110.0, 120.0]
    assert origin["previousUpdatedAt"] == 1234


def test_motion_origin_capture_keeps_rotation_limits_anchored_to_home_reference(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    work_origin_pulses = [
        53026.0,
        277839.0,
        -323796.0,
        10142.0,
        84625.0,
        -12820.0,
        46474.0,
        49416.0,
        366208.0,
        -159813.0,
        -680804.0,
        -694.0,
    ]

    class FakeHal:
        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": work_origin_pulses,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": work_origin_pulses[:6],
        "rightPulse": work_origin_pulses[6:12],
        "updatedAt": 123,
    }
    config["motion"]["leftSoftLimits"]["yaw"] = {"min": -8000.0, "max": 8000.0}
    config["motion"]["rightSoftLimits"]["yaw"] = {"min": -40000.0, "max": -26000.0}
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/origin/capture", json={"confirmLargeDrift": True})

    assert response.status_code == 200
    saved = response.json()["data"]["config"]
    _assert_rotation_window_from_home_reference(saved, "left")
    _assert_rotation_window_from_home_reference(saved, "right")
    left_yaw = saved["motion"]["leftSoftLimits"]["yaw"]
    right_yaw = saved["motion"]["rightSoftLimits"]["yaw"]
    assert left_yaw["max"] - left_yaw["min"] == pytest.approx(14_000.0)
    assert right_yaw["max"] - right_yaw["min"] == pytest.approx(14_000.0)


def test_motion_origin_capture_does_not_issue_hardware_home(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [float(value) for value in range(1, 13)],
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            self.commands.append((name, payload or {}))
            if name == "motion.home_side" and (payload or {}).get("side") == "right":
                raise RuntimeError("right home failed")
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    original_origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "rightPulse": [70.0, 80.0, 90.0, 100.0, 110.0, 120.0],
        "updatedAt": 1234,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config = settings.get_config()
    config["motion"]["origin"] = original_origin
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/origin/capture")

    assert response.status_code == 200
    assert fake_hal.commands == []
    assert settings.get_config()["motion"]["origin"]["leftPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert settings.get_config()["motion"]["origin"]["rightPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]


def test_motion_origin_capture_requires_confirmation_for_large_origin_drift(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")

    class FakeHal:
        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [100000.0, 0.0, 0.0, 0.0, 0.0, 0.0] + [0.0] * 6,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    original_origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1234,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["origin"] = original_origin
    _set_home_reference_to_origin(config)
    _set_zero_work_origin_offset(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/left/origin/capture")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "ORIGIN_DRIFT_CONFIRM_REQUIRED"
    assert detail["drift"]["requiresConfirmation"] is True
    assert detail["drift"]["sides"][0]["side"] == "left"
    assert detail["drift"]["sides"][0]["axes"][0]["axis"] == "X"
    assert settings.get_config()["motion"]["origin"] == original_origin


def test_motion_origin_capture_allows_confirmed_large_origin_drift(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")

    class FakeHal:
        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [100000.0, 0.0, 0.0, 0.0, 0.0, 0.0] + [0.0] * 6,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1234,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/left/origin/capture", json={"confirmLargeDrift": True})

    assert response.status_code == 200
    data = response.json()["data"]
    origin = data["origin"]
    assert origin["leftPulse"] == [100000.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert origin["rightPulse"] == [0.0] * 6
    assert origin["previousValid"] is True
    assert origin["previousLeftPulse"] == [0.0] * 6
    assert data["originCaptureDrift"]["requiresConfirmation"] is True


def test_restore_previous_motion_origin_swaps_current_and_previous(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 100,
        "previousValid": True,
        "previousLeftPulse": [101.0, 102.0, 103.0, 9.0, 10.0, 11.0],
        "previousRightPulse": [107.0, 108.0, 109.0, 13.0, 14.0, 15.0],
        "previousUpdatedAt": 50,
    }
    _set_home_reference_to_origin(config)
    _set_zero_work_origin_offset(config)
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/origin/restore_previous")

    assert response.status_code == 200
    origin = response.json()["data"]["origin"]
    assert origin["leftPulse"] == [101.0, 102.0, 103.0, 9.0, 10.0, 11.0]
    assert origin["rightPulse"] == [107.0, 108.0, 109.0, 13.0, 14.0, 15.0]
    assert origin["updatedAt"] == 50
    assert origin["previousValid"] is True
    assert origin["previousLeftPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert origin["previousRightPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    assert origin["previousUpdatedAt"] == 100


def test_restore_previous_motion_origin_keeps_rotation_limits_anchored_to_home_reference(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    settings = client.app.state.settings
    config = settings.get_config()
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4000.0, 5000.0, 6000.0],
        "rightPulse": [7.0, 8.0, 9.0, 10000.0, 11000.0, 12000.0],
        "updatedAt": 100,
        "previousValid": True,
        "previousLeftPulse": [53026.0, 277839.0, -323796.0, 10142.0, 84625.0, -12820.0],
        "previousRightPulse": [46474.0, 49416.0, 366208.0, -159813.0, -680804.0, -694.0],
        "previousUpdatedAt": 50,
    }
    config["motion"]["leftSoftLimits"]["yaw"] = {"min": -8000.0, "max": 8000.0}
    config["motion"]["rightSoftLimits"]["yaw"] = {"min": -40000.0, "max": -26000.0}
    settings.save_config(config, emit_log=False)

    response = client.post("/api/motion/origin/restore_previous")

    assert response.status_code == 200
    saved = response.json()["data"]["config"]
    _assert_rotation_window_from_home_reference(saved, "left")
    _assert_rotation_window_from_home_reference(saved, "right")


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
        -4900.0,
        9800.0,
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
    assert relative[1] == pytest.approx(2000.0)
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


def test_runtime_shutdown_skips_stop_stack_while_websocket_is_active(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        pass

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        _ = kwargs
        calls.append(args)
        return FakeProcess()

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_CLOSE_SHUTDOWN_DELAY_SEC", "0.01")
    monkeypatch.setattr("backend.app.subprocess.Popen", fake_popen)

    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["type"] == "telemetry"
            response = client.post("/api/runtime/shutdown", json={"reason": "still-open"})
            assert response.status_code == 200
            time.sleep(0.1)

    assert calls == []


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


def test_runtime_release_handles_stops_gripper_workers_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    calls: list[float] = []

    def fake_stop_all(timeout_sec: float = 1.0) -> None:
        calls.append(timeout_sec)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise AssertionError("gripper worker stop_all ran on the event loop")

    monkeypatch.setattr(client.app.state.gripper_workers, "stop_all", fake_stop_all)

    response = client.post("/api/runtime/release_handles", json={"reason": "unit-test"})

    assert response.status_code == 200
    assert calls == [1.0]


def test_app_shutdown_closes_telemetry_hardware_resources(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    shutdown_calls: list[str] = []

    with TestClient(create_app(tmp_path)) as client:
        def fake_shutdown() -> None:
            shutdown_calls.append("shutdown")
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            raise AssertionError("telemetry shutdown ran on the event loop")

        client.app.state.telemetry.shutdown = fake_shutdown

    assert shutdown_calls == ["shutdown"]


def test_app_shutdown_stops_gripper_workers_off_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    calls: list[float] = []

    with TestClient(create_app(tmp_path)) as client:
        def fake_stop_all(timeout_sec: float = 1.0) -> None:
            calls.append(timeout_sec)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            raise AssertionError("gripper worker stop_all ran on the event loop")

        monkeypatch.setattr(client.app.state.gripper_workers, "stop_all", fake_stop_all)

    assert calls == [1.0]


def test_teleop_logical_connect_enables_mapped_motion_side(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["gripperTeleop"]["enabled"] = False
    assert client.put("/api/settings", json=config).status_code == 200
    gripper_start_calls: list[str] = []
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": gripper_start_calls.append(source),
    )

    connect_response = client.post("/api/teleop/left/connect")
    assert connect_response.status_code == 200
    assert connect_response.json()["data"]["connected"] is True
    assert client.get("/api/settings").json()["teleop"]["leftConnected"] is True
    assert gripper_start_calls == []

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
        json={"side": "right", "axis": "X", "direction": 1, "step": 100, "speedMode": "fine"},
    )
    assert command_response.status_code == 200


def test_teleop_logical_connection_saves_config_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["gripperTeleop"]["enabled"] = False
    assert client.put("/api/settings", json=config).status_code == 200
    calls: list[bool] = []

    def fake_save_config(next_config: dict[str, Any], emit_log: bool = True) -> dict[str, Any]:
        _ = emit_log
        calls.append(bool(next_config["teleop"]["leftConnected"]))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return next_config
        raise AssertionError("teleop logical save_config ran on the event loop")

    app_state = _app_state(client)
    monkeypatch.setattr(app_state.settings, "save_config", fake_save_config)

    connect_response = client.post("/api/teleop/left/connect")
    disconnect_response = client.post("/api/teleop/left/disconnect")

    assert connect_response.status_code == 200
    assert disconnect_response.status_code == 200
    assert calls == [True, False]


def test_teleop_logical_connect_starts_native_without_return_to_work_origin() -> None:
    source = (REPO_ROOT / "backend" / "app.py").read_text(encoding="utf-8")

    assert 'await teleop_mapper.start("teleop-connect", pre_home=False, home_side=mapped_side)' in source


def test_record_session_controls_gripper_teleop_recording_source(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    start_calls: list[str] = []
    stop_calls: list[str] = []

    async def fake_start_session(_dataset_name: str, _task: str) -> dict[str, Any]:
        return {"recording": True}

    async def fake_save_episode() -> dict[str, Any]:
        return {"recording": False}

    monkeypatch.setattr(client.app.state.recorder, "start_session", fake_start_session)
    monkeypatch.setattr(client.app.state.recorder, "save_episode", fake_save_episode)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": start_calls.append(source),
    )
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )
    monkeypatch.setattr(client.app.state.gripper_tele, "get_status", lambda: {"running": True})

    start_response = client.post(
        "/api/record/session/create",
        json={"dataset_name": "unit", "task": "teleop"},
    )
    save_response = client.post("/api/record/episode/save")

    assert start_response.status_code == 200
    assert save_response.status_code == 200
    assert start_calls == ["recording"]
    assert stop_calls == ["recording"]


def test_record_session_create_rolls_back_when_gripper_teleop_start_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    finish_calls: list[str] = []

    async def fake_start_session(_dataset_name: str, _task: str) -> dict[str, Any]:
        return {"active": True, "recording": True}

    async def fake_finish_session() -> dict[str, Any]:
        finish_calls.append("finish")
        return {"active": False, "recording": False}

    def fail_gripper_start(source: str = "manual") -> None:
        raise RuntimeError(f"{source} gripper teleop failed")

    monkeypatch.setattr(client.app.state.recorder, "start_session", fake_start_session)
    monkeypatch.setattr(client.app.state.recorder, "finish_session", fake_finish_session)
    monkeypatch.setattr(client.app.state.gripper_tele, "start", fail_gripper_start)

    response = client.post(
        "/api/record/session/create",
        json={"dataset_name": "unit", "task": "teleop"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECORDING_BUSY"
    assert finish_calls == ["finish"]


def test_record_save_failure_stops_gripper_teleop_recording_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    stop_calls: list[str] = []

    async def fake_save_episode() -> dict[str, Any]:
        raise DatasetSaveError("writer failed")

    monkeypatch.setattr(client.app.state.recorder, "save_episode", fake_save_episode)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )

    response = client.post("/api/record/episode/save")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "RECORDING_SAVE_FAILED"
    assert stop_calls == ["recording"]


def test_record_discard_pauses_gripper_teleop_recording_source(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    start_calls: list[str] = []
    stop_calls: list[str] = []

    async def fake_discard_episode() -> dict[str, Any]:
        return {"recording": False}

    monkeypatch.setattr(client.app.state.recorder, "discard_episode", fake_discard_episode)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": start_calls.append(source),
    )
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )

    response = client.post("/api/record/episode/discard")

    assert response.status_code == 200
    assert response.json()["data"]["recording"] is False
    assert start_calls == []
    assert stop_calls == ["recording"]


def test_record_discard_failure_stops_gripper_teleop_recording_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    stop_calls: list[str] = []

    async def fake_discard_episode() -> dict[str, Any]:
        raise RuntimeError("discard failed")

    monkeypatch.setattr(client.app.state.recorder, "discard_episode", fake_discard_episode)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )

    response = client.post("/api/record/episode/discard")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECORDING_NOT_ACTIVE"
    assert stop_calls == ["recording"]


def test_record_finish_failure_stops_gripper_teleop_recording_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    stop_calls: list[str] = []

    async def fake_finish_session() -> dict[str, Any]:
        raise RuntimeError("finish failed")

    monkeypatch.setattr(client.app.state.recorder, "finish_session", fake_finish_session)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )

    response = client.post("/api/record/session/finish")

    assert response.status_code == 500
    assert stop_calls == ["recording"]


def test_native_record_save_releases_aux_teleop_sources_without_restart(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "hal_native"
    client.app.state.settings.save_config(config, emit_log=False)
    native_stop_calls: list[tuple[str, bool]] = []

    async def fake_save_episode() -> dict[str, Any]:
        return {"recording": False}

    async def fake_native_stop(source: str = "recording", *, restart_remaining: bool = True) -> dict[str, Any]:
        native_stop_calls.append((source, restart_remaining))
        return {"running": source == "recording"}

    monkeypatch.setattr(client.app.state.recorder, "save_episode", fake_save_episode)
    monkeypatch.setattr(client.app.state.teleop_mapper, "stop", fake_native_stop)

    response = client.post("/api/record/episode/save")

    assert response.status_code == 200
    assert native_stop_calls == [
        ("teleop-connect", False),
        ("manual-gripper", False),
    ]


def test_backend_shutdown_finishes_active_record_session(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    finish_calls: list[str] = []

    with TestClient(create_app(tmp_path)) as client:
        monkeypatch.setattr(
            client.app.state.recorder,
            "status",
            lambda: {"active": True, "recording": True},
        )

        async def fake_finish_session() -> dict[str, Any]:
            finish_calls.append("finish")
            return {"active": False, "recording": False}

        monkeypatch.setattr(client.app.state.recorder, "finish_session", fake_finish_session)

    assert finish_calls == ["finish"]


def test_record_skip_reset_reports_not_ready(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    async def fake_skip_reset() -> dict[str, Any]:
        raise RuntimeError("record reset is not ready")

    monkeypatch.setattr(client.app.state.recorder, "skip_reset", fake_skip_reset)

    response = client.post("/api/record/reset/skip")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECORD_RESET_NOT_READY"


def test_record_skip_reset_discards_episode_when_gripper_teleop_start_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "python_mapper"
    client.app.state.settings.save_config(config, emit_log=False)
    discard_calls: list[str] = []

    async def fake_skip_reset() -> dict[str, Any]:
        return {"active": True, "recording": True}

    async def fake_discard_episode() -> dict[str, Any]:
        discard_calls.append("discard")
        return {"active": True, "recording": False, "resetPending": True}

    def fail_gripper_start(source: str = "manual") -> None:
        raise RuntimeError(f"{source} gripper teleop failed")

    monkeypatch.setattr(client.app.state.recorder, "skip_reset", fake_skip_reset)
    monkeypatch.setattr(client.app.state.recorder, "discard_episode", fake_discard_episode)
    monkeypatch.setattr(client.app.state.gripper_tele, "start", fail_gripper_start)

    response = client.post("/api/record/reset/skip")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECORDING_NOT_ACTIVE"
    assert discard_calls == ["discard"]


def test_record_skip_reset_not_ready_does_not_stop_native_aux_sources(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    assert client.put("/api/settings", json=config).status_code == 200
    teleop_stop_calls: list[str] = []
    gripper_stop_calls: list[str] = []
    worker_stop_calls: list[str] = []

    async def fake_skip_reset() -> dict[str, Any]:
        raise RuntimeError("record reset work origin is not ready")

    async def fake_native_stop(source: str = "recording", *, restart_remaining: bool = True) -> dict[str, Any]:
        teleop_stop_calls.append(source)
        return {"running": False}

    monkeypatch.setattr(client.app.state.recorder, "skip_reset", fake_skip_reset)
    monkeypatch.setattr(
        client.app.state.recorder,
        "status",
        lambda: {"active": True, "recording": False, "resetPending": True, "resetReady": False},
    )
    monkeypatch.setattr(client.app.state.teleop_mapper, "stop", fake_native_stop)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: gripper_stop_calls.append("force" if force else source),
    )
    monkeypatch.setattr(client.app.state.gripper_workers, "stop_all", lambda: worker_stop_calls.append("stop_all"))

    response = client.post("/api/record/reset/skip")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RECORD_RESET_NOT_READY"
    assert teleop_stop_calls == []
    assert gripper_stop_calls == []
    assert worker_stop_calls == []


def test_native_gripper_teleop_start_uses_python_gripper_service(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    start_calls: list[str] = []
    stop_calls: list[str] = []
    native_start_calls: list[tuple[str, bool]] = []

    async def fake_native_start(source: str = "recording", home_side: str | None = None, *, pre_home: bool = True):
        native_start_calls.append((source, pre_home))
        return {"running": True}

    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": start_calls.append(source),
    )
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )
    monkeypatch.setattr(client.app.state.gripper_tele, "get_status", lambda: {"running": True, "sources": ["manual"]})
    monkeypatch.setattr(client.app.state.teleop_mapper, "start", fake_native_start)

    response = client.post("/api/teleop/gripper/start")

    assert response.status_code == 200
    assert response.json()["data"] == {"running": True, "sources": ["manual"]}
    assert start_calls == ["manual"]
    assert stop_calls == []
    assert native_start_calls == []


def test_gripper_teleop_loop_reads_config_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    with TestClient(create_app(tmp_path)) as client:
        config = client.get("/api/settings").json()
        config["teleop"]["gripperTeleop"]["enabled"] = False
        config["teleop"]["gripperTeleop"]["loopHz"] = 50
        assert client.put("/api/settings", json=config).status_code == 200
        app_state = _app_state(client)
        original_get_config = app_state.settings.get_config
        calls: list[str] = []

        def guarded_get_config() -> dict[str, Any]:
            calls.append("get_config")
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return original_get_config()
            raise AssertionError("gripper teleop loop read config on the event loop")

        monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)

        response = client.post("/api/teleop/gripper/start")
        time.sleep(0.08)
        status_response = client.get("/api/teleop/gripper/status")
        client.post("/api/teleop/gripper/stop")

        assert response.status_code == 200
        assert status_response.status_code == 200
        assert status_response.json()["data"]["running"] is True
        assert calls


def test_native_teleop_connect_starts_python_dual_worker_gripper_follow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["teleop"]["gripperTeleop"]["enabled"] = True
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200
    gripper_start_calls: list[str] = []
    worker_stop_calls: list[str] = []
    native_start_calls: list[str] = []

    async def fake_native_start(source: str = "recording", home_side: str | None = None, *, pre_home: bool = True):
        native_start_calls.append(source)
        return {"running": True}

    monkeypatch.setattr(client.app.state.teleop_mapper, "start", fake_native_start)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": gripper_start_calls.append(source),
    )
    monkeypatch.setattr(client.app.state.gripper_workers, "stop_all", lambda: worker_stop_calls.append("stop_all"))

    response = client.post("/api/teleop/left/connect")

    assert response.status_code == 200
    time.sleep(0.1)
    assert native_start_calls == ["teleop-connect"]
    assert gripper_start_calls == ["teleop-connect"]
    assert worker_stop_calls == []


def test_native_teleop_connect_rolls_back_when_gripper_follow_start_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["teleop"]["gripperTeleop"]["enabled"] = True
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200
    native_start_calls: list[str] = []
    native_stop_calls: list[str] = []

    async def fake_native_start(source: str = "recording", home_side: str | None = None, *, pre_home: bool = True):
        native_start_calls.append(source)
        return {"running": True}

    async def fake_native_stop(source: str = "recording", *, restart_remaining: bool = True) -> dict[str, Any]:
        native_stop_calls.append(source)
        return {"running": False}

    def fail_gripper_start(source: str = "manual") -> None:
        raise RuntimeError(f"{source} gripper follow failed")

    monkeypatch.setattr(client.app.state.teleop_mapper, "start", fake_native_start)
    monkeypatch.setattr(client.app.state.teleop_mapper, "stop", fake_native_stop)
    monkeypatch.setattr(client.app.state.gripper_tele, "start", fail_gripper_start)

    response = client.post("/api/teleop/left/connect")

    deadline = time.monotonic() + 1.0
    while not native_stop_calls and time.monotonic() < deadline:
        time.sleep(0.01)

    assert response.status_code == 200
    assert native_start_calls == ["teleop-connect"]
    assert native_stop_calls == ["teleop-connect"]


def test_native_gripper_teleop_stop_uses_python_gripper_service(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    python_stop_calls: list[str] = []
    native_stop_calls: list[str] = []

    async def fake_native_stop(source: str = "recording") -> dict[str, Any]:
        native_stop_calls.append(source)
        return {"running": False}

    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: python_stop_calls.append("force" if force else source),
    )
    monkeypatch.setattr(client.app.state.gripper_tele, "get_status", lambda: {"running": False, "sources": []})
    monkeypatch.setattr(client.app.state.teleop_mapper, "stop", fake_native_stop)

    response = client.post("/api/teleop/gripper/stop")

    assert response.status_code == 200
    assert response.json()["data"] == {"running": False, "sources": []}
    assert python_stop_calls == ["manual"]
    assert native_stop_calls == []


def test_native_gripper_status_uses_python_gripper_service(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.teleop_mapper._native_status_cache = {
        "gripperTargets": [8.0, 9.0],
        "grippers": {
            "left": {
                "ok": False,
                "message": "serialOperation open failed COM8, ret=-1",
                "positionMm": 1.25,
                "targetMm": 8.0,
                "lastCommandTs": 123,
            },
            "right": {
                "ok": True,
                "message": "runWithParam COM9, slave=9, pos=10, speed=128, torque=192, ret=0",
                "positionMm": 22.5,
                "targetMm": 9.0,
                "lastCommandTs": 124,
            },
        },
    }
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "get_status",
        lambda: {"running": True, "sources": ["teleop-connect"], "leftTargetMm": 12.0},
    )

    response = client.get("/api/teleop/gripper/status")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "running": True,
        "sources": ["teleop-connect"],
        "leftTargetMm": 12.0,
    }


def test_native_gripper_status_does_not_report_stale_hal_running(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    client.app.state.teleop_mapper._native_status_cache = {
        "running": True,
        "gripperTargets": [8.0, 9.0],
        "grippers": {
            "left": {"ok": True, "positionMm": 8.0, "targetMm": 8.0, "message": "", "lastCommandTs": 0},
            "right": {"ok": True, "positionMm": 9.0, "targetMm": 9.0, "message": "", "lastCommandTs": 0},
        },
    }
    monkeypatch.setattr(client.app.state.gripper_tele, "get_status", lambda: {"running": False, "sources": []})

    response = client.get("/api/teleop/gripper/status")

    assert response.status_code == 200
    assert response.json()["data"] == {"running": False, "sources": []}


def test_native_gripper_command_dispatches_without_manual_teleop_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    assert client.put("/api/settings", json=config).status_code == 200
    start_calls: list[tuple[str, bool]] = []
    command_calls: list[str] = []

    async def fake_native_start(source: str = "recording", home_side: str | None = None, *, pre_home: bool = True):
        start_calls.append((source, pre_home))
        return {"running": True}

    async def fake_gripper_command(request: GripperCommandRequest) -> dict[str, Any]:
        command_calls.append(request.command)
        return {"nativeManaged": True, "targetMm": 26.0}

    monkeypatch.setattr(client.app.state.teleop_mapper, "start", fake_native_start)
    monkeypatch.setattr(client.app.state.commands, "gripper_command", fake_gripper_command)

    response = client.post("/api/gripper/left/command", json={"side": "left", "command": "open"})

    assert response.status_code == 200
    assert start_calls == []
    assert command_calls == ["open"]


def test_native_record_session_starts_python_gripper_teleop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))
    start_calls: list[str] = []
    stop_calls: list[str] = []
    worker_stops: list[str] = []

    async def fake_start_session(_dataset_name: str, _task: str) -> dict[str, Any]:
        return {"recording": True}

    monkeypatch.setattr(client.app.state.recorder, "start_session", fake_start_session)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": start_calls.append(source),
    )
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "stop",
        lambda source="manual", force=False: stop_calls.append("force" if force else source),
    )
    monkeypatch.setattr(client.app.state.gripper_workers, "stop_all", lambda: worker_stops.append("stop_all"))

    response = client.post(
        "/api/record/session/create",
        json={"dataset_name": "unit", "task": "teleop"},
    )

    assert response.status_code == 200
    assert start_calls == ["recording"]
    assert stop_calls == []
    assert worker_stops == []


def test_real_record_session_requires_hardware_recognition_before_start(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {"side": "left", "connected": True, "lastReadOk": True, "deviceId": 0, "serial": "L"},
                    {"side": "right", "connected": True, "lastReadOk": True, "deviceId": 1, "serial": "R"},
                ]
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    assert client.put("/api/settings", json=config).status_code == 200
    start_calls: list[str] = []
    include_gripper_values: list[bool] = []

    async def fake_start_session(_dataset_name: str, _task: str) -> dict[str, Any]:
        start_calls.append("start")
        return {"recording": True}

    monkeypatch.setattr(client.app.state.recorder, "start_session", fake_start_session)

    def fake_hardware_status(*, include_gripper: bool = True) -> dict[str, Any]:
        include_gripper_values.append(include_gripper)
        return {
            "camera": {"ok": False, "message": "cameras not ready"},
            "force": {"ok": True, "message": "force ready"},
            "gripper": (
                {"ok": False, "message": "right COM9: serialOperation open ret=-1", "ports": []}
                if include_gripper
                else {"ok": None, "message": "Python gripper probe skipped"}
            ),
            "pico": {},
        }

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)

    response = client.post(
        "/api/record/session/create",
        json={"dataset_name": "unit", "task": "teleop"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "HARDWARE_PRECHECK_FAILED"
    assert "cameras not ready" in response.json()["detail"]["message"]
    assert "COM9" not in response.json()["detail"]["message"]
    assert start_calls == []
    assert include_gripper_values == [False]


def test_native_record_session_ignores_stale_native_gripper_status_when_python_workers_own_gripper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {"side": "left", "connected": True, "lastReadOk": True, "deviceId": 0, "serial": "L"},
                    {"side": "right", "connected": True, "lastReadOk": True, "deviceId": 1, "serial": "R"},
                ]
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"
    assert client.put("/api/settings", json=config).status_code == 200
    client.app.state.teleop_mapper._native_status_cache = {
        "running": True,
        "gripperTargets": [8.0, 9.0],
        "grippers": {
            "left": {"ok": False, "message": "serialOperation open failed COM8, ret=-1", "lastCommandTs": 123},
            "right": {"ok": True, "message": "ok", "lastCommandTs": 124},
        },
    }
    start_calls: list[str] = []
    include_gripper_values: list[bool] = []

    async def fake_start_session(_dataset_name: str, _task: str) -> dict[str, Any]:
        start_calls.append("start")
        return {"recording": True}

    def fake_hardware_status(*, include_gripper: bool = True) -> dict[str, Any]:
        include_gripper_values.append(include_gripper)
        return {
            "camera": {"ok": True, "message": "cameras ready"},
            "force": {"ok": True, "message": "force ready"},
            "gripper": (
                {"ok": False, "message": "right COM9: serialOperation open ret=-1", "ports": []}
                if include_gripper
                else {"ok": None, "message": "Python gripper probe skipped"}
            ),
            "pico": {},
        }

    monkeypatch.setattr(client.app.state.recorder, "start_session", fake_start_session)
    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)

    response = client.post(
        "/api/record/session/create",
        json={"dataset_name": "unit", "task": "teleop"},
    )

    assert response.status_code == 200
    assert start_calls == ["start"]
    assert include_gripper_values == [False]


def test_native_gripper_teleop_start_uses_python_service_without_native_source(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {"side": "left", "connected": True, "lastReadOk": True, "deviceId": 0, "serial": "L"},
                    {"side": "right", "connected": True, "lastReadOk": True, "deviceId": 1, "serial": "R"},
                ]
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            return {"command": name, "payload": payload or {}}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["teleop"]["engine"] = "hal_native"
    assert client.put("/api/settings", json=config).status_code == 200
    start_calls: list[str] = []
    gripper_start_calls: list[str] = []
    include_gripper_values: list[bool] = []

    async def fake_native_start(source: str = "recording", home_side: str | None = None, *, pre_home: bool = True):
        _ = (home_side, pre_home)
        start_calls.append(source)
        return {"running": True}

    monkeypatch.setattr(client.app.state.teleop_mapper, "start", fake_native_start)
    monkeypatch.setattr(
        client.app.state.gripper_tele,
        "start",
        lambda source="manual": gripper_start_calls.append(source),
    )
    monkeypatch.setattr(client.app.state.gripper_tele, "get_status", lambda: {"running": True, "sources": ["manual"]})

    def fake_hardware_status(*, include_gripper: bool = True) -> dict[str, Any]:
        include_gripper_values.append(include_gripper)
        return {
            "camera": {"ok": True, "message": "cameras ready"},
            "force": {"ok": True, "message": "force ready"},
            "gripper": (
                {"ok": False, "message": "right COM9: serialOperation open ret=-1", "ports": []}
                if include_gripper
                else {"ok": None, "message": "Python gripper probe skipped"}
            ),
            "pico": {},
        }

    monkeypatch.setattr(client.app.state.hardware, "status", fake_hardware_status)

    response = client.post("/api/teleop/gripper/start")

    assert response.status_code == 200
    assert response.json()["data"] == {"running": True, "sources": ["manual"]}
    assert start_calls == []
    assert gripper_start_calls == ["manual"]
    assert include_gripper_values == []


def test_native_teleop_connect_does_not_require_gripper_probe_before_start(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {"side": "left", "connected": True, "lastReadOk": True, "deviceId": 0, "serial": "L"},
                    {"side": "right", "connected": True, "lastReadOk": True, "deviceId": 1, "serial": "R"},
                ]
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    with TestClient(create_app(tmp_path)) as client:
        config = client.app.state.settings.get_config()
        config["motion"]["homeReference"] = {
            "valid": True,
            "leftValid": True,
            "rightValid": True,
            "leftPulse": [0.0] * 6,
            "rightPulse": [0.0] * 6,
            "updatedAt": 0,
        }
        config["motion"]["origin"] = {
            "valid": True,
            "leftValid": True,
            "rightValid": True,
            "leftPulse": [0.0] * 6,
            "rightPulse": [0.0] * 6,
            "updatedAt": 0,
            "previousValid": False,
            "previousLeftPulse": [0.0] * 6,
            "previousRightPulse": [0.0] * 6,
            "previousUpdatedAt": 0,
        }
        config["motion"]["workOriginOffset"] = {
            "valid": True,
            "leftValid": True,
            "rightValid": True,
            "leftPulseDelta": [0.0] * 6,
            "rightPulseDelta": [0.0] * 6,
            "updatedAt": 0,
        }
        config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
        config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
        client.app.state.settings.save_config(config, emit_log=False)
        monkeypatch.setattr(
            client.app.state.hardware,
            "status",
            lambda *, include_gripper=True: {
                "camera": {"ok": True, "message": "cameras ready"},
                "force": {"ok": True, "message": "force ready"},
                "gripper": {"ok": False, "message": "right COM9: serialOperation open ret=-1", "ports": []},
                "pico": {},
            },
        )
        response = client.post("/api/teleop/left/connect")

        assert response.status_code == 200
        assert client.app.state.settings.get_config()["teleop"]["leftConnected"] is True
        configure_payloads = []
        for _ in range(50):
            configure_payloads = [payload for name, payload in fake_hal.commands if name == "teleop.native.configure"]
            if configure_payloads:
                break
            time.sleep(0.01)
        assert configure_payloads
    assert configure_payloads[-1]["gripperTeleopEnabled"] is False


def test_teleop_connect_accepts_physical_hand_when_last_read_timed_out(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {
                        "side": "left",
                        "connected": True,
                        "lastReadOk": False,
                        "deviceId": 0,
                        "serial": "L",
                        "message": "operation timed out",
                    },
                    {"side": "right", "connected": True, "lastReadOk": True, "deviceId": 1, "serial": "R"},
                ]
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            self.commands.append((name, payload or {}))
            if name == "teleop.native.status":
                return {"mode": "real", "command": name, "response": {"running": True}}
            return {"mode": "real", "command": name, "response": {}}

        async def motion_state(self) -> dict[str, Any]:
            return {"enabled": [True] * 12, "pulses": [0.0] * 12}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "hal_native"
    client.app.state.settings.save_config(config, emit_log=False)
    monkeypatch.setattr(
        client.app.state.hardware,
        "status",
        lambda *, include_gripper=True: {
            "camera": {"ok": True, "message": "cameras ready"},
            "force": {"ok": True, "message": "force ready"},
            "gripper": {"ok": True, "message": "grippers ready", "ports": []},
            "pico": {},
        },
    )

    response = client.post("/api/teleop/left/connect")

    assert response.status_code == 200
    assert response.json()["data"]["connected"] is True
    assert response.json()["data"]["backgroundSync"] is True
    assert client.app.state.settings.get_config()["teleop"]["leftConnected"] is True


def test_teleop_connect_accepts_logical_connection_without_waiting_for_hal(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class BlockingHal:
        async def health(self) -> HalHealth:
            raise RuntimeError("HAL health should run in background")

        async def omega_state(self) -> dict[str, Any]:
            raise RuntimeError("omega state should run in background")

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            raise RuntimeError(f"{name} should run in background")

        async def motion_state(self) -> dict[str, Any]:
            raise RuntimeError("motion state should run in background")

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: BlockingHal())
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "hal_native"
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/teleop/left/connect")

    assert response.status_code == 200
    assert response.json()["data"]["connected"] is True
    assert response.json()["data"]["backgroundSync"] is True
    assert client.app.state.settings.get_config()["teleop"]["leftConnected"] is True


def test_teleop_disconnect_schedules_native_refresh_and_reports_mapped_stop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict[str, Any]:
            return {"hands": []}

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            return {"command": name, "payload": payload or {}}

        async def motion_state(self) -> dict[str, Any]:
            return {"enabled": [True] * 12, "pulses": [0.0] * 12}

    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    client = TestClient(create_app(tmp_path))
    config = client.app.state.settings.get_config()
    config["teleop"]["engine"] = "hal_native"
    config["teleop"]["leftConnected"] = True
    config["teleop"]["rightConnected"] = True
    config["teleop"]["swapTeleopChannels"] = True
    client.app.state.settings.save_config(config, emit_log=False)

    response = client.post("/api/teleop/left/disconnect")

    assert response.status_code == 200
    assert response.json()["data"]["connected"] is False
    assert response.json()["data"]["stoppedSide"] == "right"
    assert response.json()["data"]["backgroundSync"] is True
    assert client.app.state.settings.get_config()["teleop"]["leftConnected"] is False


def test_teleop_logical_connect_does_not_return_to_work_origin(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict]] = []

        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def omega_state(self) -> dict:
            return {
                "hands": [
                    {
                        "side": "left",
                        "connected": True,
                        "lastReadOk": True,
                        "pose": [0.0] * 6,
                    },
                    {
                        "side": "right",
                        "connected": True,
                        "lastReadOk": True,
                        "pose": [0.0] * 6,
                    }
                ]
            }

        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    monkeypatch.setattr(
        client.app.state.hardware,
        "status",
        lambda *, include_gripper=True: {
            "camera": {"ok": True, "message": "cameras ready"},
            "force": {"ok": True, "message": "force ready"},
            "gripper": {"ok": True, "message": "grippers ready", "ports": []},
            "pico": {},
        },
    )

    connect_response = client.post("/api/teleop/left/connect")
    assert connect_response.status_code == 200
    assert connect_response.json()["data"]["connected"] is True

    client.post("/api/teleop/left/disconnect")

    command_names = [name for name, _payload in fake_hal.commands]
    assert "motion.enable_side" in command_names
    assert "motion.home_origin_side" not in command_names
    assert "motion.home_all" not in command_names


def test_startup_stops_stale_native_teleop_when_no_logical_hands_connected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict]] = []

        async def health(self) -> HalHealth:
            return HalHealth(
                ltdmc_ok=True,
                omega7_ok=True,
                version="fake-hal",
                uptime_s=1.0,
                connected=True,
                mode="real",
            )

        async def command(self, name: str, payload: dict | None = None) -> dict:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)

    with TestClient(create_app(tmp_path)):
        pass

    assert ("teleop.native.stop", {}) in fake_hal.commands


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
        assert dataset["episodes"][0]["samples"] == []
        detail_response = client.get(f"/api/datasets/native_unit_test_dataset/episodes/{episode['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["data"]["episode"]["samples"]

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
        dataset_dir = dataset_root / "native_unit_test_dataset"
        assert (dataset_dir / "data" / "chunk-000" / "file-000.parquet").exists()
        assert list((dataset_dir / "videos" / "observation.images.global").glob("chunk-*/*.mp4"))
        assert not list(dataset_dir.glob("images/**/*.png"))

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
        assert dataset["episodes"][0]["samples"] == []
        episode_id = save_response.json()["data"]["episode"]["id"]
        detail_response = client.get(f"/api/datasets/created_native_dataset/episodes/{episode_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["data"]["episode"]["samples"]

        finish_response = client.post("/api/record/session/finish")
        assert finish_response.status_code == 200


def test_manual_axis_move_rejects_unsafe_step(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 20001, "speedMode": "fine"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MOTION_UNAVAILABLE"


def test_manual_axis_move_ignores_translation_soft_limit_target(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
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
                "pulses": [-45.0] + [0.0] * 11,
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

    assert response.status_code == 200


def test_manual_axis_move_uses_hardware_zero_work_limit_for_rotation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    client = TestClient(create_app(tmp_path))
    config = default_config()
    config["hal"]["mode"] = "real"
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    config["motion"]["origin"] = {
        "valid": False,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rotationWorkLimits"] = {
        "enabled": True,
        "left": {
            "roll": {"min": -1.0, "max": 1.0},
            "pitch": {"min": -100.0, "max": 100.0},
            "yaw": {"min": -7.0, "max": 7.0},
        },
        "right": {
            "roll": {"min": -100.0, "max": 100.0},
            "pitch": {"min": -100.0, "max": 100.0},
            "yaw": {"min": -7.0, "max": 7.0},
        },
    }
    assert client.put("/api/settings", json=config).status_code == 200

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0, 0.0, 0.0, 2500.0, 0.0, 0.0] + [0.0] * 6,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    client.app.state.commands.hal = fake_hal

    blocked_response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "Roll", "direction": 1, "step": 1.0, "speedMode": "fine"},
    )

    assert blocked_response.status_code == 503
    assert "left Roll target exceeds soft limit" in blocked_response.json()["detail"]["message"]
    assert fake_hal.commands == []

    recovery_response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "Roll", "direction": -1, "step": 1.0, "speedMode": "fine"},
    )

    assert recovery_response.status_code == 200
    assert fake_hal.commands[-1][0] == "motion.manual_axis_move"


def test_manual_axis_move_uses_hardware_zero_yaw_work_window(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    client = TestClient(create_app(tmp_path))
    config = default_config()
    config["hal"]["mode"] = "real"
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    config["motion"]["origin"] = {
        "valid": False,
        "leftValid": True,
        "rightValid": False,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 1,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rotationWorkLimits"] = {
        "enabled": True,
        "left": {
            "roll": {"min": -100.0, "max": 100.0},
            "pitch": {"min": -100.0, "max": 100.0},
            "yaw": {"min": -7.0, "max": 7.0},
        },
        "right": {
            "roll": {"min": -100.0, "max": 100.0},
            "pitch": {"min": -100.0, "max": 100.0},
            "yaw": {"min": -7.0, "max": 7.0},
        },
    }
    assert client.put("/api/settings", json=config).status_code == 200

    class FakeHal:
        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0, 0.0, 0.0, 0.0, 0.0, -26_666.0] + [0.0] * 6,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    client.app.state.commands.hal = FakeHal()

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "Yaw", "direction": 1, "step": 1.0, "speedMode": "fine"},
    )

    assert response.status_code == 503
    assert "left Yaw target exceeds soft limit" in response.json()["detail"]["message"]


def test_manual_axis_move_requires_hardware_zero_not_work_origin_for_rotation_work_limit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    client = TestClient(create_app(tmp_path))
    config = default_config()
    config["hal"]["mode"] = "real"
    config["motion"]["origin"]["leftValid"] = False
    config["motion"]["origin"]["valid"] = False
    config["motion"]["homeReference"]["leftValid"] = True
    config["motion"]["homeReference"]["valid"] = True
    config["motion"]["homeReference"]["leftPulse"] = [0.0] * 6
    config["motion"]["leftSoftLimits"]["yaw"] = {"min": -8000.0, "max": 8000.0}
    config["motion"]["rotationWorkLimits"] = {
        "enabled": True,
        "left": {
            "roll": {"min": -1.0, "max": 1.0},
            "pitch": {"min": -100.0, "max": 100.0},
            "yaw": {"min": -7.0, "max": 7.0},
        },
        "right": {
            "roll": {"min": -100.0, "max": 100.0},
            "pitch": {"min": -100.0, "max": 100.0},
            "yaw": {"min": -7.0, "max": 7.0},
        },
    }
    assert client.put("/api/settings", json=config).status_code == 200

    class FakeHal:
        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            return {"command": name, "payload": payload or {}}

    client.app.state.commands.hal = FakeHal()

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "Yaw", "direction": 1, "step": 0.1, "speedMode": "fine"},
    )

    assert response.status_code == 200

    config = client.get("/api/settings").json()
    config["motion"]["homeReference"]["leftValid"] = False
    config["motion"]["homeReference"]["valid"] = False
    assert client.put("/api/settings", json=config).status_code == 200

    missing_reference_response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "Yaw", "direction": 1, "step": 0.1, "speedMode": "fine"},
    )

    assert missing_reference_response.status_code == 503
    assert "home_reference_missing" in missing_reference_response.json()["detail"]["message"]


def test_manual_axis_move_allows_return_toward_limit_when_already_outside(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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
    config["motion"]["leftSoftLimits"]["x"] = {"min": 0.0, "max": 10000.0}
    assert client.put("/api/settings", json=config).status_code == 200

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict]] = []

        async def motion_state(self) -> dict:
            return {
                "positions": [0.0] * 12,
                "pulses": [5000.0] + [0.0] * 11,
                "enabled": [True] * 12,
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict:
            self.commands.append((name, payload or {}))
            return {"command": name, "payload": payload or {}}

    fake_hal = FakeHal()
    client.app.state.commands.hal = fake_hal

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "left", "axis": "X", "direction": 1, "step": 500, "speedMode": "fine"},
    )

    assert response.status_code == 200
    assert fake_hal.commands[-1][0] == "motion.manual_axis_move"


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


def test_camera_open_attempt_logs_backend_fallback(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
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
    messages = [entry.msg for entry in client.app.state.logs.list_entries()]
    assert any("event=camera_mapping" in message and "role=global" in message for message in messages)
    assert any(
        "event=camera_open_attempt" in message
        and "backend=CAP_MSMF" in message
        and "openRet=false" in message
        for message in messages
    )
    assert any(
        "event=camera_open_attempt" in message
        and "backend=CAP_DSHOW" in message
        and "openRet=true" in message
        for message in messages
    )
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


def test_camera_drop_defers_release_while_reader_is_still_reading(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_CAMERA_RELEASE_JOIN_SEC", "0.1")
    driver = OpenCVCameraDriver()
    entered_read = Event()
    finished_read = Event()
    release_observed = Event()

    class FakeCapture:
        def __init__(self, index: int, backend: int) -> None:
            self.index = index
            self.backend = backend
            self.released_during_read = False

        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: int) -> None:
            _ = (prop, value)

        def get(self, prop: int) -> float:
            _ = prop
            return 30.0

        def read(self) -> tuple[bool, object | None]:
            entered_read.set()
            time.sleep(0.6)
            finished_read.set()
            return False, None

        def release(self) -> None:
            self.released_during_read = entered_read.is_set() and not finished_read.is_set()
            release_observed.set()

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

    capture = driver._get_capture(FakeCv2, 0, 640, 480, 30)  # noqa: SLF001
    assert capture is not None
    assert entered_read.wait(timeout=1.0)

    assert driver._drop_capture(0) is False  # noqa: SLF001
    assert release_observed.is_set() is False

    assert finished_read.wait(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        reader = driver._reader_threads.get(0)  # noqa: SLF001
        if reader is None or not reader.is_alive():
            break
        time.sleep(0.01)
    assert driver._drop_capture(0) is True  # noqa: SLF001

    assert release_observed.is_set()
    assert capture.released_during_read is False


def test_camera_process_capture_reopens_after_worker_exit(monkeypatch: MonkeyPatch) -> None:
    driver = OpenCVCameraDriver()
    captures: list[FakeProcessCapture] = []

    class FakeProcessCapture:
        is_process_capture = True

        def __init__(self) -> None:
            self.alive = True
            self.released = False
            captures.append(self)

        def isOpened(self) -> bool:
            return self.alive

        def get(self, prop: int) -> float:
            _ = prop
            return 30.0

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
        def VideoCapture(index: int, backend: int) -> object:
            raise AssertionError(f"in-process VideoCapture should not be used for {index}/{backend}")

        @staticmethod
        def VideoWriter_fourcc(a: str, b: str, c: str, d: str) -> int:
            _ = (a, b, c, d)
            return 1

    monkeypatch.setattr(driver, "_process_capture_enabled", lambda cv2, candidates: True, raising=False)
    monkeypatch.setattr(
        driver,
        "_start_process_capture",
        lambda cv2, index, width, height, fps, camera, profile, candidates: FakeProcessCapture(),
        raising=False,
    )

    first = driver._get_capture(FakeCv2, 0, 640, 480, 30, "global", default_config())  # noqa: SLF001
    assert first is captures[0]
    captures[0].alive = False

    second = driver._get_capture(FakeCv2, 0, 640, 480, 30, "global", default_config())  # noqa: SLF001

    assert second is captures[1]
    assert captures[0].released is True


def test_camera_process_capture_falls_back_when_worker_unavailable(monkeypatch: MonkeyPatch) -> None:
    driver = OpenCVCameraDriver()
    captures: list[FakeCapture] = []
    attempts: list[tuple[int, int]] = []

    class FakeCapture:
        def __init__(self, index: int, backend: int) -> None:
            self.index = index
            self.backend = backend
            captures.append(self)

        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: int | float) -> None:
            _ = (prop, value)

        def get(self, prop: int) -> float:
            if prop == FakeCv2.CAP_PROP_FRAME_WIDTH:
                return 640.0
            if prop == FakeCv2.CAP_PROP_FRAME_HEIGHT:
                return 480.0
            if prop == FakeCv2.CAP_PROP_FPS:
                return 30.0
            return 0.0

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
            attempts.append((index, backend))
            return FakeCapture(index, backend)

        @staticmethod
        def VideoWriter_fourcc(a: str, b: str, c: str, d: str) -> int:
            _ = (a, b, c, d)
            return 1

        @staticmethod
        def imencode(ext: str, frame: object, options: list[int]) -> tuple[bool, bytes]:
            _ = (ext, frame, options)
            return True, b"\xff\xd8fake-jpeg\xff\xd9"

    monkeypatch.setattr(driver, "_process_capture_enabled", lambda cv2, candidates: True, raising=False)
    monkeypatch.setattr(
        driver,
        "_start_process_capture",
        lambda cv2, index, width, height, fps, camera, profile, candidates: None,
        raising=False,
    )

    capture = driver._get_capture(FakeCv2, 0, 640, 480, 30, "global", default_config())  # noqa: SLF001

    assert capture is captures[0]
    assert attempts[0] == (0, FakeCv2.CAP_DSHOW)
    assert driver._capture_backend_labels[0] == "CAP_DSHOW fallback"  # noqa: SLF001
    driver._drop_capture(0)  # noqa: SLF001


def test_camera_process_capture_starts_module_subprocess(monkeypatch: MonkeyPatch) -> None:
    driver = OpenCVCameraDriver()
    popen_calls: list[dict[str, Any]] = []
    stdin_writes: list[str] = []

    class FakeStdin:
        def write(self, text: str) -> None:
            stdin_writes.append(text)

        def flush(self) -> None:
            return None

    class FakeStdout:
        def __iter__(self) -> object:
            yield json.dumps(
                {
                    "type": "status",
                    "ok": True,
                    "opened": True,
                    "backend": "CAP_DSHOW",
                    "actualWidth": 640,
                    "actualHeight": 480,
                    "actualFps": 30,
                }
            ) + "\n"

        def close(self) -> None:
            return None

    class FakePopen:
        def __init__(self, args: list[str], **kwargs: Any) -> None:
            popen_calls.append({"args": args, "kwargs": kwargs})
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = object()
            self.pid = 12345
            self._alive = True

        def poll(self) -> int | None:
            return None if self._alive else 0

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            self._alive = False
            return 0

        def terminate(self) -> None:
            self._alive = False

    class FakeCv2:
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5

    monkeypatch.setattr("backend.drivers.camera_opencv.subprocess.Popen", FakePopen)

    capture = driver._start_process_capture(  # noqa: SLF001
        FakeCv2,
        1,
        640,
        480,
        30.0,
        "global",
        None,
        [(700, "CAP_DSHOW")],
    )

    assert capture is not None
    args = popen_calls[0]["args"]
    assert args[1:3] == ["-m", "backend.workers.camera_capture_worker"]
    startup = json.loads(args[3])
    assert startup["index"] == 1
    assert startup["backendCandidates"] == [[700, "CAP_DSHOW"]]
    capture.release()
    assert stdin_writes == ["stop\n"]


def test_camera_reader_initializes_com_around_directshow_reads(monkeypatch: MonkeyPatch) -> None:
    driver = OpenCVCameraDriver()
    events: list[str] = []
    read_observed = Event()
    uninit_observed = Event()

    def fake_com_init() -> str:
        events.append("init")
        return "token"

    def fake_com_uninit(token: object) -> None:
        events.append(f"uninit:{token}")
        uninit_observed.set()

    class FakeCapture:
        def isOpened(self) -> bool:
            return True

        def set(self, prop: int, value: int) -> None:
            _ = (prop, value)

        def get(self, prop: int) -> float:
            _ = prop
            return 30.0

        def read(self) -> tuple[bool, object]:
            read_observed.set()
            time.sleep(0.01)
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

    monkeypatch.setattr(
        "backend.drivers.camera_opencv._co_initialize_for_capture_thread",
        fake_com_init,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.drivers.camera_opencv._co_uninitialize_for_capture_thread",
        fake_com_uninit,
        raising=False,
    )

    assert driver._get_capture(FakeCv2, 0, 640, 480, 30) is not None  # noqa: SLF001
    assert read_observed.wait(timeout=1.0)

    assert driver._drop_capture(0) is True  # noqa: SLF001
    assert uninit_observed.wait(timeout=1.0)
    assert events[0] == "init"
    assert events[-1] == "uninit:token"


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


def test_camera_candidate_indices_skip_software_directshow_sources(monkeypatch: MonkeyPatch) -> None:
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(
        driver,
        "_camera_identities_by_index",
        lambda: {
            0: {
                "name": "Software Camera",
                "devicePath": "",
                "displayName": "@device:sw:{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\{A3FCE0F5}",
            },
            1: {
                "name": "UVC Camera",
                "devicePath": "\\\\?\\usb#vid_1d6b&pid_0102&mi_00#6&1e9a8698&0&0000#{guid}\\global",
                "displayName": "@device:pnp:\\\\?\\usb#vid_1d6b&pid_0102&mi_00#6&1e9a8698&0&0000#{guid}\\global",
            },
            2: {
                "name": "WN Camera",
                "devicePath": "\\\\?\\usb#vid_0edc&pid_3080&mi_00#7&38b4ea25&0&0000#{guid}\\global",
                "displayName": "@device:pnp:left",
            },
        },
    )

    assert driver._candidate_camera_indices(3) == [1, 2]  # noqa: SLF001


def test_camera_resolution_remaps_configured_software_source(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    _clear_camera_identities(config)
    config["cameras"]["global"] = "AR0234 / index 1"
    config["cameras"]["wristLeft"] = "IMX258 / index 2"
    config["cameras"]["wristRight"] = "IMX258 / index 0"
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
                "name": "Software Camera",
                "devicePath": "",
                "displayName": "@device:sw:{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\{A3FCE0F5}",
            },
            3: {
                "name": "WN Camera",
                "devicePath": "\\\\?\\usb#vid_0edc&pid_3080&mi_00#7&38b4ea25&0&0000#{guid}\\global",
                "displayName": "@device:pnp:left",
            },
        },
    )

    resolved = driver._resolved_indices(object(), config, 30, max_index=4)  # noqa: SLF001

    assert resolved == {"global": 1, "wrist_left": 3, "wrist_right": 0}


def test_camera_resolution_does_not_remap_to_unknown_index(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    _clear_camera_identities(config)
    config["cameras"]["global"] = "AR0234 / index 1"
    config["cameras"]["wristLeft"] = "IMX258 / index 2"
    config["cameras"]["wristRight"] = "IMX258 / index 0"
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
                "name": "Software Camera",
                "devicePath": "",
                "displayName": "@device:sw:{860BB310-5D01-11D0-BD3B-00A0C911CE86}\\{A3FCE0F5}",
            },
        },
    )

    resolved = driver._resolved_indices(object(), config, 30, max_index=4)  # noqa: SLF001

    assert resolved == {"global": 1, "wrist_left": -1, "wrist_right": 0}


def test_camera_resolution_cache_serializes_concurrent_mapping_logs(monkeypatch: MonkeyPatch) -> None:
    class CapturingLogs:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        def event(self, _prefix: str, _level: str, event: str, **fields: Any) -> None:
            self.events.append((event, fields))

    config = default_config()
    _clear_camera_identities(config)
    config["cameras"]["global"] = "AR0234 / index 1"
    config["cameras"]["wristLeft"] = "IMX258 / index 2"
    config["cameras"]["wristRight"] = "IMX258 / index 0"
    logs = CapturingLogs()
    driver = OpenCVCameraDriver(logs)
    gate = Barrier(3)
    gate_used = Event()

    def slow_identities() -> dict[int, dict[str, str]]:
        if not gate_used.is_set():
            try:
                gate.wait(timeout=1.0)
            except BrokenBarrierError:
                pass
            gate_used.set()
        time.sleep(0.02)
        return {
            0: {"name": "WN Camera", "devicePath": "right", "displayName": "@device:pnp:right"},
            1: {"name": "UVC Camera", "devicePath": "global", "displayName": "@device:pnp:global"},
            2: {"name": "WN Camera", "devicePath": "left", "displayName": "@device:pnp:left"},
        }

    monkeypatch.setattr(driver, "_camera_identities_by_index", slow_identities)

    results: list[dict[str, int]] = []
    threads = [
        Thread(target=lambda: results.append(driver._resolved_indices(object(), config, 30, max_index=4)))
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [
        {"global": 1, "wrist_left": 2, "wrist_right": 0},
        {"global": 1, "wrist_left": 2, "wrist_right": 0},
        {"global": 1, "wrist_left": 2, "wrist_right": 0},
    ]
    mapping_events = [event for event, _fields in logs.events if event == "camera_mapping"]
    assert len(mapping_events) == 3


def test_global_camera_auto_index_uses_remaining_device(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    _clear_camera_identities(config)
    config["cameras"]["global"] = "Global UVC / index -1"
    config["cameras"]["wristLeft"] = "IMX258 / index 2"
    config["cameras"]["wristRight"] = "IMX258 / index 0"
    driver = OpenCVCameraDriver()
    monkeypatch.setattr(driver, "_camera_identities_by_index", lambda: {})

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
    config["cameras"]["wristRight"] = "IMX258 / index -1"
    config["cameras"]["wristRightIdentity"] = ""
    driver = OpenCVCameraDriver()

    monkeypatch.setattr(
        driver,
        "_camera_identities_by_index",
        lambda: {
            0: {
                "name": "Software Camera",
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


def test_current_camera_identity_mapping_binds_reenumerated_wrist_roles(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    driver = OpenCVCameraDriver()

    monkeypatch.setattr(
        driver,
        "_camera_identities_by_index",
        lambda: {
            0: {
                "name": "USB Camera",
                "devicePath": "\\\\?\\usb#vid_0abd&pid_8050&mi_00#7&398f0a3&0&0000#{guid}\\global",
                "displayName": "@device:pnp:left",
            },
            1: {
                "name": "USB Camera",
                "devicePath": "\\\\?\\usb#vid_0abd&pid_8050&mi_00#7&1396f44d&0&0000#{guid}\\global",
                "displayName": "@device:pnp:global",
            },
            2: {
                "name": "USB Camera",
                "devicePath": "\\\\?\\usb#vid_0abd&pid_8050&mi_00#8&3724732e&0&0000#{guid}\\global",
                "displayName": "@device:pnp:right",
            },
        },
    )

    resolved = driver._resolved_indices(object(), config, 30)  # noqa: SLF001

    assert resolved == {"global": 1, "wrist_left": 0, "wrist_right": 2}


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
                "name": "USB Camera",
                "devicePath": "\\\\?\\usb#vid_0abd&pid_8050&mi_00#7&398f0a3&0&0000#{guid}\\global",
                "displayName": "@device:pnp:right",
            },
            1: {
                "name": "USB Camera",
                "devicePath": "\\\\?\\usb#vid_0abd&pid_8050&mi_00#7&1396f44d&0&0000#{guid}\\global",
                "displayName": "@device:pnp:global",
            },
            2: {
                "name": "USB Camera",
                "devicePath": "\\\\?\\usb#vid_0abd&pid_8050&mi_00#8&3724732e&0&0000#{guid}\\global",
                "displayName": "@device:pnp:right",
            },
        },
    )

    resolved = driver._resolved_indices(object(), config, 30)

    assert resolved == {"global": 1, "wrist_left": 0, "wrist_right": 2}


def test_default_camera_mapping_matches_deployment_hardware() -> None:
    config = default_config()

    assert config["cameras"]["global"] == "IMX335 / index 1"
    assert config["cameras"]["globalIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000"
    assert config["cameras"]["wristLeft"] == "IMX335 / index 0"
    assert config["cameras"]["wristLeftIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000"
    assert config["cameras"]["wristRight"] == "IMX335 / index 2"
    assert config["cameras"]["wristRightIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\8&3724732E&0&0000"
    assert config["cameras"]["previewResolution"] == "640x480"
    assert config["cameras"]["globalResolution"] == "640x480"
    assert config["cameras"]["wristLeftResolution"] == "640x480"
    assert config["cameras"]["wristRightResolution"] == "640x480"
    assert config["cameras"]["tuning"]["global"]["exposure"] == -5.5
    assert config["cameras"]["tuning"]["wrist_left"]["exposure"] == -6.0
    assert config["cameras"]["tuning"]["wrist_right"]["exposure"] == -6.0


def test_camera_tuning_allows_manual_wrist_exposure() -> None:
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
        "autoExposure": True,
        "exposure": -3.0,
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


def test_camera_tuning_apply_saves_config_off_event_loop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path))
    calls: list[str] = []

    def fake_save_config(config: dict[str, Any], emit_log: bool = True) -> dict[str, Any]:
        _ = emit_log
        calls.append("save_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return config
        raise AssertionError("camera tuning save_config ran on the event loop")

    def fake_apply(config: dict[str, Any], camera: str) -> dict[str, object]:
        return {
            "camera": camera,
            "index": 1,
            "profile": config["cameras"]["tuning"][camera],
            "actual": {"exposure": -6.5},
        }

    app_state = _app_state(client)
    monkeypatch.setattr(app_state.settings, "save_config", fake_save_config)
    monkeypatch.setattr(app_state.hardware.cameras, "apply_tuning", fake_apply)
    config = client.get("/api/settings").json()
    config["cameras"]["tuning"]["global"]["exposure"] = -6.5

    response = client.post("/api/cameras/global/tuning/apply", json=config)

    assert response.status_code == 200
    assert response.json()["data"]["profile"]["exposure"] == -6.5
    assert calls == ["save_config"]


def test_pico_endpoints_run_driver_methods_off_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    client = TestClient(create_app(tmp_path))
    calls: list[str] = []

    def fake_pico_call(method_name: str) -> PicoResult:
        calls.append(method_name)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return PicoResult(True, f"{method_name} ok")
        raise AssertionError(f"pico {method_name} ran on the event loop")

    app_state = _app_state(client)
    monkeypatch.setattr(app_state.hardware.pico, "connect", lambda _config: fake_pico_call("connect"))
    monkeypatch.setattr(app_state.hardware.pico, "start_vision", lambda _config: fake_pico_call("start_vision"))
    monkeypatch.setattr(app_state.hardware.pico, "stop_vision", lambda _config: fake_pico_call("stop_vision"))
    monkeypatch.setattr(app_state.hardware.pico, "status", lambda _config: fake_pico_call("status"))

    responses = [
        client.post("/api/pico/adb/connect"),
        client.post("/api/pico/vision/start"),
        client.post("/api/pico/vision/stop"),
        client.post("/api/pico/status/check"),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert calls == ["connect", "start_vision", "stop_vision", "status"]


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


def test_camera_driver_fps_uses_windowed_samples_to_ignore_single_interval_jitter() -> None:
    driver = OpenCVCameraDriver()

    for frame_index in range(31):
        driver._record_frame_timestamp(0, frame_index / 30.0)  # noqa: SLF001
    driver._record_frame_timestamp(0, 1.001)  # noqa: SLF001

    assert driver._latest_fps[0] < 35.0  # noqa: SLF001


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
    bodies: list[dict[str, Any]] = []

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
            calls.append((method, path))
            body = kwargs.get("body")
            if body is None and args:
                body = args[0]
            if isinstance(body, bytes):
                bodies.append(json.loads(body.decode("utf-8")))

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("backend.hal_client.client.http.client.HTTPConnection", FakeConnection)
    client = RealHalClient("http://127.0.0.1:8091", 5000, LogService())

    asyncio.run(
        client.command(
            "motion.teleop_target_update",
            {"side": "left", "deltas": {"X": 12.5, "Yaw": -0.2}},
        )
    )
    asyncio.run(client.command("motion.teleop_stop_side", {"side": "left"}))
    asyncio.run(client.command("motion.home_origin_side", {"side": "right", "pulse": [0, 1, 2, 3, 4, 5]}))

    assert calls == [
        ("POST", "/motion/teleop_target_update"),
        ("POST", "/motion/teleop_stop_side"),
        ("POST", "/motion/home_origin_side"),
    ]
    assert bodies[0]["X"] == 12.5
    assert bodies[0]["Yaw"] == -0.2


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


def test_real_hal_client_uses_long_no_retry_policy_for_motion_home(monkeypatch: MonkeyPatch) -> None:
    timeouts: list[float] = []
    requests: list[tuple[str, str]] = []

    class FakeConnection:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            _ = (host, port)
            timeouts.append(timeout)
            self.sock = type("FakeSocket", (), {"setsockopt": staticmethod(lambda *a, **k: None)})()

        def connect(self) -> None:
            return None

        def request(self, method: str, path: str, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)
            requests.append((method, path))

        def getresponse(self) -> object:
            raise TimeoutError("home command still running")

        def close(self) -> None:
            return None

    monkeypatch.setattr("backend.hal_client.client.http.client.HTTPConnection", FakeConnection)
    client = RealHalClient("http://127.0.0.1:8091", 5000, LogService())

    with pytest.raises(RuntimeError, match="home command still running"):
        asyncio.run(
            client.command(
                "motion.home_origin_side",
                {"side": "left", "pulse": [0.0] * 6, "enabledAxes": [True] * 6},
            )
        )

    assert timeouts == [75.0]
    assert requests == [("POST", "/motion/home_origin_side")]


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

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
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
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/stability/start",
            json={"durationS": 0.08, "samplePeriodS": 0.02, "includeCameras": False, "includeForce": True},
        )
        assert response.status_code == 200
        assert response.json()["data"]["active"] is True
        status = {}
        for _ in range(50):
            status_response = client.get("/api/stability/status")
            assert status_response.status_code == 200
            status = status_response.json()["data"]
            if not status["active"] and status["hal"]["connectedSamples"] > 0:
                break
            time.sleep(0.02)

    assert status["active"] is False
    assert status["hal"]["connectedSamples"] > 0
    assert status["force"]["skippedTestMode"] > 0


def test_stability_monitor_samples_read_config_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    with TestClient(create_app(tmp_path)) as client:
        app_state = _app_state(client)
        original_get_config = app_state.settings.get_config
        calls: list[str] = []

        def guarded_get_config() -> dict[str, Any]:
            calls.append("get_config")
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return original_get_config()
            raise AssertionError("stability monitor read config on the event loop")

        monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)

        response = client.post(
            "/api/stability/start",
            json={"durationS": 0.08, "samplePeriodS": 0.02, "includeCameras": False, "includeForce": True},
        )
        assert response.status_code == 200
        status = {}
        for _ in range(50):
            status_response = client.get("/api/stability/status")
            assert status_response.status_code == 200
            status = status_response.json()["data"]
            if not status["active"] and calls:
                break
            time.sleep(0.02)

        assert status["errors"] == []
        assert calls


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


def test_policy_auto_routes_read_config_off_event_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    app_state = _app_state(client)
    original_get_config = app_state.settings.get_config
    calls: list[str] = []

    def guarded_get_config() -> dict[str, Any]:
        calls.append("get_config")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_get_config()
        raise AssertionError("policy route read config on the event loop")

    monkeypatch.setattr(app_state.settings, "get_config", guarded_get_config)

    status_response = client.get("/api/auto/status")
    start_response = client.post("/api/auto/start", json={"modelId": "act"})
    action_response = client.post(
        "/api/auto/action",
        json={"side": "left", "axis": "X", "direction": 1, "step": 50, "speedMode": "fine"},
    )
    dispatch_response = client.post("/api/auto/dispatch_next")

    assert status_response.status_code == 200
    assert start_response.status_code == 200
    assert action_response.status_code == 200
    assert dispatch_response.status_code == 200
    assert calls


def test_emergency_stop_clears_auto_policy_queue(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    client = TestClient(create_app(tmp_path))

    start_response = client.post("/api/auto/start", json={"modelId": "act"})
    assert start_response.status_code == 200

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
    assert action_response.json()["data"]["status"]["queueDepth"] == 1

    emergency_response = client.post("/api/motion/emergency_stop")
    assert emergency_response.status_code == 200

    status_response = client.get("/api/auto/status")
    assert status_response.status_code == 200
    status = status_response.json()["data"]
    assert status["running"] is False
    assert status["queueDepth"] == 0


def test_acknowledge_safety_restores_enable_snapshot_without_origin_move(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FakeHal:
        def __init__(self) -> None:
            self.commands: list[tuple[str, dict[str, Any]]] = []
            self.enabled = [False] * 12

        async def motion_state(self) -> dict[str, Any]:
            return {
                "positions": [0.0] * 12,
                "pulses": [0.0] * 12,
                "enabled": list(self.enabled),
                "estop_active": False,
            }

        async def command(self, name: str, payload: dict | None = None) -> dict[str, Any]:
            payload = payload or {}
            self.commands.append((name, payload))
            if name == "motion.emergency_stop":
                self.enabled = [False] * 12
            if name == "motion.enable_side":
                start = 0 if payload["side"] == "left" else 6
                axes = list(payload["enabledAxes"])
                self.enabled[start : start + 6] = axes
            return {"command": name, "payload": payload}

    fake_hal = FakeHal()
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: fake_hal)
    client = TestClient(create_app(tmp_path))
    client.app.state.telemetry.set_motion_axis_enabled("left", [True, False, True, False, False, False])
    client.app.state.telemetry.set_motion_axis_enabled("right", [True, True, False, False, False, False])
    config = client.app.state.settings.get_config()
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
    }
    client.app.state.settings.save_config(config, emit_log=False)
    origin_before = json.loads(json.dumps(client.app.state.settings.get_config()["motion"]["origin"]))

    emergency_response = client.post("/api/motion/emergency_stop")
    assert emergency_response.status_code == 200

    acknowledge_response = client.post("/api/motion/safety/acknowledge")

    assert acknowledge_response.status_code == 200
    assert fake_hal.commands == [
        ("motion.emergency_stop", {}),
        ("motion.enable_side", {"side": "left", "enabledAxes": [True, False, True, False, False, False]}),
        ("motion.enable_side", {"side": "right", "enabledAxes": [True, True, False, False, False, False]}),
    ]
    assert client.app.state.settings.get_config()["motion"]["origin"] == origin_before


def test_real_hal_client_logs_motion_error_on_http_failure() -> None:
    logs = LogService(emit_startup=False)
    client = RealHalClient("http://127.0.0.1:9", 1, logs)

    with pytest.raises(RuntimeError):
        asyncio.run(client.command("motion.manual_axis_move", {"side": "left", "axis": "Yaw"}))

    message = next(entry.msg for entry in logs.list_entries() if "event=motion_error" in entry.msg)
    assert "api=motion.manual_axis_move" in message
    assert "ret=http_error" in message


def test_omega_state_poll_logs_device_summary(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    class FakeHal:
        async def health(self) -> HalHealth:
            return HalHealth(ltdmc_ok=True, omega7_ok=True, version="test", uptime_s=1.0)

        async def motion_state(self) -> dict[str, Any]:
            return {"positions": [0.0] * 12, "pulses": [0.0] * 12, "enabled": [True] * 12}

        async def omega_state(self) -> dict[str, Any]:
            return {
                "hands": [
                    {
                        "side": "left",
                        "openId": 0,
                        "deviceId": 3,
                        "serial": "L",
                        "connected": True,
                        "leftHanded": True,
                        "lastReadOk": True,
                    },
                    {
                        "side": "right",
                        "openId": 1,
                        "deviceId": 4,
                        "serial": "R",
                        "connected": True,
                        "leftHanded": False,
                        "lastReadOk": True,
                    },
                ]
            }

        async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            _ = (name, payload)
            return {}

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: FakeHal())
    with TestClient(create_app(tmp_path)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()

        messages = [entry.msg for entry in client.app.state.logs.list_entries()]
    assert any(
        "event=omega_device" in message and "side=left" in message and "deviceId=3" in message
        for message in messages
    )
