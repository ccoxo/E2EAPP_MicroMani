from types import SimpleNamespace

import pytest

from backend.core.defaults import default_config
from backend.services.dataset_recorder import DatasetRecorderService


def recorder_at(path, session):
    config = default_config()
    recorder = DatasetRecorderService(
        settings=SimpleNamespace(get_config=lambda: config),
        hardware=SimpleNamespace(), hal=SimpleNamespace(),
        telemetry=SimpleNamespace(recording=False),
        logs=SimpleNamespace(), teleop=SimpleNamespace(status=lambda: {}),
    )
    recorder._dataset_dir = path
    recorder._session_id = session
    return recorder


def test_resume_save_discard_and_restart_counts(tmp_path):
    recorder = recorder_at(tmp_path, "old")
    rows = [{"id": f"episode_{i:06d}", "episodeIndex": i,
             "session": "old", "deleted": i == 4, "status": "review"} for i in range(21)]
    recorder._write_episodes(tmp_path, rows)
    assert recorder.status()["savedEpisodes"] == 20

    resumed = recorder_at(tmp_path, "new")
    resumed._update_record_progress(tmp_path, resumed._read_episodes(tmp_path))
    assert resumed.status()["savedEpisodes"] == 20
    assert resumed.status()["sessionSavedEpisodes"] == 0
    rows.extend({"id": f"episode_{i:06d}", "episodeIndex": i,
                 "session": "new", "deleted": False, "status": "review"} for i in range(21, 31))
    resumed._write_episodes(tmp_path, rows)
    resumed._episode_index = 31
    resumed._mark_saved_episode_deleted_locked(rows[27])
    status = resumed.status()
    assert (status["episodeIndex"], status["savedEpisodes"], status["sessionSavedEpisodes"]) == (31, 29, 9)

    # 重复标记不重复扣数；状态查询只读取内存，保留编号。
    resumed._mark_saved_episode_deleted_locked(rows[27])
    resumed._read_episodes = lambda _: pytest.fail("status must not read episode files")
    assert resumed.status()["savedEpisodes"] == 29
    restarted = recorder_at(tmp_path, "next")
    restarted._update_record_progress(tmp_path, restarted._read_episodes(tmp_path))
    assert restarted.status()["savedEpisodes"] == 29
    assert restarted.status()["sessionSavedEpisodes"] == 0


def test_unrelated_dataset_does_not_change_active_counts(tmp_path):
    recorder = recorder_at(tmp_path / "active", "current")
    recorder._write_episodes(tmp_path / "active", [{"session": "current", "deleted": False}])
    recorder._write_episodes(tmp_path / "other", [])
    assert recorder.status()["savedEpisodes"] == 1
    assert recorder.status()["sessionSavedEpisodes"] == 1


def test_failed_metadata_write_does_not_publish_new_count(tmp_path, monkeypatch):
    recorder = recorder_at(tmp_path, "current")
    recorder._write_episodes(tmp_path, [])
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(type(tmp_path), "write_text", fail)
    with pytest.raises(OSError):
        recorder._write_episodes(tmp_path, [{"session": "current"}])
    assert recorder.status()["savedEpisodes"] == 0


def test_new_dataset_starts_empty(tmp_path):
    recorder = recorder_at(tmp_path, "new")
    recorder._update_record_progress(tmp_path, recorder._read_episodes(tmp_path))
    assert recorder.status()["savedEpisodes"] == 0
    assert recorder.status()["sessionSavedEpisodes"] == 0
