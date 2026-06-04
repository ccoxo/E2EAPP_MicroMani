from __future__ import annotations

from typing import Any

from backend.drivers.camera_opencv import OpenCVCameraDriver
from backend.workers.camera_capture_worker import _CameraCaptureWorker


class _FakeCapture:
    def __init__(self) -> None:
        self.set_calls: list[tuple[int, Any]] = []

    def set(self, prop: int, value: Any) -> None:
        self.set_calls.append((prop, value))


class _FakeCv2:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FOURCC = 6
    CAP_PROP_BUFFERSIZE = 7

    @staticmethod
    def VideoWriter_fourcc(a: str, b: str, c: str, d: str) -> str:
        return f"{a}{b}{c}{d}"


def test_camera_driver_requests_yuyv_fourcc_before_capture_dimensions() -> None:
    capture = _FakeCapture()

    OpenCVCameraDriver()._configure_capture(_FakeCv2, capture, 640, 480, 30.0)  # noqa: SLF001

    assert capture.set_calls[0] == (_FakeCv2.CAP_PROP_FOURCC, "YUYV")
    assert capture.set_calls[2:] == [
        (_FakeCv2.CAP_PROP_FRAME_WIDTH, 640),
        (_FakeCv2.CAP_PROP_FRAME_HEIGHT, 480),
        (_FakeCv2.CAP_PROP_FPS, 30.0),
    ]


def test_camera_worker_requests_yuyv_fourcc_before_capture_dimensions() -> None:
    capture = _FakeCapture()
    worker = object.__new__(_CameraCaptureWorker)
    worker.cv2 = _FakeCv2
    worker.width = 640
    worker.height = 480
    worker.fps = 30.0
    worker.profile = None

    worker._configure_capture(capture)  # noqa: SLF001

    assert capture.set_calls[0] == (_FakeCv2.CAP_PROP_FOURCC, "YUYV")
    assert capture.set_calls[2:] == [
        (_FakeCv2.CAP_PROP_FRAME_WIDTH, 640),
        (_FakeCv2.CAP_PROP_FRAME_HEIGHT, 480),
        (_FakeCv2.CAP_PROP_FPS, 30.0),
    ]
