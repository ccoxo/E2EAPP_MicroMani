"""只使用临时元数据和替身验证续录，不打开相机或 HAL。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from backend.core.data_contract import data_contract_metadata
from backend.core.defaults import default_config
from backend.services.dataset_recorder import DatasetRecorderService


@pytest.fixture
def recorded(tmp_path):
    config = default_config()
    config["hal"]["mode"] = "mock"
    config["motion"]["origin"].update(leftValid=True, rightValid=True)
    config["cameras"].update(globalIdentity="global-serial", wristLeftIdentity="left-serial", wristRightIdentity="right-serial")
    config["storage"]["datasetRoot"] = str(tmp_path)
    recorder = object.__new__(DatasetRecorderService)
    recorder._native_use_videos = True
    recorder._record_fps_hz = 30
    recorder._force_sample_hz = 200
    recorder._dataset_name = "dataset"
    recorder._session_id = "session-old"
    recorder._participation = None
    root = tmp_path / "dataset"
    recorder._write_appstation_info(root, config)
    info = {"fps": 30, "features": recorder._native_features(config), "total_frames": 20, "total_episodes": 2,
            "dataContract": data_contract_metadata()}
    recorder._write_json(root / "meta/info.json", info)
    recorder._write_episodes(root, [
        {"id": f"episode_{i:06d}", "episodeIndex": i, "participation": None,
         "frames": 10, "datasetFromIndex": i * 10, "datasetToIndex": (i + 1) * 10}
        for i in range(2)
    ])
    native_meta = root / "meta/episodes/chunk-000"
    native_meta.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"episode_index": i, "length": 10, "dataset_from_index": i * 10, "dataset_to_index": (i + 1) * 10, "tasks": ["task"]}
        for i in range(2)
    ]), native_meta / "file-000.parquet")
    return recorder, root, config


def change_json(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_compatible_resume_and_enumeration_changes_are_allowed(recorded):
    recorder, root, config = recorded
    config["cameras"]["wristLeft"] = "index 9"
    config["cameras"]["previewResolution"] = "320x240"
    config["motion"]["origin"]["updatedAt"] = 42
    assert recorder._dataset_resume_error(root, config) == ""


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, "NaN", True])
@pytest.mark.parametrize("old", [True, False])
def test_invalid_origin_fails_closed(recorded, value, old):
    recorder, root, config = recorded
    if old:
        change_json(root / "meta/appstation_info.json", lambda data: data["sessionOrigin"]["leftPulse"].__setitem__(0, value))
    else:
        config["motion"]["origin"]["leftPulse"][0] = value
    assert recorder._dataset_resume_error(root, config)


@pytest.mark.parametrize("mutation,reason", [
    (lambda c: c["motion"]["origin"]["leftPulse"].__setitem__(0, 100000), "原点 W 已改变"),
    (lambda c: c["storage"].__setitem__("recordFps", 25), "录制帧率"),
    (lambda c: c["cameras"].__setitem__("wristLeftIdentity", "different"), "身份或角色"),
    (lambda c: c["cameras"].__setitem__("wristLeftIdentity", "right-serial"), "身份或角色"),
    (lambda c: c["cameras"].__setitem__("wristLeftIdentity", ""), "稳定身份"),
    (lambda c: c["cameras"].__setitem__("fps", 25), "相机采集帧率"),
    (lambda c: c["cameras"].__setitem__("globalResolution", "1280x720"), "分辨率"),
    (lambda c: c["motion"]["kinematics"]["axisOrder"].reverse(), "运动标定"),
])
def test_changed_capture_conditions_are_rejected(recorded, mutation, reason):
    recorder, root, config = recorded
    mutation(config)
    assert reason in recorder._dataset_resume_error(root, config)


@pytest.mark.parametrize("name", ["info.json", "appstation_info.json"])
def test_corrupt_metadata_is_not_rebuilt_or_overwritten(recorded, name):
    recorder, root, config = recorded
    path = root / "meta" / name
    path.write_text('{"broken":', encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert "元数据" in recorder._dataset_resume_error(root, config)
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", [
    lambda data: data.pop("features"),
    lambda data: data["features"]["observation.state"].__setitem__("shape", [12]),
    lambda data: data["features"]["action"]["names"].reverse(),
    lambda data: data.__setitem__("fps", float("nan")),
    lambda data: data.pop("dataContract"),
    lambda data: data.__setitem__("total_frames", "unknown"),
])
def test_incompatible_native_metadata_is_rejected(recorded, mutation):
    recorder, root, config = recorded
    change_json(root / "meta/info.json", mutation)
    assert recorder._dataset_resume_error(root, config)


def test_participation_change_and_unproven_legacy_metadata_are_rejected(recorded):
    recorder, root, config = recorded
    recorder._participation = {"version": "appstation.participation.v1", "arms": ["right"], "grippers": ["right"]}
    assert "参与采集" in recorder._dataset_resume_error(root, config)
    recorder._participation = None
    change_json(root / "meta/appstation_info.json", lambda data: data.pop("participation"))
    assert recorder._dataset_resume_error(root, config) == ""
    episodes = recorder._read_episodes(root)
    for episode in episodes:
        episode.pop("participation")
    recorder._write_episodes(root, episodes)
    assert "参与侧" in recorder._dataset_resume_error(root, config)


def test_metadata_keeps_original_conditions_and_one_entry_per_session(recorded):
    recorder, root, config = recorded
    original = recorder._read_json(root / "meta/appstation_info.json")
    recorder._session_id = "session-new"
    config["cameras"]["tuning"]["global"]["exposure"] = -4.0
    recorder._write_appstation_info(root, config)
    recorder._write_appstation_info(root, config)
    saved = recorder._read_json(root / "meta/appstation_info.json")
    for key in ("sessionOrigin", "hardware", "recording", "createdAt"):
        assert saved[key] == original[key]
    assert [s["session"] for s in saved["sessionHistory"]] == ["session-old", "session-new"]
    newest = saved["sessionHistory"][-1]
    assert newest["firstEpisodeIndex"] == 2
    assert newest["hardware"]["cameras"]["tuning"]["global"]["exposure"] == -4.0


def test_empty_dataset_can_establish_its_first_capture_conditions(recorded):
    recorder, root, config = recorded
    (root / "meta/episodes.jsonl").unlink()
    (root / "meta/episodes/chunk-000/file-000.parquet").unlink()
    change_json(root / "meta/info.json", lambda data: data.update(total_frames=0, total_episodes=0))
    config["motion"]["origin"]["leftPulse"][0] = 123
    assert recorder._dataset_resume_error(root, config) == ""
    recorder._write_appstation_info(root, config)
    assert recorder._read_json(root / "meta/appstation_info.json")["sessionOrigin"]["leftPulse"][0] == 123


def test_claimed_existing_data_without_episode_files_is_rejected(recorded):
    recorder, root, config = recorded
    (root / "meta/episodes.jsonl").unlink()
    (root / "meta/episodes/chunk-000/file-000.parquet").unlink()
    assert "片段文件不一致" in recorder._dataset_resume_error(root, config)


@pytest.mark.parametrize("suffix", ['{"broken":', 'null\n'])
def test_damaged_episode_list_cannot_prove_legacy_participation(recorded, suffix):
    recorder, root, config = recorded
    change_json(root / "meta/appstation_info.json", lambda data: data.pop("participation"))
    path = root / "meta/episodes.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")
    before = path.read_bytes()
    assert "片段清单" in recorder._dataset_resume_error(root, config)
    assert path.read_bytes() == before


@pytest.mark.parametrize("empty", [False, True])
def test_corrupt_session_history_is_rejected_before_any_recreation(recorded, empty):
    recorder, root, config = recorded
    if empty:
        (root / "meta/episodes.jsonl").unlink()
        change_json(root / "meta/info.json", lambda data: data.update(total_frames=0, total_episodes=0))
    change_json(root / "meta/appstation_info.json", lambda data: data.update(sessionHistory={}))
    assert "会话历史损坏" in recorder._dataset_resume_error(root, config)


def test_single_operator_side_checks_only_its_corresponding_hardware_origin(recorded):
    recorder, root, config = recorded
    recorder._participation = {"version": "appstation.participation.v1", "arms": ["right"], "grippers": ["right"]}
    change_json(root / "meta/appstation_info.json", lambda data: data.update(participation=recorder._participation))
    config["motion"]["origin"].update(rightValid=False, rightPulse=[float("nan")] * 6)
    assert recorder._dataset_resume_error(root, config) == ""
    config["motion"]["origin"]["leftPulse"][0] = 100
    assert "硬件 left 侧工作原点 W 已改变" in recorder._dataset_resume_error(root, config)


def test_dataset_with_only_discarded_episode_can_resume(recorded):
    recorder, root, config = recorded
    (root / "meta/episodes/chunk-000/file-000.parquet").unlink()
    change_json(root / "meta/info.json", lambda data: data.update(total_frames=0, total_episodes=0))
    (root / "meta/episodes.jsonl").write_text(
        '{"id":"episode_000000","episodeIndex":0,"status":"discarded","participation":null}\n', encoding="utf-8"
    )
    assert recorder._dataset_resume_error(root, config) == ""


def test_start_rejects_before_writer_or_teleop_can_modify_old_dataset(recorded, monkeypatch):
    _, root, config = recorded
    config["storage"]["recordFps"] = 25
    teleop = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    recorder = DatasetRecorderService(
        SimpleNamespace(get_config=lambda: config), SimpleNamespace(), SimpleNamespace(),
        SimpleNamespace(), SimpleNamespace(), teleop,
    )
    begin = AsyncMock()
    monkeypatch.setattr(recorder, "_try_begin_native_dataset", begin)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(RuntimeError, match="录制帧率"):
        asyncio.run(recorder.start_session("dataset", "task"))
    begin.assert_not_called()
    teleop.start.assert_not_called()
    assert recorder._writer_thread is None
    assert not recorder._session_starting
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_writer_resumes_compatible_dataset_and_rechecks_drift(recorded):
    recorder, root, config = recorded
    native = SimpleNamespace(resume=Mock(return_value=SimpleNamespace()), create=Mock())
    recorder._dataset_dir = root
    recorder._dataset_id = "dataset"
    recorder._recording_config = lambda: config
    recorder._native_recording_requested = lambda: True
    recorder._native_preflight = lambda: ""
    recorder._native_imports = lambda: (native, None)
    recorder._native_writer_kwargs = lambda: {}
    recorder._configure_native_chunk_settings = lambda data: None
    assert recorder._open_native_dataset_for_writer() is native.resume.return_value
    native.resume.assert_called_once()
    native.create.assert_not_called()
    config["cameras"]["wristRightIdentity"] = "changed"
    with pytest.raises(RuntimeError, match="身份或角色"):
        recorder._open_native_dataset_for_writer()
    native.resume.assert_called_once()


def test_resume_maps_ranges_and_preserves_explicit_discard_records(recorded):
    recorder, root, config = recorded
    episodes = recorder._read_episodes(root)
    episodes[0].update(id="episode_000099", episodeIndex=99, deleted=True, status="invalid")
    recorder._write_episodes(root, episodes)
    assert recorder._dataset_resume_error(root, config) == ""
    recorder._write_episodes(root, episodes[1:])
    assert "缺少保留或弃用记录" in recorder._dataset_resume_error(root, config)


@pytest.mark.parametrize("change", [
    lambda episodes: episodes[1].update(datasetFromIndex=0, datasetToIndex=10),
    lambda episodes: episodes[0].update(frames=11),
    lambda episodes: episodes[0].update(datasetFromIndex=1),
])
def test_resume_rejects_ambiguous_or_changed_frame_ranges(recorded, change):
    recorder, root, config = recorded
    episodes = recorder._read_episodes(root)
    change(episodes)
    recorder._write_episodes(root, episodes)
    assert "索引无法核对" in recorder._dataset_resume_error(root, config)


def test_training_export_uses_ranges_and_requires_evidence_for_orphans(recorded):
    from scripts.export_retained_dataset import export_plan
    recorder, root, _ = recorded
    episodes = recorder._read_episodes(root)
    # 模拟旧版软件复用 0 号，但帧范围明确指向原生 1 号。
    retained = {**episodes[1], "id": "episode_000000", "episodeIndex": 0}
    recorder._write_episodes(root, [retained])
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="日志核实"):
        export_plan(root, [])
    plan = export_plan(root, [0])
    assert plan["keepNativeIndices"] == [1]
    assert plan["excludedNativeIndices"] == [0]
    assert plan["frames"] == 10
    with pytest.raises(ValueError, match="冲突"):
        export_plan(root, [0, 1])
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_training_export_excludes_explicit_invalid_and_deleted_records(recorded):
    from scripts.export_retained_dataset import export_plan
    recorder, root, _ = recorded
    episodes = recorder._read_episodes(root)
    episodes[0].update(deleted=True, status="invalid")
    recorder._write_episodes(root, episodes)
    assert export_plan(root, [])["keepNativeIndices"] == [1]


def test_save_discard_rerecord_restart_keeps_unique_ids_and_tombstones(tmp_path, monkeypatch):
    async def run_case():
        config = default_config()
        config["hal"]["mode"] = "mock"
        config["storage"]["datasetRoot"] = str(tmp_path)
        config["motion"]["origin"].update(leftValid=True, rightValid=True)
        config["cameras"].update(globalIdentity="global", wristLeftIdentity="left", wristRightIdentity="right")
        recorder = DatasetRecorderService(
            SimpleNamespace(get_config=lambda: config), SimpleNamespace(), SimpleNamespace(),
            SimpleNamespace(recording=False, episode_count=0, frame_count=0),
            SimpleNamespace(info=lambda *_: None, warning=lambda *_: None, error=lambda *_: None),
            SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), status=lambda: {}),
        )
        root = tmp_path / "dataset"
        native_rows = []

        async def open_dataset(_config):
            recorder._native_use_videos = True
            info = recorder._read_json(root / "meta/info.json") or {
                "fps": 30, "features": recorder._native_features(config), "dataContract": data_contract_metadata(),
                "total_frames": 0, "total_episodes": 0,
            }
            recorder._write_json(root / "meta/info.json", info)
            recorder._native_total_frames_cached = info["total_frames"]
            return True

        async def save_native():
            begin = recorder._native_total_frames_cached
            length = recorder._episode_frames
            native_rows.append({"episode_index": len(native_rows), "dataset_from_index": begin,
                                "dataset_to_index": begin + length, "length": length, "tasks": ["task"]})
            meta = root / "meta/episodes/chunk-000"
            meta.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(native_rows), meta / "file-000.parquet")
            info = recorder._read_json(root / "meta/info.json")
            info.update(total_frames=begin + length, total_episodes=len(native_rows))
            recorder._write_json(root / "meta/info.json", info)
            recorder._native_total_frames_cached = begin + length

        monkeypatch.setattr(recorder, "_try_begin_native_dataset", open_dataset)
        monkeypatch.setattr(recorder, "_save_native_episode", save_native)
        monkeypatch.setattr(recorder, "_native_writer_active", lambda: True)
        monkeypatch.setattr(recorder, "_start_sampler_tasks_locked", lambda: None)
        for name in ("_record_loop", "_frame_assembler_loop", "_refresh_gripper_cache", "_wait_for_episode_warmup"):
            monkeypatch.setattr(recorder, name, AsyncMock())
        try:
            await recorder.start_session("dataset", "task")
            recorder._episode_frames = 3
            first = (await recorder.save_episode())["episode"]
            await recorder.discard_episode()
            assert recorder._episode_index == 1
            recorder._reset_returned_sides = set(recorder._reset_required_sides_locked())
            await recorder.skip_reset()
            recorder._episode_frames = 4
            second = (await recorder.save_episode())["episode"]
            await recorder.finish_session()
            await recorder.start_session("dataset", "task")
            assert recorder._episode_index == 2
            recorder._episode_frames = 2
            third = (await recorder.save_episode())["episode"]
            saved_bytes = (root / "meta/episodes.jsonl").read_bytes()
            recorder._episode_index = 2
            with pytest.raises(RuntimeError, match="拒绝覆盖"):
                recorder._finalize_episode_locked(status="review", deleted=False)
            assert (root / "meta/episodes.jsonl").read_bytes() == saved_bytes
            recorder._episode_index = 3
            await recorder.finish_session()
        finally:
            if recorder._session_active:
                await recorder.finish_session()
        assert [first["episodeIndex"], second["episodeIndex"], third["episodeIndex"]] == [0, 1, 2]
        records = recorder._read_episodes(root)
        assert len(records) == 3
        assert records[0]["deleted"] is True and records[0]["status"] == "invalid"
        assert [e["datasetFromIndex"] for e in records] == [0, 3, 7]
        assert recorder._dataset_resume_error(root, config) == ""
        from scripts.export_retained_dataset import export_plan
        assert export_plan(root, [])["keepNativeIndices"] == [1, 2]
    asyncio.run(run_case())
