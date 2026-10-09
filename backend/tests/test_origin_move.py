from __future__ import annotations

import ast
import asyncio
import inspect
import queue
import sys
from concurrent.futures import Future
from pathlib import Path
from threading import Lock
from types import ModuleType, SimpleNamespace

import pytest

from backend.services.dataset_recorder import (
    ORIGIN_MOVE_POSITION_NAMES,
    DatasetRecorderService,
    DatasetSaveError,
    LeRobotWriterThread,
    OriginMoveWrite,
    TimedSample,
    WriterCommand,
)


def make_tap_recorder(start: float) -> DatasetRecorderService:
    recorder = object.__new__(DatasetRecorderService)
    recorder._recording = True
    recorder._episode_start_monotonic_s = start
    recorder._origin_move_lock = Lock()
    recorder._origin_move_buffer = []
    recorder._origin_move_last_t = None
    recorder._native_live_episode_metadata = {}
    return recorder


@pytest.mark.parametrize("motion_hz", [100, 200, 1000])
def test_origin_move_rate_hardware_order_and_snapshot(motion_hz: int) -> None:
    start = 1_234_567.5
    recorder = make_tap_recorder(start)
    positions = list(range(12))
    for index in range(motion_hz):
        target = start + index / motion_hz
        recorder._tap_origin_move(TimedSample("hal", target, {"positions": positions, "pulses": [999] * 12}), target)
    positions[0] = -999
    assert len(recorder._origin_move_buffer) == 100
    assert recorder._origin_move_buffer[0] == (start, tuple(float(i) for i in range(12)))
    assert [round(t - start, 5) for t, _ in recorder._origin_move_buffer] == [i / 100 for i in range(100)]


def test_origin_move_rejects_invalid_and_out_of_episode_samples() -> None:
    recorder = make_tap_recorder(50.0)
    value = {"positions": list(range(12))}
    recorder._tap_origin_move(TimedSample("hal", 49.99, value), 50.0)
    recorder._tap_origin_move(TimedSample("hal", 50.0, value, ok=False), 50.0)
    recorder._tap_origin_move(TimedSample("hal", 50.0, {"positions": [1] * 11}), 50.0)
    recorder._recording = False
    recorder._tap_origin_move(TimedSample("hal", 50.01, value), 50.01)
    assert recorder._origin_move_buffer == []
    recorder._recording = True
    recorder._tap_origin_move(TimedSample("hal", 50.02, value), 50.02)
    assert recorder._origin_move_buffer == [(50.02, tuple(range(12)))]


def test_origin_move_reset_and_discard_clear_without_write() -> None:
    recorder = DatasetRecorderService(
        SimpleNamespace(get_config=lambda: {}),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    recorder._origin_move_buffer = [(100.1, (1.0,) * 12)]
    recorder._origin_move_last_t = 100.1
    recorder._writer_thread = object()
    recorder._native_dataset = object()
    recorder._begin_episode_locked(sample_clock_now_s=100, schedule_now_s=100)
    assert recorder._origin_move_buffer == [] and recorder._origin_move_last_t is None
    recorder._origin_move_buffer = [(100.7, (2.0,) * 12)]
    recorder._origin_move_last_t = 100.7
    calls = []

    async def command(kind):
        calls.append(kind)

    recorder._native_writer_command = command
    asyncio.run(recorder._clear_native_episode_buffer())
    assert calls == ["clear_episode"]
    assert recorder._origin_move_buffer == [] and recorder._origin_move_last_t is None


@pytest.mark.parametrize("degrees", [-1.25, 0.0, 0.0125, 37.0])
def test_origin_move_parquet_units_columns_and_timestamps(tmp_path: Path, degrees: float) -> None:
    parquet = pytest.importorskip("pyarrow.parquet")
    recorder = make_tap_recorder(50.0)
    positions = [1.0, 2.0, 3.0, degrees, degrees, degrees, 7.0, 8.0, 9.0, degrees, degrees, degrees]
    for t in (50.0, 50.01):
        recorder._tap_origin_move(TimedSample("hal", t, {"positions": positions, "pulses": [999] * 12}), t)
    payload = OriginMoveWrite(tmp_path, 12, 50.0, tuple(recorder._origin_move_buffer))
    writer = LeRobotWriterThread(SimpleNamespace(), queue.Queue())
    future = Future()
    writer._handle_command(WriterCommand("write_origin_move", future, payload))
    assert future.result() is None
    path = tmp_path / "origin_move" / "episode_000012.parquet"
    table = parquet.read_table(path)
    assert table.column_names == ["timestamp_s", *ORIGIN_MOVE_POSITION_NAMES]
    assert table.column("timestamp_s").to_pylist() == pytest.approx([0.0, 0.01])
    row = table.to_pylist()[0]
    for side in ("left", "right"):
        for axis in ("roll", "pitch", "yaw"):
            assert row[f"{side}_{axis}_mdeg"] == pytest.approx(degrees * 1000.0)
    assert row["left_x_um"] == 1.0 and row["right_x_um"] == 7.0
    assert recorder._origin_move_buffer[0][1] == tuple(positions)
    assert not path.with_suffix(".parquet.tmp").exists()
    assert not (tmp_path / "data").exists() and not (tmp_path / "meta").exists()


def test_origin_move_writer_failure_does_not_poison_native(tmp_path: Path) -> None:
    writer = LeRobotWriterThread(SimpleNamespace(), queue.Queue())
    writer._dataset = SimpleNamespace(meta=SimpleNamespace(total_frames=23), save_episode=lambda **kw: None)

    def fail(payload):
        raise OSError("sidecar unavailable")

    writer._write_origin_move = fail
    future = Future()
    writer._handle_command(WriterCommand("write_origin_move", future, OriginMoveWrite(tmp_path, 0, 1.0, ())))
    with pytest.raises(OSError, match="sidecar unavailable"):
        future.result()
    assert writer._error == ""
    saved = Future()
    writer._handle_command(WriterCommand("save_episode", saved))
    assert saved.result() == 23


def test_origin_move_save_isolation_reanchor_and_live_metadata(tmp_path: Path) -> None:
    recorder = make_tap_recorder(20.0)
    recorder._dataset_dir = tmp_path
    recorder._episode_index = 6
    recorder._native_error = ""
    recorder._origin_move_buffer = [(19.0, (1.0,) * 12), (20.01, (2.0,) * 12)]
    calls, warnings = [], []
    recorder.logs = SimpleNamespace(warning=lambda tag, msg: warnings.append(msg))

    async def command(kind, payload=None):
        calls.append(kind)
        if kind == "save_episode":
            return 77
        if kind == "latest_episode_metadata":
            return {"episode_index": 6, "video": "retained"}
        assert payload.samples == ((20.01, (2.0,) * 12),)
        raise OSError("write failed")

    recorder._native_writer_command = command
    asyncio.run(recorder._save_native_episode())
    assert calls == ["save_episode", "latest_episode_metadata", "write_origin_move"]
    assert recorder._native_total_frames_cached == 77 and recorder._native_error == ""
    assert recorder._native_live_episode_metadata[6]["video"] == "retained"
    assert len(warnings) == 1 and "write failed" in warnings[0]


def test_origin_move_not_written_when_native_save_fails(tmp_path: Path) -> None:
    recorder = make_tap_recorder(1.0)
    recorder._dataset_dir = tmp_path
    recorder._episode_index = 0
    calls = []

    async def command(kind, payload=None):
        calls.append(kind)
        raise RuntimeError("native failure")

    recorder._native_writer_command = command
    with pytest.raises(DatasetSaveError, match="native failure"):
        asyncio.run(recorder._save_native_episode())
    assert calls == ["save_episode"] and not (tmp_path / "origin_move").exists()


def test_origin_move_uses_writer_thread_after_native_save(tmp_path: Path) -> None:
    recorder = make_tap_recorder(300.0)
    recorder._dataset_dir = tmp_path
    recorder._episode_index = 8
    recorder._native_error = ""
    recorder._origin_move_buffer = [(300.0, tuple(float(i) for i in range(12)))]
    calls, warnings = [], []
    recorder.logs = SimpleNamespace(
        error=lambda tag, msg: warnings.append(msg), warning=lambda tag, msg: warnings.append(msg)
    )
    work = queue.Queue()
    writer = LeRobotWriterThread(recorder, work)
    writer._dataset = SimpleNamespace(
        meta=SimpleNamespace(total_frames=18), save_episode=lambda **kw: calls.append("native")
    )
    raw_write = writer._write_origin_move

    def write(payload):
        calls.append("sidecar")
        raw_write(payload)

    writer._write_origin_move = write
    recorder._writer_thread = writer
    writer.start()
    try:
        asyncio.run(recorder._save_native_episode())
    finally:
        work.put(None)
        writer.join(2.0)
    assert calls == ["native", "sidecar"] and warnings == []
    assert (tmp_path / "origin_move" / "episode_000008.parquet").is_file()


def test_origin_move_hub_upload_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.push_dataset_to_hub import push_dataset

    calls = []

    class Dataset:
        def __init__(self, repo, *, root):
            pass

        def push_to_hub(self, *, private, allow_patterns):
            calls.append(allow_patterns)

    module = ModuleType("lerobot.datasets.lerobot_dataset")
    module.LeRobotDataset = Dataset
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "info.json").write_text("{}", encoding="utf-8")
    (tmp_path / "origin_move").mkdir()
    push_dataset("org/dataset", tmp_path, private=True)
    assert calls == [["data/**", "meta/**", "videos/**", "README.md"]]


def test_origin_move_tap_is_memory_only_and_after_ring_append() -> None:
    loop = inspect.getsource(DatasetRecorderService._sample_source_loop_with_runner)
    assert loop.index(".append(sample)") < loop.index("self._tap_origin_move(sample, target_s)")
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(DatasetRecorderService._tap_origin_move)))
    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & {"mkdir", "write_table", "open", "write_text", "put", "put_nowait", "submit"})
