from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path

from backend.hal_client.dds_types import (
    TOPIC_HAL_HEALTH,
    TOPIC_HAL_MOTION_STATE,
    TOPIC_HAL_NATIVE_TELEOP_STATUS,
    TOPIC_HAL_OMEGA_STATE,
    HalCommandReply,
    HalCommandRequest,
    JsonEnvelope,
)

_RESULT_NO_DATA = 0
_RESULT_OK = 1
_RESULT_ERROR = -1
_RESULT_BUFFER_TOO_SMALL = -2

_ERROR_CAPACITY = 4096
_INITIAL_SOURCE_CAPACITY = 256
_INITIAL_PAYLOAD_CAPACITY = 65536

_TOPIC_IDS = {
    TOPIC_HAL_HEALTH: 0,
    TOPIC_HAL_MOTION_STATE: 1,
    TOPIC_HAL_OMEGA_STATE: 2,
    TOPIC_HAL_NATIVE_TELEOP_STATUS: 3,
}


class FastDdsBindingUnavailableError(RuntimeError):
    pass


class _FastDdsLibrary:
    def __init__(self, dll_path: Path) -> None:
        self.dll_path = dll_path
        self._dll_directory_handles: list[object] = []
        if not dll_path.exists():
            raise FastDdsBindingUnavailableError(
                "Fast-DDS Python bindings are required: build "
                "backend\\native\\build_fastdds_transport.cmd first or set APPSTATION_FASTDDS_BINDING_DLL"
            )
        self._add_dll_directories(dll_path)
        try:
            self.lib = ctypes.CDLL(str(dll_path))
        except OSError as exc:
            raise FastDdsBindingUnavailableError(
                f"Fast-DDS Python bindings are required: failed to load {dll_path}: {exc}"
            ) from exc
        self._configure_signatures()

    def _add_dll_directories(self, dll_path: Path) -> None:
        # Python 只绑定本项目 C++ DLL；Fast-DDS 依赖 DLL 仍从 F:\opt\ros\jazzy\bin 加载。
        candidates = [dll_path.parent]
        fastdds_root = Path(os.environ.get("APPSTATION_FASTDDS_ROOT", r"F:\opt\ros\jazzy"))
        candidates.append(fastdds_root / "bin")
        candidates.append(fastdds_root / ".pixi" / "envs" / "default" / "Library" / "bin")
        for directory in candidates:
            if directory.exists():
                os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
                if hasattr(os, "add_dll_directory"):
                    self._dll_directory_handles.append(os.add_dll_directory(str(directory)))

    def _configure_signatures(self) -> None:
        self.lib.appstation_fastdds_version.argtypes = []
        self.lib.appstation_fastdds_version.restype = ctypes.c_char_p

        self.lib.appstation_fastdds_create.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.lib.appstation_fastdds_create.restype = ctypes.c_void_p

        self.lib.appstation_fastdds_start.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.lib.appstation_fastdds_start.restype = ctypes.c_int

        self.lib.appstation_fastdds_close.argtypes = [ctypes.c_void_p]
        self.lib.appstation_fastdds_close.restype = None

        self.lib.appstation_fastdds_destroy.argtypes = [ctypes.c_void_p]
        self.lib.appstation_fastdds_destroy.restype = None

        self.lib.appstation_fastdds_get_latest.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self.lib.appstation_fastdds_get_latest.restype = ctypes.c_int

        self.lib.appstation_fastdds_publish_command_request.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self.lib.appstation_fastdds_publish_command_request.restype = ctypes.c_int

        self.lib.appstation_fastdds_publish_emergency_stop.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self.lib.appstation_fastdds_publish_emergency_stop.restype = ctypes.c_int

        self.lib.appstation_fastdds_wait_for_command_reply.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self.lib.appstation_fastdds_wait_for_command_reply.restype = ctypes.c_int



_LIBRARY_LOCK = threading.Lock()
_LIBRARY: _FastDdsLibrary | None = None


def _default_dll_path() -> Path:
    override = os.environ.get("APPSTATION_FASTDDS_BINDING_DLL")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "backend" / "native" / "build" / "appstation_fastdds_transport.dll"


def _load_library() -> _FastDdsLibrary:
    global _LIBRARY
    with _LIBRARY_LOCK:
        dll_path = _default_dll_path()
        if _LIBRARY is None or _LIBRARY.dll_path != dll_path:
            _LIBRARY = _FastDdsLibrary(dll_path)
        return _LIBRARY


def _error_buffer() -> ctypes.Array[ctypes.c_char]:
    return ctypes.create_string_buffer(_ERROR_CAPACITY)


def _buffer_text(buffer: ctypes.Array[ctypes.c_char]) -> str:
    return bytes(buffer.value).decode("utf-8", errors="replace")


def _raise_error(prefix: str, error: ctypes.Array[ctypes.c_char]) -> None:
    detail = _buffer_text(error)
    raise RuntimeError(f"{prefix}: {detail}" if detail else prefix)


class FastDdsHalTransport:
    def __init__(self, *, domain_id: int) -> None:
        self._library = _load_library()
        self._handle: int | None = None
        error = _error_buffer()
        handle = self._library.lib.appstation_fastdds_create(int(domain_id), error, _ERROR_CAPACITY)
        if not handle:
            _raise_error("Fast-DDS transport create failed", error)
        self._handle = int(handle)
        self._closed = False

    def start(self) -> None:
        handle = self._require_handle()
        error = _error_buffer()
        result = self._library.lib.appstation_fastdds_start(handle, error, _ERROR_CAPACITY)
        if result != _RESULT_OK:
            _raise_error("Fast-DDS transport start failed", error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handle is not None:
            self._library.lib.appstation_fastdds_close(self._handle)
            self._library.lib.appstation_fastdds_destroy(self._handle)
            self._handle = None

    def get_latest(self, topic_name: str) -> JsonEnvelope | None:
        topic_id = _TOPIC_IDS.get(topic_name)
        if topic_id is None:
            raise RuntimeError(f"DDS topic is not mapped: {topic_name}")

        source_capacity = _INITIAL_SOURCE_CAPACITY
        payload_capacity = _INITIAL_PAYLOAD_CAPACITY
        while True:
            stamp_unix_ms = ctypes.c_uint64()
            stamp_monotonic_ms = ctypes.c_uint64()
            source_required = ctypes.c_int()
            payload_required = ctypes.c_int()
            source = ctypes.create_string_buffer(source_capacity)
            payload = ctypes.create_string_buffer(payload_capacity)
            error = _error_buffer()
            result = self._library.lib.appstation_fastdds_get_latest(
                self._require_handle(),
                topic_id,
                ctypes.byref(stamp_unix_ms),
                ctypes.byref(stamp_monotonic_ms),
                source,
                source_capacity,
                ctypes.byref(source_required),
                payload,
                payload_capacity,
                ctypes.byref(payload_required),
                error,
                _ERROR_CAPACITY,
            )
            if result == _RESULT_NO_DATA:
                return None
            if result == _RESULT_ERROR:
                _raise_error("Fast-DDS get_latest failed", error)
            if result == _RESULT_BUFFER_TOO_SMALL:
                source_capacity = max(source_capacity * 2, source_required.value)
                payload_capacity = max(payload_capacity * 2, payload_required.value)
                continue
            if result != _RESULT_OK:
                raise RuntimeError(f"Fast-DDS get_latest returned unexpected code: {result}")
            return JsonEnvelope(
                stamp_unix_ms=int(stamp_unix_ms.value),
                stamp_monotonic_ms=int(stamp_monotonic_ms.value),
                source=_buffer_text(source),
                payload_json=_buffer_text(payload),
            )

    def publish_command_request(self, request: HalCommandRequest) -> None:
        error = _error_buffer()
        result = self._library.lib.appstation_fastdds_publish_command_request(
            self._require_handle(),
            request.request_id.encode("utf-8"),
            int(request.stamp_unix_ms),
            request.name.encode("utf-8"),
            request.payload_json.encode("utf-8"),
            error,
            _ERROR_CAPACITY,
        )
        if result != _RESULT_OK:
            _raise_error("Fast-DDS publish command request failed", error)

    def publish_emergency_stop(self, request: HalCommandRequest) -> None:
        error = _error_buffer()
        result = self._library.lib.appstation_fastdds_publish_emergency_stop(
            self._require_handle(),
            request.request_id.encode("utf-8"),
            int(request.stamp_unix_ms),
            request.name.encode("utf-8"),
            request.payload_json.encode("utf-8"),
            error,
            _ERROR_CAPACITY,
        )
        if result != _RESULT_OK:
            _raise_error("Fast-DDS publish emergency stop failed", error)

    def wait_for_command_reply(self, request_id: str, timeout_s: float) -> HalCommandReply | None:
        result_capacity = _INITIAL_PAYLOAD_CAPACITY
        error_capacity = _INITIAL_PAYLOAD_CAPACITY
        timeout_ms = max(int(timeout_s * 1000), 0)
        while True:
            ok = ctypes.c_int()
            result_required = ctypes.c_int()
            reply_error_required = ctypes.c_int()
            result_json = ctypes.create_string_buffer(result_capacity)
            reply_error = ctypes.create_string_buffer(error_capacity)
            error = _error_buffer()
            result = self._library.lib.appstation_fastdds_wait_for_command_reply(
                self._require_handle(),
                request_id.encode("utf-8"),
                timeout_ms,
                ctypes.byref(ok),
                result_json,
                result_capacity,
                ctypes.byref(result_required),
                reply_error,
                error_capacity,
                ctypes.byref(reply_error_required),
                error,
                _ERROR_CAPACITY,
            )
            if result == _RESULT_NO_DATA:
                return None
            if result == _RESULT_ERROR:
                _raise_error("Fast-DDS wait command reply failed", error)
            if result == _RESULT_BUFFER_TOO_SMALL:
                result_capacity = max(result_capacity * 2, result_required.value)
                error_capacity = max(error_capacity * 2, reply_error_required.value)
                continue
            if result != _RESULT_OK:
                raise RuntimeError(f"Fast-DDS wait command reply returned unexpected code: {result}")
            return HalCommandReply(
                request_id=request_id,
                ok=bool(ok.value),
                result_json=_buffer_text(result_json),
                error=_buffer_text(reply_error),
            )

    def _require_handle(self) -> int:
        if self._handle is None:
            raise RuntimeError("Fast-DDS transport is closed")
        return self._handle
