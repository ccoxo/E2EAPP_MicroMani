from __future__ import annotations

import os
import time
from dataclasses import dataclass
from importlib import import_module
from threading import Event, Lock, Thread
from typing import Any

from backend.core.schemas import CameraTelemetry, ConnectionState

CAMERA_CAPTURE_SIZES: dict[str, tuple[int, int]] = {
    "global": (1920, 1080),
    "wrist_left": (1920, 1080),
    "wrist_right": (1920, 1080),
}


@dataclass
class CameraProbeResult:
    ok: bool
    message: str
    cameras: list[CameraTelemetry]


def _backend_candidates(cv2: Any) -> list[tuple[int, str]]:
    """Return Windows OpenCV capture backends in the order we should try them.

    The final deployment computer can expose the same cameras differently
    across OpenCV builds and Windows camera stacks. MSMF is fast when it works,
    but on this machine it currently fails to open every index; DSHOW opens the
    UVC cameras at 30fps. Keep the env override for diagnostics, then fallback
    automatically so one bad backend does not blank all previews.
    """
    known = {
        "dshow": (getattr(cv2, "CAP_DSHOW", 0), "CAP_DSHOW"),
        "msmf": (getattr(cv2, "CAP_MSMF", 0), "CAP_MSMF"),
        "any": (getattr(cv2, "CAP_ANY", 0), "CAP_ANY"),
    }
    override = os.environ.get("APPSTATION_CAMERA_BACKEND", "").strip().lower()
    ordered: list[tuple[int, str]] = []
    if override in known:
        ordered.append(known[override])
    for key in ("dshow", "any", "msmf"):
        candidate = known[key]
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


class OpenCVCameraDriver:
    def __init__(self) -> None:
        self._last_probe = 0.0
        self._cached: CameraProbeResult | None = None
        self._capture_lock = Lock()
        self._captures: dict[int, Any] = {}
        self._capture_sizes: dict[int, tuple[int, int, float]] = {}
        self._latest_jpegs: dict[int, bytes] = {}
        self._latest_at: dict[int, float] = {}
        self._latest_fps: dict[int, float] = {}
        self._latest_mean: dict[int, list[float]] = {}
        self._reader_threads: dict[int, Thread] = {}
        self._reader_stops: dict[int, Event] = {}
        self._encode_threads: dict[int, Thread] = {}
        self._encode_stops: dict[int, Event] = {}
        self._latest_frames: dict[int, Any] = {}
        self._frame_locks: dict[int, Lock] = {}
        self._frame_events: dict[int, Event] = {}
        self._resolved_cache_key: tuple[object, ...] | None = None
        self._resolved_cache_at = 0.0
        self._resolved_cache: dict[str, int] | None = None
        self._backend_label: str = ""

    def probe(self, config: dict[str, Any]) -> CameraProbeResult:
        now = time.monotonic()
        if self._cached is not None and now - self._last_probe < 5.0:
            return self._cached
        self._last_probe = now
        try:
            cv2 = import_module("cv2")
        except Exception as exc:
            return self._store(False, f"OpenCV import failed: {exc}", self._offline_cameras("error"))

        cameras: list[CameraTelemetry] = []
        errors: list[str] = []
        fps_config = float(config["cameras"].get("fps", 30))
        resolved = self._resolved_indices(cv2, config, fps_config)
        definitions = [
            ("global", "Global Camera", resolved["global"]),
            ("wrist_left", "Left Wrist Camera", resolved["wrist_left"]),
            ("wrist_right", "Right Wrist Camera", resolved["wrist_right"]),
        ]
        for key, label, index in definitions:
            width, height = self._capture_size(config, key)
            with self._capture_lock:
                capture = self._get_capture(cv2, index, width, height, fps_config)
                if capture is None:
                    errors.append(f"{label} index {index} open failed")
                    cameras.append(
                        CameraTelemetry(
                            key=key,  # type: ignore[arg-type]
                            label=label,
                            fps=0,
                            timestampSkewMs=0,
                            frameAgeMs=9999,
                            health="error",
                        )
                    )
                    continue
                fps = self._latest_fps.get(index)
                if fps is None or fps <= 0:
                    fps = float(capture.get(getattr(cv2, "CAP_PROP_FPS", 5)) or fps_config)
                latest_at = self._latest_at.get(index)
                frame_age = 0 if latest_at is None else round((time.monotonic() - latest_at) * 1000)
                has_frame = index in self._latest_jpegs
            cameras.append(
                CameraTelemetry(
                    key=key,  # type: ignore[arg-type]
                    label=label,
                    fps=round(float(fps), 1),
                    timestampSkewMs=0,
                    frameAgeMs=frame_age,
                    health="ok" if has_frame else "checking",
                )
            )
        message = "; ".join(errors) if errors else f"OpenCV {self._backend_label} cameras available"
        return self._store(not errors, message, cameras)

    def snapshot(self, config: dict[str, Any], camera: str) -> bytes:
        try:
            cv2 = import_module("cv2")
        except Exception as exc:
            raise RuntimeError(f"OpenCV import failed: {exc}") from exc

        fps_config = float(config["cameras"].get("fps", 30))
        resolved = self._resolved_indices(cv2, config, fps_config)
        if camera not in resolved:
            raise RuntimeError(f"unknown camera: {camera}")
        index = resolved[camera]
        width, height = self._capture_size(config, camera)
        with self._capture_lock:
            capture = self._get_capture(cv2, index, width, height, fps_config)
            if capture is None:
                raise RuntimeError(f"{camera} index {index} open failed")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            cached = self._latest_jpegs.get(index)
            if cached is not None:
                return cached
            time.sleep(0.01)
        raise RuntimeError(f"{camera} index {index} frame read failed")

    def reconnect(self, config: dict[str, Any], camera: str | None = None) -> CameraProbeResult:
        if camera is not None and camera not in {"global", "wrist_left", "wrist_right"}:
            return self._store(False, f"unknown camera: {camera}", self._offline_cameras("error"))
        try:
            cv2 = import_module("cv2")
        except Exception as exc:
            return self._store(False, f"OpenCV import failed: {exc}", self._offline_cameras("error"))

        if camera is None:
            with self._capture_lock:
                indices = set(self._captures)
        else:
            fps_config = float(config["cameras"].get("fps", 30))
            resolved = self._resolved_indices(cv2, config, fps_config)
            indices = {resolved[camera]}

        with self._capture_lock:
            for index in indices:
                self._drop_capture(index)
            self._clear_probe_cache()
        return self.probe(config)

    def enumerate_devices(self, config: dict[str, Any], max_index: int = 3) -> list[dict[str, Any]]:
        try:
            cv2 = import_module("cv2")
        except Exception as exc:
            return [{"error": f"OpenCV import failed: {exc}"}]
        fps_config = float(config["cameras"].get("fps", 30))
        configured = self._resolved_indices(cv2, config, fps_config, max_index=max_index)
        devices: list[dict[str, Any]] = []
        for index in range(max_index):
            started = time.perf_counter()
            roles = [role for role, role_index in configured.items() if role_index == index]
            probe_role = roles[0] if roles else "global"
            width, height = self._capture_size(config, probe_role)
            with self._capture_lock:
                capture = self._get_capture(cv2, index, width, height, fps_config)
                opened = capture is not None
                read = index in self._latest_jpegs
                mean_bgr = self._latest_mean.get(index)
                fps = self._latest_fps.get(index, 0.0)
                actual_width = 0.0
                actual_height = 0.0
                if capture is not None:
                    fps = fps or float(capture.get(getattr(cv2, "CAP_PROP_FPS", 5)) or 0)
                    actual_width = float(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    actual_height = float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                latest_at = self._latest_at.get(index)
                frame_age_ms = None if latest_at is None else round((time.monotonic() - latest_at) * 1000)
            devices.append(
                {
                    "index": index,
                    "opened": opened,
                    "read": bool(read),
                    "fps": round(float(fps), 1),
                    "width": actual_width,
                    "height": actual_height,
                    "mean_bgr": mean_bgr,
                    "configuredAs": roles,
                    "frameAgeMs": frame_age_ms,
                    "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
                }
            )
        return devices

    def _store(self, ok: bool, message: str, cameras: list[CameraTelemetry]) -> CameraProbeResult:
        self._cached = CameraProbeResult(ok=ok, message=message, cameras=cameras)
        return self._cached

    def _clear_probe_cache(self) -> None:
        self._cached = None
        self._last_probe = 0.0
        self._resolved_cache_key = None
        self._resolved_cache_at = 0.0
        self._resolved_cache = None

    def _offline_cameras(self, health: ConnectionState) -> list[CameraTelemetry]:
        return [
            CameraTelemetry(
                key="global",
                label="Global Camera",
                fps=0,
                timestampSkewMs=0,
                frameAgeMs=9999,
                health=health,
            ),
            CameraTelemetry(
                key="wrist_left",
                label="Left Wrist Camera",
                fps=0,
                timestampSkewMs=0,
                frameAgeMs=9999,
                health=health,
            ),
            CameraTelemetry(
                key="wrist_right",
                label="Right Wrist Camera",
                fps=0,
                timestampSkewMs=0,
                frameAgeMs=9999,
                health=health,
            ),
        ]

    def _parse_index(self, descriptor: str, fallback: int) -> int:
        for token in descriptor.replace("/", " ").split():
            if token.lstrip("-").isdigit():
                return int(token)
        return fallback

    def _resolved_indices(
        self,
        cv2: Any,
        config: dict[str, Any],
        fps: float,
        *,
        max_index: int = 10,
    ) -> dict[str, int]:
        cameras = config["cameras"]
        cache_key = (
            str(cameras.get("global", "index -1")),
            str(cameras.get("wristLeft", "index 1")),
            str(cameras.get("wristRight", "index 0")),
            str(cameras.get("globalResolution", cameras.get("previewResolution", "native"))),
            str(cameras.get("wristLeftResolution", cameras.get("previewResolution", "native"))),
            str(cameras.get("wristRightResolution", cameras.get("previewResolution", "native"))),
            fps,
            max_index,
        )
        now = time.monotonic()
        if (
            self._resolved_cache_key == cache_key
            and self._resolved_cache is not None
            and now - self._resolved_cache_at < 30
        ):
            return dict(self._resolved_cache)
        resolved = {
            "global": self._parse_index(str(cameras.get("global", "index -1")), -1),
            "wrist_left": self._parse_index(str(cameras.get("wristLeft", "index 1")), 1),
            "wrist_right": self._parse_index(str(cameras.get("wristRight", "index 0")), 0),
        }
        wrist_indices = {resolved["wrist_left"], resolved["wrist_right"]}
        if resolved["global"] < 0 or resolved["global"] in wrist_indices:
            readable = self._discover_readable_indices(cv2, *CAMERA_CAPTURE_SIZES["global"], fps, max_index)
            remaining = [index for index in readable if index not in wrist_indices]
            if remaining:
                resolved["global"] = remaining[0]
        self._resolved_cache_key = cache_key
        self._resolved_cache_at = now
        self._resolved_cache = dict(resolved)
        return resolved

    def _discover_readable_indices(self, cv2: Any, width: int, height: int, fps: float, max_index: int) -> list[int]:
        readable: list[int] = []
        for index in range(max_index):
            with self._capture_lock:
                capture = self._get_capture(cv2, index, width, height, fps)
            if capture is None:
                continue
            deadline = time.monotonic() + 0.2
            while index not in self._latest_jpegs and time.monotonic() < deadline:
                time.sleep(0.02)
            readable.append(index)
        return readable

    def _get_capture(self, cv2: Any, index: int, width: int, height: int, fps: float) -> Any | None:
        if index < 0:
            return None
        capture = self._captures.get(index)
        size = self._capture_sizes.get(index)
        if capture is not None and capture.isOpened() and size == (width, height, fps):
            return capture
        self._drop_capture(index)

        capture = None
        backend_label = ""
        for backend, label in _backend_candidates(cv2):
            candidate = cv2.VideoCapture(index, backend)
            if not candidate.isOpened():
                candidate.release()
                continue
            self._configure_capture(cv2, candidate, width, height, fps)
            capture = candidate
            backend_label = label
            break
        if capture is None:
            return None
        self._backend_label = backend_label

        self._captures[index] = capture
        self._capture_sizes[index] = (width, height, fps)
        self._frame_locks[index] = Lock()
        self._frame_events[index] = Event()
        self._start_reader(cv2, index, capture)
        self._start_encoder(cv2, index)
        return capture

    def _configure_capture(self, cv2: Any, capture: Any, width: int, height: int, fps: float) -> None:
        # The order matters on Windows: FOURCC must be set BEFORE width/height/fps
        # or Media Foundation falls back to YUY2 and caps at ~10fps for HD.
        if hasattr(cv2, "CAP_PROP_FOURCC") and hasattr(cv2, "VideoWriter_fourcc"):
            try:
                capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            except Exception:
                pass
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            try:
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if hasattr(cv2, "CAP_PROP_FPS"):
            capture.set(cv2.CAP_PROP_FPS, fps)

    def _drop_capture(self, index: int) -> None:
        for stops, threads in (
            (self._reader_stops, self._reader_threads),
            (self._encode_stops, self._encode_threads),
        ):
            stop = stops.pop(index, None)
            if stop is not None:
                stop.set()
            thread = threads.pop(index, None)
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.3)
        capture = self._captures.pop(index, None)
        self._capture_sizes.pop(index, None)
        self._latest_jpegs.pop(index, None)
        self._latest_at.pop(index, None)
        self._latest_fps.pop(index, None)
        self._latest_mean.pop(index, None)
        self._latest_frames.pop(index, None)
        self._frame_locks.pop(index, None)
        self._frame_events.pop(index, None)
        if capture is not None:
            capture.release()

    def _start_reader(self, cv2: Any, index: int, capture: Any) -> None:
        if index in self._reader_threads:
            return
        stop = Event()
        self._reader_stops[index] = stop
        frame_lock = self._frame_locks[index]
        frame_event = self._frame_events[index]

        def read_loop() -> None:
            consecutive_failures = 0
            while not stop.is_set():
                ok, frame = capture.read()
                if ok and frame is not None:
                    consecutive_failures = 0
                    now = time.monotonic()
                    last_at = self._latest_at.get(index)
                    if last_at is not None and now > last_at:
                        instant_fps = 1.0 / max(now - last_at, 0.001)
                        previous_fps = self._latest_fps.get(index)
                        self._latest_fps[index] = (
                            instant_fps if previous_fps is None else previous_fps * 0.85 + instant_fps * 0.15
                        )
                    self._latest_at[index] = now
                    with frame_lock:
                        self._latest_frames[index] = frame
                    frame_event.set()
                else:
                    consecutive_failures += 1
                    if consecutive_failures > 10:
                        if consecutive_failures > 100:
                            self._latest_jpegs.pop(index, None)
                            self._latest_at.pop(index, None)
                            self._latest_fps.pop(index, None)
                            consecutive_failures = 0
                        time.sleep(0.1)
                    else:
                        time.sleep(0.005)

        thread = Thread(target=read_loop, name=f"camera-reader-{index}", daemon=True)
        self._reader_threads[index] = thread
        thread.start()

    def _start_encoder(self, cv2: Any, index: int) -> None:
        # JPEG encode runs on its own thread so a slow encode never throttles
        # capture FPS. The UI fetches at most every 120ms anyway, so we encode
        # at ~30Hz and the snapshot endpoint just hands back the latest bytes.
        if index in self._encode_threads:
            return
        stop = Event()
        self._encode_stops[index] = stop
        frame_lock = self._frame_locks[index]
        frame_event = self._frame_events[index]
        target_period = 1.0 / 30.0
        encode_quality = int(os.environ.get("APPSTATION_JPEG_QUALITY", "78"))

        def encode_loop() -> None:
            last_encoded_at = 0.0
            while not stop.is_set():
                if not frame_event.wait(timeout=0.5):
                    continue
                frame_event.clear()
                now = time.monotonic()
                if now - last_encoded_at < target_period:
                    # Skip — we only need ~30 jpegs/sec for the preview.
                    continue
                with frame_lock:
                    frame = self._latest_frames.get(index)
                if frame is None:
                    continue
                try:
                    encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), encode_quality])
                except Exception:
                    continue
                if not encoded:
                    continue
                self._latest_jpegs[index] = bytes(buffer)
                last_encoded_at = now
                if hasattr(frame, "mean"):
                    try:
                        self._latest_mean[index] = [round(float(value), 2) for value in frame.mean(axis=(0, 1))]
                    except Exception:
                        pass

        thread = Thread(target=encode_loop, name=f"camera-encoder-{index}", daemon=True)
        self._encode_threads[index] = thread
        thread.start()

    def _capture_size(self, config: dict[str, Any], camera: str) -> tuple[int, int]:
        cameras = config.get("cameras", {})
        key = f"{camera}Resolution"
        if camera == "wrist_left":
            key = "wristLeftResolution"
        elif camera == "wrist_right":
            key = "wristRightResolution"
        configured = cameras.get(key)
        if configured:
            return self._parse_size(str(configured), CAMERA_CAPTURE_SIZES.get(camera, (1920, 1080)))
        shared_preview = str(cameras.get("previewResolution", "native"))
        if shared_preview.lower().replace(" ", "") != "native":
            return self._parse_size(shared_preview, CAMERA_CAPTURE_SIZES.get(camera, (1920, 1080)))
        return CAMERA_CAPTURE_SIZES.get(camera, (1920, 1080))

    def _parse_size(self, value: str, fallback: tuple[int, int]) -> tuple[int, int]:
        normalized = value.lower().replace(" ", "")
        if normalized == "native":
            return fallback
        if "x" not in normalized:
            return fallback
        width_raw, height_raw = normalized.split("x", 1)
        try:
            width = min(max(int(width_raw), 160), 4096)
            height = min(max(int(height_raw), 120), 2160)
        except ValueError:
            return fallback
        return width, height
