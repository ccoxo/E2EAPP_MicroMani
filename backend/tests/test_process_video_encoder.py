import time

import av
import numpy as np
import pytest

from backend.services.process_video_encoder import ProcessVideoEncoder
from backend.services.recording_gc import RecordingGcScope


def test_worker_reports_silent_drop_and_accepts_discard(monkeypatch):
    from types import SimpleNamespace
    from lerobot.datasets import video_utils
    from backend.services.process_video_encoder import _worker

    class Encoder:
        def __init__(self, **_options):
            self._dropped_frames = {}
            self._frame_queues = {}

        def feed_frame(self, _key, drop):
            if drop:
                self._dropped_frames["global"] = 1

        def cancel_episode(self):
            self._dropped_frames.clear()

        def close(self):
            pass

    monkeypatch.setattr(video_utils, "StreamingVideoEncoder", Encoder)
    commands = iter([("feed_frame", ("global", True)), ("cancel_episode", ()),
                     ("feed_frame", ("global", False)), ("close", ())])
    replies = []
    _worker(SimpleNamespace(get=lambda: next(commands), close=lambda: None),
            SimpleNamespace(send=replies.append, close=lambda: None), {})
    assert replies[0]["ok"] is True
    assert replies[1]["ok"] is False
    assert "video encoder dropped frames" in replies[1]["error"]
    assert all(reply["ok"] for reply in replies[2:])
    assert replies[3]["drops"] == {}


def test_three_camera_encoding_is_complete_and_can_start_next_episode(tmp_path):
    encoder = ProcessVideoEncoder(fps=30, vcodec="h264", encoder_threads=2, queue_maxsize=90)
    try:
        assert encoder._process.is_alive()
        for episode in range(2):
            encoder.start_episode(["global", "left", "right"], tmp_path)
            for i in range(30):
                image = np.full((48, 64, 3), i * 5, dtype=np.uint8)
                for key in ("global", "left", "right"):
                    encoder.feed_frame(key, image)
                image[:] = 0
            results = encoder.finish_episode()
            assert encoder._dropped_frames == {}
            assert set(results) == {"global", "left", "right"}
            for path, stats in results.values():
                with av.open(str(path)) as video:
                    frames = list(video.decode(video=0))
                assert len(frames) == 30
                assert float(frames[-1].pts * frames[-1].time_base) == pytest.approx(29 / 30)
                assert frames[-1].to_ndarray().mean() > 50
                assert stats is not None
                np.testing.assert_allclose(stats["std"], np.std(np.arange(30, dtype=float) * 5), rtol=1e-6)
    finally:
        encoder.close()
    assert not encoder._process.is_alive()


def test_encoder_discard_and_worker_failure_are_recoverable(tmp_path):
    encoder = ProcessVideoEncoder(fps=30, vcodec="h264", encoder_threads=2)
    try:
        encoder.start_episode(["global"], tmp_path)
        encoder.feed_frame("global", np.zeros((48, 64, 3), dtype=np.uint8))
        encoder.cancel_episode()
        encoder.start_episode(["global"], tmp_path)
        encoder._process.terminate()
        encoder._process.join(2)
        started = time.monotonic()
        with pytest.raises(RuntimeError):
            encoder.feed_frame("global", np.zeros((48, 64, 3), dtype=np.uint8))
        assert time.monotonic() - started < 1
        encoder.cancel_episode()
        encoder.start_episode(["global"], tmp_path)
        for _ in range(3):
            encoder.feed_frame("global", np.zeros((48, 64, 3), dtype=np.uint8))
        assert "global" in encoder.finish_episode()
    finally:
        encoder.close()


def test_gc_scope_restores_state_without_disabling_gc(monkeypatch):
    calls = []
    monkeypatch.setattr("gc.isenabled", lambda: True)
    monkeypatch.setattr("gc.get_freeze_count", lambda: 0)
    for name in ("collect", "freeze", "unfreeze"):
        monkeypatch.setattr("gc." + name, lambda name=name: calls.append(name))
    scope = RecordingGcScope()
    scope.start()
    scope.start()
    scope.close()
    scope.close()
    assert calls == ["collect", "freeze", "unfreeze"]


def test_gc_scope_does_not_unfreeze_an_existing_owner(monkeypatch):
    monkeypatch.setattr("gc.isenabled", lambda: True)
    monkeypatch.setattr("gc.get_freeze_count", lambda: 42)
    monkeypatch.setattr("gc.unfreeze", lambda: pytest.fail("must preserve existing freeze"))
    scope = RecordingGcScope()
    scope.start()
    scope.close()


def test_dataset_writer_saves_process_encoded_videos_and_stats(tmp_path):
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from backend.services.process_video_encoder import isolate_dataset_encoder

    features = {"observation.state": {"dtype": "float32", "shape": (2,), "names": ["x", "y"]}}
    keys = ["observation.images." + side for side in ("global", "left", "right")]
    for key in keys:
        features[key] = {"dtype": "video", "shape": (48, 64, 3), "names": ["height", "width", "channels"]}
    dataset = LeRobotDataset.create(repo_id="local/offline_test", fps=30, features=features,
                                    root=tmp_path / "dataset", use_videos=True, vcodec="h264",
                                    streaming_encoding=True, encoder_threads=2)
    try:
        isolate_dataset_encoder(dataset)
        encoder = dataset.writer._streaming_encoder
        for i in range(12):
            frame = {"observation.state": np.array([i, i], dtype=np.float32), "task": "offline"}
            frame.update({key: np.full((48, 64, 3), i * 10, dtype=np.uint8) for key in keys})
            dataset.add_frame(frame)
        dataset.save_episode(parallel_encoding=True)
        assert dataset.meta.total_frames == 12
        assert all(key in dataset.meta.stats for key in keys)
    finally:
        dataset.finalize()
    assert not encoder._process.is_alive()
    videos = list((tmp_path / "dataset").rglob("*.mp4"))
    assert len(videos) == 3
    for path in videos:
        with av.open(str(path)) as container:
            assert sum(1 for _ in container.decode(video=0)) == 12
    from backend.services.video_statistics import decoded_video_statistics
    from scripts.recompute_video_statistics import repair
    import json
    source = tmp_path / "dataset"
    original = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with av.open(str(videos[0])) as container:
        pixels = np.concatenate([f.to_ndarray(format="rgb24").reshape(-1, 3)
                                 for f in container.decode(video=0)]) / 255
    stats = decoded_video_statistics(videos[0], 0, 12 / 30, 12)
    np.testing.assert_allclose(stats["std"].ravel(), pixels.std(axis=0), atol=1e-10)
    with pytest.raises(ValueError, match="frame count mismatch"):
        decoded_video_statistics(videos[0], 0, 12 / 30, 13)
    destination = repair(source, tmp_path / "repaired")
    for relative, content in original.items():
        assert (source / relative).read_bytes() == content
        if relative.parts[0] in ("data", "videos"):
            assert (destination / relative).read_bytes() == content
    repaired_stats = json.loads((destination / "meta/stats.json").read_text())
    old_stats = json.loads(original[next(p for p in original if p.as_posix() == "meta/stats.json")])
    assert repaired_stats["observation.state"] == old_stats["observation.state"]
    assert np.asarray(repaired_stats[keys[0]]["std"]).min() > 0.1
    with pytest.raises(ValueError, match="输出必须"):
        repair(source, destination)


def test_finish_failure_restores_gc_scope():
    import asyncio
    from types import SimpleNamespace
    from backend.services.dataset_recorder import DatasetRecorderService

    recorder = object.__new__(DatasetRecorderService)
    closed = []
    recorder._recording_gc_scope = SimpleNamespace(close=lambda: closed.append(True))
    async def fail():
        raise RuntimeError("failed to drain")
    recorder._finish_session = fail
    with pytest.raises(RuntimeError, match="failed to drain"):
        asyncio.run(recorder.finish_session())
    assert closed == [True]
    assert recorder._recording_gc_scope is None
