from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from multiprocessing.queues import Queue
from typing import Any

CAMERA_CAPTURE_FOURCC = "YUYV"


def run_camera_capture_worker(
    index: int,
    width: int,
    height: int,
    fps: float,
    profile: dict[str, float | bool] | None,
    backend_candidates: list[tuple[int, str]],
    status_queue: Queue,
    command_queue: Queue,
    parent_pid: int | None = None,
) -> None:
    worker = _CameraCaptureWorker(
        index,
        width,
        height,
        fps,
        profile,
        backend_candidates,
        status_queue,
        command_queue,
        parent_pid,
    )
    worker.run()


def run_camera_capture_worker_stdio(startup: dict[str, Any]) -> None:
    command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    status_queue = _StdoutStatusQueue()

    def read_commands() -> None:
        for line in sys.stdin:
            if line.strip().lower() == "stop":
                command_queue.put({"type": "stop"})
                break

    threading.Thread(target=read_commands, name="camera-worker-stdin", daemon=True).start()
    worker = _CameraCaptureWorker(
        int(startup["index"]),
        int(startup["width"]),
        int(startup["height"]),
        float(startup["fps"]),
        startup.get("profile"),
        [(int(backend), str(label)) for backend, label in startup["backendCandidates"]],
        status_queue,
        command_queue,  # type: ignore[arg-type]
        int(startup.get("parentPid")) if startup.get("parentPid") is not None else None,
    )
    worker.run()


class _StdoutStatusQueue:
    def put_nowait(self, payload: dict[str, Any]) -> None:
        message = dict(payload)
        jpeg = message.pop("jpeg", None)
        if isinstance(jpeg, bytes):
            message["jpegB64"] = base64.b64encode(jpeg).decode("ascii")
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


class _CameraCaptureWorker:
    def __init__(
        self,
        index: int,
        width: int,
        height: int,
        fps: float,
        profile: dict[str, float | bool] | None,
        backend_candidates: list[tuple[int, str]],
        status_queue: Queue,
        command_queue: Queue,
        parent_pid: int | None,
    ) -> None:
        self.index = int(index)
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.profile = profile
        self.backend_candidates = backend_candidates
        self.status_queue = status_queue
        self.command_queue = command_queue
        self.parent_pid = parent_pid
        self.capture: Any | None = None
        self.cv2: Any | None = None
        self.backend_label = ""
        self.sequence = 0
        self.frame_times: deque[float] = deque()
        self.actual_fps = 0.0
        self.running = True

    def run(self) -> None:
        com_token = self._co_initialize()
        try:
            import cv2

            self.cv2 = cv2
            if not self._open_capture():
                return
            while self.running:
                if self._parent_exited() or not self._drain_commands():
                    break
                assert self.capture is not None
                ok, frame = self.capture.read()
                if not ok or frame is None:
                    time.sleep(0.02)
                    continue
                encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if not encoded:
                    continue
                self.sequence += 1
                now = time.monotonic()
                self._record_fps(now)
                self._publish(
                    {
                        "type": "frame",
                        "ok": True,
                        "opened": True,
                        "backend": self.backend_label,
                        "sequence": self.sequence,
                        "jpeg": bytes(buffer),
                        "fps": round(self.actual_fps, 1),
                        "monotonicMs": int(now * 1000),
                        "tsMs": int(time.time() * 1000),
                        "actualWidth": self._safe_get("CAP_PROP_FRAME_WIDTH") or self.width,
                        "actualHeight": self._safe_get("CAP_PROP_FRAME_HEIGHT") or self.height,
                        "actualFps": self._safe_get("CAP_PROP_FPS") or self.fps,
                        "message": "frame",
                    }
                )
        except BaseException as exc:  # noqa: BLE001 - keep startup failures visible to the parent.
            self._publish({"type": "status", "ok": False, "opened": False, "message": str(exc)})
        finally:
            self._release_capture()
            self._co_uninitialize(com_token)

    def _open_capture(self) -> bool:
        assert self.cv2 is not None
        last_error = ""
        for backend, label in self.backend_candidates:
            capture = self.cv2.VideoCapture(self.index, backend)
            if not bool(capture.isOpened()):
                last_error = f"{label} open failed"
                capture.release()
                continue
            self.capture = capture
            self.backend_label = label
            self._configure_capture(capture)
            self._publish(
                {
                    "type": "status",
                    "ok": True,
                    "opened": True,
                    "backend": label,
                    "sequence": 0,
                    "fps": 0.0,
                    "monotonicMs": int(time.monotonic() * 1000),
                    "tsMs": int(time.time() * 1000),
                    "actualWidth": self._safe_get("CAP_PROP_FRAME_WIDTH") or self.width,
                    "actualHeight": self._safe_get("CAP_PROP_FRAME_HEIGHT") or self.height,
                    "actualFps": self._safe_get("CAP_PROP_FPS") or self.fps,
                    "message": "opened",
                }
            )
            return True
        self._publish({"type": "status", "ok": False, "opened": False, "message": last_error or "open failed"})
        return False

    def _configure_capture(self, capture: Any) -> None:
        assert self.cv2 is not None
        if hasattr(self.cv2, "CAP_PROP_FOURCC") and hasattr(self.cv2, "VideoWriter_fourcc"):
            try:
                capture.set(self.cv2.CAP_PROP_FOURCC, self.cv2.VideoWriter_fourcc(*CAMERA_CAPTURE_FOURCC))
            except Exception:
                pass
        if hasattr(self.cv2, "CAP_PROP_BUFFERSIZE"):
            try:
                capture.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
        capture.set(self.cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(self.cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if hasattr(self.cv2, "CAP_PROP_FPS"):
            capture.set(self.cv2.CAP_PROP_FPS, self.fps)
        if self.profile is not None:
            self._safe_set("CAP_PROP_AUTOFOCUS", 0.0)
            auto_exposure = bool(self.profile.get("autoExposure", False))
            self._safe_set("CAP_PROP_AUTO_EXPOSURE", 0.75 if auto_exposure else 0.25)
            if not auto_exposure:
                self._safe_set("CAP_PROP_EXPOSURE", float(self.profile.get("exposure", 0.0)))
            self._safe_set("CAP_PROP_GAIN", float(self.profile.get("gain", 0.0)))
            self._safe_set("CAP_PROP_AUTO_WB", 1.0 if bool(self.profile.get("autoWhiteBalance", False)) else 0.0)

    def _safe_set(self, prop_name: str, value: float) -> None:
        if self.cv2 is None or self.capture is None or not hasattr(self.cv2, prop_name):
            return
        try:
            self.capture.set(getattr(self.cv2, prop_name), value)
        except Exception:
            return

    def _safe_get(self, prop_name: str) -> float | None:
        if self.cv2 is None or self.capture is None or not hasattr(self.cv2, prop_name):
            return None
        try:
            return float(self.capture.get(getattr(self.cv2, prop_name)))
        except Exception:
            return None

    def _record_fps(self, now: float) -> None:
        self.frame_times.append(now)
        cutoff = now - 1.0
        while len(self.frame_times) > 2 and self.frame_times[0] < cutoff:
            self.frame_times.popleft()
        if len(self.frame_times) < 2:
            return
        span = self.frame_times[-1] - self.frame_times[0]
        if span > 0:
            self.actual_fps = (len(self.frame_times) - 1) / span

    def _drain_commands(self) -> bool:
        while True:
            try:
                message = self.command_queue.get_nowait()
            except queue.Empty:
                return True
            if isinstance(message, dict) and message.get("type") == "stop":
                return False

    def _release_capture(self) -> None:
        if self.capture is not None:
            try:
                self.capture.release()
            except Exception:
                pass
            self.capture = None

    def _publish(self, payload: dict[str, Any]) -> None:
        try:
            self.status_queue.put_nowait(payload)
        except queue.Full:
            try:
                self.status_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.status_queue.put_nowait(payload)
            except queue.Full:
                pass

    def _co_initialize(self) -> object | None:
        if os.name != "nt":
            return None
        try:
            hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            return None
        return True if hr in (0, 1) else None

    def _co_uninitialize(self, token: object | None) -> None:
        if token is None:
            return
        try:
            ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass

    def _parent_exited(self) -> bool:
        if self.parent_pid is None:
            return False
        if os.name == "nt":
            synchronize = 0x00100000
            wait_object_0 = 0x00000000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(self.parent_pid))
            if not handle:
                return False
            try:
                result = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                return result == wait_object_0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        try:
            os.kill(int(self.parent_pid), 0)
        except OSError:
            return True
        return False


def _main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("camera worker requires one JSON startup argument")
    run_camera_capture_worker_stdio(json.loads(sys.argv[1]))


if __name__ == "__main__":
    _main()
