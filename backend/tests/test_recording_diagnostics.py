import gc
import json
import queue
import time
from threading import Event
from types import SimpleNamespace

import pytest

from backend.services.recording_diagnostics import RecordingDiagnostics
from backend.services.dataset_recorder import DatasetRecorderService, TimedRingBuffer, TimedSample


@pytest.mark.parametrize("setting,expected", [(None, False), ("0", False), ("1", True)])
def test_recording_diagnostics_requires_explicit_opt_in(monkeypatch, setting, expected):
    from backend.services import dataset_recorder as module
    if setting is None:
        monkeypatch.delenv("APPSTATION_RECORDING_DIAGNOSTICS", raising=False)
    else:
        monkeypatch.setenv("APPSTATION_RECORDING_DIAGNOSTICS", setting)
    started = []
    monkeypatch.setattr(module, "SOURCE_KEYS", ())
    monkeypatch.setattr(module, "RecordingDiagnostics", lambda path: SimpleNamespace(start=lambda: started.append(path)))
    recorder = object.__new__(DatasetRecorderService)
    recorder._sampler_stop_event = Event()
    recorder.logs = SimpleNamespace(info=lambda *_args: None)
    recorder._start_sampler_tasks_locked()
    assert bool(started) is expected


def test_trace_records_timeline_and_stacks_then_removes_gc_callback(tmp_path):
    trace = RecordingDiagnostics(tmp_path / "trace.jsonl", duration_s=0.15)
    original_callbacks = list(gc.callbacks)
    trace.emit("selected_camera", source="camera_global", video_s=8.0, capture_s=123.0)
    trace.start()
    trace._thread.join(2)
    assert not trace._thread.is_alive()
    assert trace.error == ""
    rows = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["kind"] == "start"
    assert any(row.get("video_s") == 8.0 and row["capture_s"] == 123.0 for row in rows)
    assert any(row["kind"] == "stacks" and row["threads"] for row in rows)
    assert rows[-1] == {"kind": "end", "dropped_events": 0}
    assert gc.callbacks == original_callbacks
    trace.emit("after_close")
    assert trace.events.empty()


def test_full_diagnostic_queue_drops_diagnostics_without_blocking():
    trace = RecordingDiagnostics(None)
    trace.events = queue.Queue(maxsize=1)
    trace.emit("first")
    trace.emit("second")
    assert trace.dropped == 1
    assert trace.events.qsize() == 1


def test_gc_only_records_long_pause(monkeypatch, tmp_path):
    trace = RecordingDiagnostics(tmp_path / "trace.jsonl")
    now = [10.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    trace._gc_callback("start", {"generation": 2})
    now[0] += 0.05
    trace._gc_callback("stop", {"generation": 2, "collected": 12})
    event = trace.events.get_nowait()
    assert event["kind"] == "gc_pause"
    assert 49 < event["elapsed_ms"] < 51
    trace._gc_callback("start", {"generation": 0})
    trace._gc_callback("stop", {"generation": 0})
    assert trace.events.empty()


def test_trace_disk_failure_is_contained_and_cleans_up(tmp_path):
    path = tmp_path / "existing.jsonl"
    path.write_text("keep", encoding="utf-8")
    trace = RecordingDiagnostics(path)
    original_callbacks = list(gc.callbacks)
    trace.start()
    trace._thread.join(2)
    assert trace.error
    assert gc.callbacks == original_callbacks
    assert path.read_text(encoding="utf-8") == "keep"


def test_explicit_close_drains_events(tmp_path):
    trace = RecordingDiagnostics(tmp_path / "trace.jsonl")
    trace.start()
    for index in range(100):
        trace.emit("assembly", frame=index)
    trace.close()
    rows = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert [row["frame"] for row in rows if row["kind"] == "assembly"] == list(range(100))
    assert not trace._thread.is_alive()


def test_selected_camera_trace_preserves_real_stamp_for_repeated_frame(tmp_path):
    recorder = object.__new__(DatasetRecorderService)
    recorder._episode_index = 0
    recorder._sampler_start_monotonic_s = 100.0
    recorder._recording_diagnostics = RecordingDiagnostics(tmp_path / "trace.jsonl")
    recorder._record_source_quality = lambda *_args: None
    buffer = TimedRingBuffer()
    image = object()
    buffer.append(TimedSample("camera_global", 108.5, image))
    recorder._sample_buffers = {"camera_global": buffer}
    for target in (108.5, 108.5 + 1 / 30):
        assert recorder._aligned_sample("camera_global", target).value is image
    first = recorder._recording_diagnostics.events.get_nowait()
    second = recorder._recording_diagnostics.events.get_nowait()
    assert first["video_s"] == 8.0
    assert second["video_s"] > first["video_s"]
    assert first["capture_s"] == second["capture_s"] == 108.5
    assert "value" not in first and "images" not in first
