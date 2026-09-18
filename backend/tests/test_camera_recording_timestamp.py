from threading import Lock
from types import SimpleNamespace

from backend.drivers import camera_opencv


def test_jpeg_decode_keeps_timestamp_of_selected_frame(monkeypatch):
    driver = camera_opencv.OpenCVCameraDriver()
    driver._frame_locks[0] = Lock()
    driver._latest_jpegs[0] = b"old-frame"
    driver._latest_at[0] = 10.0
    monkeypatch.setattr(camera_opencv, "import_module", lambda _name: SimpleNamespace())
    monkeypatch.setattr(driver, "_resolved_indices", lambda *_args: {"global": 0})
    monkeypatch.setattr(driver, "_capture_size", lambda *_args: (640, 480))
    monkeypatch.setattr(driver, "_get_capture", lambda *_args: object())

    def decode(_cv2, jpeg):
        # 解码期间采集线程发布下一帧，不能把它的时间戳贴到已选取的旧画面上。
        with driver._frame_locks[0]:
            driver._latest_jpegs[0] = b"new-frame"
            driver._latest_at[0] = 10.033
        return jpeg

    monkeypatch.setattr(driver, "_decode_jpeg_to_rgb_frame", decode)
    snapshot = driver.snapshot_frame_with_timestamp({"cameras": {"fps": 30}}, "global")
    assert snapshot.frame == b"old-frame"
    assert snapshot.monotonic_s == 10.0
