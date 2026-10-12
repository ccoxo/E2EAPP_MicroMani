"""共享内存相机模式（APPSTATION_CAMERA_CAPTURE_MODE=shm）的离线测试：不打开真实相机。"""

from __future__ import annotations

import time
from multiprocessing import shared_memory
from threading import Event, Lock

import numpy as np
import pytest

from backend.drivers.camera_opencv import OpenCVCameraDriver, _ShmCameraCapture
from backend.workers.camera_shm_worker import ShmFrameReader, ShmFrameWriter, shm_size

W, H = 64, 48


@pytest.fixture()
def shm():
    block = shared_memory.SharedMemory(create=True, size=shm_size(W, H))
    yield block
    try:
        block.close()
        block.unlink()
    except Exception:
        pass


def frame(value: int) -> np.ndarray:
    return np.full((H, W, 3), value, dtype=np.uint8)


def test_round_trip_keeps_pixels_and_capture_time(shm):
    writer = ShmFrameWriter(shm.buf, W, H)
    reader = ShmFrameReader(shm.buf, W, H)
    assert reader.read_latest(0) is None
    seq = writer.write(frame(7), 123.456, max_gap_ms=40)
    got = reader.read_latest(0)
    assert got is not None
    got_seq, img, ts, gap = got
    assert got_seq == seq == 1 and ts == pytest.approx(123.456, abs=1e-6) and gap == 40
    assert img.dtype == np.uint8 and img.shape == (H, W, 3) and int(img[0, 0, 0]) == 7
    assert reader.read_latest(got_seq) is None  # no new frame
    writer.write(frame(9), 124.0)
    got = reader.read_latest(got_seq)
    assert got is not None and got[0] == 2 and int(got[1][5, 5, 2]) == 9


def test_copy_is_independent_of_later_writes(shm):
    writer = ShmFrameWriter(shm.buf, W, H)
    reader = ShmFrameReader(shm.buf, W, H)
    writer.write(frame(1), 1.0)
    _, img, _, _ = reader.read_latest(0)
    for v in range(2, 6):
        writer.write(frame(v), float(v))
    assert int(img.max()) == 1 and int(img.min()) == 1


def test_overwrite_during_copy_is_rejected(shm, monkeypatch):
    writer = ShmFrameWriter(shm.buf, W, H)
    reader = ShmFrameReader(shm.buf, W, H)
    writer.write(frame(1), 1.0)
    original_copy = np.ndarray.copy
    calls = {"n": 0}

    class Slot(np.ndarray):
        def copy(self, *a, **k):  # simulate the writer lapping the reader twice mid-copy
            calls["n"] += 1
            if calls["n"] == 1:
                writer.write(frame(2), 2.0)
                writer.write(frame(3), 3.0)
            return original_copy(self, *a, **k)

    reader.slots = [s.view(Slot) for s in reader.slots]
    got = reader.read_latest(0)
    assert got is not None
    seq, img, ts, _ = got
    assert seq == 3 and ts == pytest.approx(3.0) and int(img[0, 0, 0]) == 3  # retried, consistent frame
    assert calls["n"] >= 2


def test_stop_request_goes_through_shared_memory_header(shm):
    from backend.workers.camera_shm_worker import SHM_STOP_FIELD

    writer = ShmFrameWriter(shm.buf, W, H)
    reader = ShmFrameReader(shm.buf, W, H)
    assert int(writer.header[SHM_STOP_FIELD]) == 0
    reader.request_stop()
    assert int(writer.header[SHM_STOP_FIELD]) == 1
    writer.write(frame(1), 1.0)  # writing frames never clears the stop flag
    assert int(writer.header[SHM_STOP_FIELD]) == 1


def test_mode_gate(monkeypatch):
    import cv2

    driver = OpenCVCameraDriver()
    dshow = [(getattr(cv2, "CAP_DSHOW", 700), "CAP_DSHOW")]
    monkeypatch.delenv("APPSTATION_CAMERA_CAPTURE_MODE", raising=False)
    assert driver._shm_capture_enabled(cv2, dshow) is False
    monkeypatch.setenv("APPSTATION_CAMERA_CAPTURE_MODE", "process")
    assert driver._shm_capture_enabled(cv2, dshow) is False
    monkeypatch.setenv("APPSTATION_CAMERA_CAPTURE_MODE", "shm")
    assert driver._shm_capture_enabled(cv2, dshow) is True
    assert driver._shm_capture_enabled(cv2, [(1400, "CAP_MSMF")]) is False


class _FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.stdin = None

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        self.alive = False
        return 0


def test_shm_reader_thread_publishes_worker_capture_time(shm):
    driver = OpenCVCameraDriver()
    writer = ShmFrameWriter(shm.buf, W, H)
    capture = _ShmCameraCapture(_FakeProcess(), shm, ShmFrameReader(shm.buf, W, H), {})
    index = 5
    driver._frame_locks[index] = Lock()
    driver._frame_events[index] = Event()
    driver._jpeg_events[index] = Event()
    driver._start_shm_reader(index, capture)
    try:
        capture_time = time.monotonic() - 0.25  # captured 250 ms ago in the worker
        writer.write(frame(42), capture_time)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and driver._latest_frames.get(index) is None:
            time.sleep(0.005)
        assert driver._latest_frames.get(index) is not None
        assert int(driver._latest_frames[index][0, 0, 0]) == 42
        # the published time is the worker's capture time, not the moment this process copied it
        assert driver._latest_at[index] == pytest.approx(capture_time, abs=1e-6)
        assert driver._frame_events[index].is_set()
    finally:
        driver._reader_stops[index].set()
        driver._reader_threads[index].join(timeout=1.0)
        capture.reader = None


def test_shm_reader_marks_stale_when_worker_exits(shm):
    driver = OpenCVCameraDriver()
    proc = _FakeProcess()
    capture = _ShmCameraCapture(proc, shm, ShmFrameReader(shm.buf, W, H), {})
    index = 6
    driver._frame_locks[index] = Lock()
    driver._frame_events[index] = Event()
    driver._start_shm_reader(index, capture)
    thread = driver._reader_threads[index]
    proc.alive = False
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert index in driver._stale_indices
    capture.reader = None
