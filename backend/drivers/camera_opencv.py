from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from importlib import import_module
from threading import Event, Lock, Thread, current_thread
from typing import Any

from backend.core.schemas import CameraTelemetry, ConnectionState

# CAMERA_CAPTURE_SIZES: dict[str, tuple[int, int]] = {
#     "global": (1920, 1080),
#     "wrist_left": (1920, 1080),
#     "wrist_right": (1920, 1080),
# }

# 三路相机默认采集分辨率
CAMERA_CAPTURE_SIZES: dict[str, tuple[int, int]] = {
    "global": (640, 480),
    "wrist_left": (640, 480),
    "wrist_right": (640, 480),
}

CAMERA_IDENTITY_KEYS = {
    "global": "globalIdentity",
    "wrist_left": "wristLeftIdentity",
    "wrist_right": "wristRightIdentity",
}
CAMERA_DESCRIPTOR_KEYS = {
    "global": "global",
    "wrist_left": "wristLeft",
    "wrist_right": "wristRight",
}
CAMERA_INDEX_FALLBACKS = {
    "global": -1,
    "wrist_left": 2,
    "wrist_right": 0,
}
IDENTITY_CACHE_TTL_S = 30.0
IDENTITY_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")
CAMERA_STALE_FRAME_SEC = 2.0
CAMERA_INITIAL_FRAME_GRACE_SEC = 1.5
CAMERA_FRAME_WAIT_SEC = 1.5
CAMERA_TUNING_DEFAULTS: dict[str, dict[str, float | bool]] = {
    "global": {
        "autoExposure": False,
        "exposure": -5.5,
        "gain": 0.0,
        "autoWhiteBalance": False,
    },
    "wrist_left": {
        "autoExposure": False,
        "exposure": -6.0,
        "gain": 0.0,
        "autoWhiteBalance": False,
    },
    "wrist_right": {
        "autoExposure": False,
        "exposure": -6.0,
        "gain": 0.0,
        "autoWhiteBalance": False,
    },
}

DIRECTSHOW_CAMERA_ENUM_SCRIPT = r"""
$code = @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

public static class AppstationDirectShowCameraEnum
{
    [ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface ICreateDevEnum
    {
        int CreateClassEnumerator(
            [In] ref Guid pType,
            out IEnumMoniker ppEnumMoniker,
            int dwFlags);
    }

    [ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyBag
    {
        [PreserveSig]
        int Read(
            [MarshalAs(UnmanagedType.LPWStr)] string pszPropName,
            ref object pVar,
            IntPtr pErrorLog);

        [PreserveSig]
        int Write(
            [MarshalAs(UnmanagedType.LPWStr)] string pszPropName,
            ref object pVar);
    }

    public static string List()
    {
        var sb = new StringBuilder();
        Guid systemDeviceEnum = new Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86");
        Guid videoInputDevice = new Guid("860BB310-5D01-11D0-BD3B-00A0C911CE86");
        Type type = Type.GetTypeFromCLSID(systemDeviceEnum);
        var devEnum = (ICreateDevEnum)Activator.CreateInstance(type);
        IEnumMoniker enumMoniker;
        int hr = devEnum.CreateClassEnumerator(ref videoInputDevice, out enumMoniker, 0);
        if (hr != 0 || enumMoniker == null) return "NO_DEVICES hr=" + hr;

        IMoniker[] monikers = new IMoniker[1];
        IntPtr fetched = Marshal.AllocCoTaskMem(4);
        int index = 0;
        try
        {
            while (enumMoniker.Next(1, monikers, fetched) == 0)
            {
                string friendly = ReadBag(monikers[0], "FriendlyName");
                string devicePath = ReadBag(monikers[0], "DevicePath");
                string displayName = "";
                try
                {
                    IBindCtx ctx;
                    CreateBindCtx(0, out ctx);
                    monikers[0].GetDisplayName(ctx, null, out displayName);
                }
                catch { }

                sb.AppendLine("INDEX=" + index);
                sb.AppendLine("FriendlyName=" + friendly);
                sb.AppendLine("DevicePath=" + devicePath);
                sb.AppendLine("DisplayName=" + displayName);
                sb.AppendLine();
                Marshal.ReleaseComObject(monikers[0]);
                index++;
            }
        }
        finally
        {
            Marshal.FreeCoTaskMem(fetched);
            Marshal.ReleaseComObject(enumMoniker);
            Marshal.ReleaseComObject(devEnum);
        }
        return sb.ToString();
    }

    static string ReadBag(IMoniker moniker, string name)
    {
        object bagObj;
        Guid bagId = typeof(IPropertyBag).GUID;
        try
        {
            moniker.BindToStorage(null, null, ref bagId, out bagObj);
            var bag = (IPropertyBag)bagObj;
            object value = null;
            int hr = bag.Read(name, ref value, IntPtr.Zero);
            Marshal.ReleaseComObject(bag);
            if (hr == 0 && value != null) return value.ToString();
        }
        catch { }
        return "";
    }

    [DllImport("ole32.dll")]
    static extern int CreateBindCtx(int reserved, out IBindCtx ppbc);
}
'@
Add-Type -TypeDefinition $code -Language CSharp
[AppstationDirectShowCameraEnum]::List()
"""


def _identity_token(value: str) -> str:
    return IDENTITY_TOKEN_PATTERN.sub("", value.lower())


def _identity_matches(expected: str, identity: dict[str, str]) -> bool:
    expected_token = _identity_token(expected)
    if not expected_token:
        return False
    haystack = _identity_token(
        " ".join(
            [
                identity.get("name", ""),
                identity.get("devicePath", ""),
                identity.get("displayName", ""),
            ]
        )
    )
    return expected_token in haystack

@dataclass
class CameraProbeResult:
    ok: bool
    message: str
    cameras: list[CameraTelemetry]


@dataclass(frozen=True)
class CameraFrameSnapshot:
    frame: Any
    monotonic_s: float


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
    def __init__(self, logs: Any | None = None) -> None:
        self._logs = logs
        self._last_probe = 0.0
        self._cached: CameraProbeResult | None = None
        self._capture_lock = Lock()
        self._captures: dict[int, Any] = {}
        self._capture_sizes: dict[int, tuple[int, int, float]] = {}
        self._capture_opened_at: dict[int, float] = {}
        self._capture_roles: dict[int, str] = {}
        self._capture_backend_labels: dict[int, str] = {}
        self._stale_indices: set[int] = set()
        self._latest_jpegs: dict[int, bytes] = {}
        self._latest_sequences: dict[int, int] = {}
        self._latest_at: dict[int, float] = {}
        self._latest_fps: dict[int, float] = {}
        self._latest_mean: dict[int, list[float]] = {}
        self._jpeg_events: dict[int, Event] = {}
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
        self._identity_cache_at = 0.0
        self._identity_cache: dict[int, dict[str, str]] | None = None
        self._backend_label: str = ""

    def _log(self, level: str, message: str) -> None:
        if self._logs is None:
            return
        try:
            if level == "warning":
                self._logs.warning("[CAMERA]", message)
            elif level == "error":
                self._logs.error("[CAMERA]", message)
            else:
                self._logs.info("[CAMERA]", message)
        except Exception:
            pass

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
                capture = self._get_capture(cv2, index, width, height, fps_config, key, config)
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
            capture = self._get_capture(cv2, index, width, height, fps_config, camera, config)
            if capture is None:
                raise RuntimeError(f"{camera} index {index} open failed")
        deadline = time.monotonic() + CAMERA_FRAME_WAIT_SEC
        while time.monotonic() < deadline:
            cached = self._latest_jpegs.get(index)
            if cached is not None:
                return cached
            event = self._jpeg_events.get(index)
            if event is None:
                time.sleep(0.01)
                continue
            event.wait(timeout=0.05)
            event.clear()
        raise RuntimeError(f"{camera} index {index} frame read failed")

    def snapshot_frame(self, config: dict[str, Any], camera: str) -> Any:
        return self.snapshot_frame_with_timestamp(config, camera).frame

    def snapshot_frame_with_timestamp(self, config: dict[str, Any], camera: str) -> CameraFrameSnapshot:
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
            capture = self._get_capture(cv2, index, width, height, fps_config, camera, config)
            if capture is None:
                raise RuntimeError(f"{camera} index {index} open failed")
        deadline = time.monotonic() + CAMERA_FRAME_WAIT_SEC
        while time.monotonic() < deadline:
            frame_lock = self._frame_locks.get(index)
            if frame_lock is not None:
                with frame_lock:
                    frame = self._latest_frames.get(index)
                    latest_at = self._latest_at.get(index)
                    if frame is not None and hasattr(frame, "copy"):
                        frame = frame.copy()
            else:
                frame = None
                latest_at = None
            if frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                return CameraFrameSnapshot(rgb, float(latest_at or time.monotonic()))
            event = self._frame_events.get(index)
            if event is None:
                time.sleep(0.01)
                continue
            event.wait(timeout=0.05)
            event.clear()
        raise RuntimeError(f"{camera} index {index} frame read failed")

    def wait_for_frame(
        self,
        config: dict[str, Any],
        camera: str,
        last_sequence: int = -1,
        timeout: float = CAMERA_FRAME_WAIT_SEC,
    ) -> tuple[int, bytes]:
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
            capture = self._get_capture(cv2, index, width, height, fps_config, camera, config)
            if capture is None:
                raise RuntimeError(f"{camera} index {index} open failed")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sequence = self._latest_sequences.get(index, -1)
            cached = self._latest_jpegs.get(index)
            if cached is not None and sequence != last_sequence:
                return sequence, cached
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        cached = self._latest_jpegs.get(index)
        sequence = self._latest_sequences.get(index, -1)
        if cached is not None:
            return sequence, cached
        raise RuntimeError(f"{camera} index {index} frame read failed")

    def apply_tuning(self, config: dict[str, Any], camera: str) -> dict[str, Any]:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise RuntimeError(f"unknown camera: {camera}")
        try:
            cv2 = import_module("cv2")
        except Exception as exc:
            raise RuntimeError(f"OpenCV import failed: {exc}") from exc

        fps_config = float(config["cameras"].get("fps", 30))
        resolved = self._resolved_indices(cv2, config, fps_config)
        index = resolved[camera]
        width, height = self._capture_size(config, camera)
        profile = self._camera_tuning(config, camera)
        with self._capture_lock:
            capture = self._get_capture(cv2, index, width, height, fps_config, camera, config)
            if capture is None:
                raise RuntimeError(f"{camera} index {index} open failed")
            actual = self._apply_tuning(cv2, capture, profile)
            self._clear_probe_cache()
        self._log("info", f"{camera} tuning applied on index {index}: {profile}")
        return {
            "camera": camera,
            "index": index,
            "profile": profile,
            "actual": actual,
        }

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
                capture = self._get_capture(cv2, index, width, height, fps_config, probe_role, config)
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
            *(str(cameras.get(key, "index -1")) for key in CAMERA_DESCRIPTOR_KEYS.values()),
            *(str(cameras.get(key, "")) for key in CAMERA_IDENTITY_KEYS.values()),
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
            role: self._parse_index(
                str(cameras.get(config_key, f"index {CAMERA_INDEX_FALLBACKS[role]}")),
                CAMERA_INDEX_FALLBACKS[role],
            )
            for role, config_key in CAMERA_DESCRIPTOR_KEYS.items()
        }
        resolved.update(self._resolve_indices_by_identity(cameras))
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

    def _resolve_indices_by_identity(self, cameras: dict[str, Any]) -> dict[str, int]:
        configured = {
            role: str(cameras.get(config_key, "")).strip()
            for role, config_key in CAMERA_IDENTITY_KEYS.items()
        }
        if not any(configured.values()):
            return {}

        identities = self._camera_identities_by_index()
        resolved: dict[str, int] = {}
        used_indices: set[int] = set()
        for role, expected in configured.items():
            if not expected:
                continue
            for index, identity in identities.items():
                if index in used_indices:
                    continue
                if _identity_matches(expected, identity):
                    resolved[role] = index
                    used_indices.add(index)
                    break
        return resolved

    def _camera_identities_by_index(self) -> dict[int, dict[str, str]]:
        if os.name != "nt":
            return {}
        now = time.monotonic()
        if self._identity_cache is not None and now - self._identity_cache_at < IDENTITY_CACHE_TTL_S:
            return {index: dict(value) for index, value in self._identity_cache.items()}
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", DIRECTSHOW_CAMERA_ENUM_SCRIPT],
                capture_output=True,
                check=False,
                creationflags=creationflags,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if completed.returncode != 0:
            return {}
        identities = self._parse_directshow_identity_output(completed.stdout)
        self._identity_cache = identities
        self._identity_cache_at = now
        return {index: dict(value) for index, value in identities.items()}

    def _parse_directshow_identity_output(self, output: str) -> dict[int, dict[str, str]]:
        identities: dict[int, dict[str, str]] = {}
        current_index: int | None = None
        current: dict[str, str] = {}

        def store_current() -> None:
            if current_index is None:
                return
            identities[current_index] = {
                "name": current.get("FriendlyName", ""),
                "devicePath": current.get("DevicePath", ""),
                "displayName": current.get("DisplayName", ""),
            }

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("INDEX="):
                store_current()
                current = {}
                try:
                    current_index = int(stripped.split("=", 1)[1])
                except ValueError:
                    current_index = None
                continue
            if "=" in stripped:
                key, value = stripped.split("=", 1)
                current[key] = value
        store_current()
        return identities

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
            if index in self._latest_jpegs:
                readable.append(index)
        return readable

    def _get_capture(
        self,
        cv2: Any,
        index: int,
        width: int,
        height: int,
        fps: float,
        camera: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> Any | None:
        if index < 0:
            return None
        capture = self._captures.get(index)
        if capture is not None and self._capture_usable(capture, index, (width, height, fps)):
            return capture
        if capture is not None:
            self._log("warning", f"camera index {index} reopening; stale or mismatched capture")
        self._drop_capture(index)

        capture = None
        backend_label = ""
        profile = self._camera_tuning(config, camera) if config is not None and camera is not None else None
        for backend, label in _backend_candidates(cv2):
            candidate = cv2.VideoCapture(index, backend)
            if not candidate.isOpened():
                candidate.release()
                continue
            self._configure_capture(cv2, candidate, width, height, fps, profile)
            capture = candidate
            backend_label = label
            break
        if capture is None:
            return None
        self._backend_label = backend_label

        self._captures[index] = capture
        self._capture_sizes[index] = (width, height, fps)
        self._capture_opened_at[index] = time.monotonic()
        if camera is not None:
            self._capture_roles[index] = camera
        self._capture_backend_labels[index] = backend_label
        self._stale_indices.discard(index)
        self._frame_locks[index] = Lock()
        self._frame_events[index] = Event()
        self._jpeg_events[index] = Event()
        self._start_reader(cv2, index, capture)
        self._start_encoder(cv2, index)
        self._log("info", f"{camera or 'camera'} opened index {index} via {backend_label} {width}x{height}@{fps:g}")
        return capture

    def _capture_usable(self, capture: Any, index: int, size: tuple[int, int, float]) -> bool:
        if index in self._stale_indices:
            return False
        if not capture.isOpened() or self._capture_sizes.get(index) != size:
            return False
        reader = self._reader_threads.get(index)
        encoder = self._encode_threads.get(index)
        if reader is None or not reader.is_alive() or encoder is None or not encoder.is_alive():
            return False
        latest_at = self._latest_at.get(index)
        if latest_at is not None:
            return time.monotonic() - latest_at <= CAMERA_STALE_FRAME_SEC
        opened_at = self._capture_opened_at.get(index)
        return opened_at is None or time.monotonic() - opened_at <= CAMERA_INITIAL_FRAME_GRACE_SEC

    def _configure_capture(
        self,
        cv2: Any,
        capture: Any,
        width: int,
        height: int,
        fps: float,
        profile: dict[str, float | bool] | None = None,
    ) -> None:
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
        if profile is not None:
            self._apply_tuning(cv2, capture, profile)

    def _camera_tuning(self, config: dict[str, Any] | None, camera: str | None) -> dict[str, float | bool]:
        role = camera if camera in CAMERA_TUNING_DEFAULTS else "global"
        profile = dict(CAMERA_TUNING_DEFAULTS[role])
        if config is not None:
            raw_tuning = config.get("cameras", {}).get("tuning", {})
            if isinstance(raw_tuning, dict) and isinstance(raw_tuning.get(role), dict):
                profile.update(raw_tuning[role])
        profile["autoExposure"] = bool(profile.get("autoExposure", False))
        profile["exposure"] = self._clamp_float(
            profile.get("exposure"),
            -13.0,
            0.0,
            float(CAMERA_TUNING_DEFAULTS[role]["exposure"]),
        )
        profile["gain"] = self._clamp_float(
            profile.get("gain"),
            0.0,
            64.0,
            float(CAMERA_TUNING_DEFAULTS[role]["gain"]),
        )
        profile["autoWhiteBalance"] = bool(profile.get("autoWhiteBalance", False))
        if role in {"wrist_left", "wrist_right"}:
            profile["autoExposure"] = False
            profile["exposure"] = min(float(profile["exposure"]), -5.0)
        return profile

    def _apply_tuning(self, cv2: Any, capture: Any, profile: dict[str, float | bool]) -> dict[str, float | None]:
        self._safe_set(cv2, capture, "CAP_PROP_AUTOFOCUS", 0.0)
        auto_exposure = bool(profile["autoExposure"])
        self._safe_set(cv2, capture, "CAP_PROP_AUTO_EXPOSURE", 0.75 if auto_exposure else 0.25)
        if not auto_exposure:
            self._safe_set(cv2, capture, "CAP_PROP_EXPOSURE", float(profile["exposure"]))
        self._safe_set(cv2, capture, "CAP_PROP_GAIN", float(profile["gain"]))
        self._safe_set(cv2, capture, "CAP_PROP_AUTO_WB", 1.0 if bool(profile["autoWhiteBalance"]) else 0.0)
        return {
            "autoExposure": self._safe_get(cv2, capture, "CAP_PROP_AUTO_EXPOSURE"),
            "exposure": self._safe_get(cv2, capture, "CAP_PROP_EXPOSURE"),
            "gain": self._safe_get(cv2, capture, "CAP_PROP_GAIN"),
            "autoWhiteBalance": self._safe_get(cv2, capture, "CAP_PROP_AUTO_WB"),
        }

    def _safe_set(self, cv2: Any, capture: Any, prop_name: str, value: float) -> None:
        if not hasattr(cv2, prop_name):
            return
        try:
            capture.set(getattr(cv2, prop_name), value)
        except Exception:
            return

    def _safe_get(self, cv2: Any, capture: Any, prop_name: str) -> float | None:
        if not hasattr(cv2, prop_name):
            return None
        try:
            return float(capture.get(getattr(cv2, prop_name)))
        except Exception:
            return None

    def _clamp_float(self, value: object, low: float, high: float, fallback: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = fallback
        return min(high, max(low, parsed))

    def _drop_capture(self, index: int) -> None:
        for stops, threads in (
            (self._reader_stops, self._reader_threads),
            (self._encode_stops, self._encode_threads),
        ):
            stop = stops.pop(index, None)
            if stop is not None:
                stop.set()
            thread = threads.pop(index, None)
            if thread is not None and thread.is_alive() and thread is not current_thread():
                thread.join(timeout=0.3)
        capture = self._captures.pop(index, None)
        self._capture_sizes.pop(index, None)
        self._capture_opened_at.pop(index, None)
        self._capture_roles.pop(index, None)
        self._capture_backend_labels.pop(index, None)
        self._stale_indices.discard(index)
        self._latest_jpegs.pop(index, None)
        self._latest_sequences.pop(index, None)
        self._latest_at.pop(index, None)
        self._latest_fps.pop(index, None)
        self._latest_mean.pop(index, None)
        self._jpeg_events.pop(index, None)
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
            try:
                while not stop.is_set():
                    try:
                        ok, frame = capture.read()
                    except Exception:
                        ok, frame = False, None
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
                            if consecutive_failures > 30:
                                self._latest_jpegs.pop(index, None)
                                self._latest_sequences.pop(index, None)
                                self._latest_at.pop(index, None)
                                self._latest_fps.pop(index, None)
                                self._stale_indices.add(index)
                                self._log("warning", f"camera index {index} reader stopped after read failures")
                                break
                            time.sleep(0.1)
                        else:
                            time.sleep(0.005)
            finally:
                if self._reader_threads.get(index) is current_thread():
                    self._reader_threads.pop(index, None)
                    self._reader_stops.pop(index, None)

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
                self._latest_sequences[index] = self._latest_sequences.get(index, 0) + 1
                event = self._jpeg_events.get(index)
                if event is not None:
                    event.set()
                last_encoded_at = now
                if hasattr(frame, "mean"):
                    try:
                        self._latest_mean[index] = [round(float(value), 2) for value in frame.mean(axis=(0, 1))]
                    except Exception:
                        pass
            if self._encode_threads.get(index) is current_thread():
                self._encode_threads.pop(index, None)
                self._encode_stops.pop(index, None)

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
