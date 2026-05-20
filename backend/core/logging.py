from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.schemas import LogChannel, LogEntry, LogLevel

LOG_SCHEMA_VERSION = "e2e-diagnostics-v1"


def now_ms() -> int:
    return int(time.time() * 1000)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def default_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def stable_config_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def _format_log_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, str):
        if value == "":
            return '""'
        if any(ch.isspace() for ch in value) or '"' in value or "\\" in value:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return value
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return "[" + ",".join(_format_log_value(item) for item in value) + "]"
    return _format_log_value(str(value))


class LogService:
    def __init__(
        self,
        max_entries: int = 5000,
        *,
        clock_ms: Callable[[], int] = now_ms,
        monotonic_ms: Callable[[], int] = monotonic_ms,
        session_id: str | None = None,
        emit_startup: bool = True,
        log_file_path: str | Path | None = None,
        max_log_files: int = 50,
        max_log_total_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self._max_entries = max_entries
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._cursor = 0
        self._clock_ms = clock_ms
        self._monotonic_ms = monotonic_ms
        self.session_id = session_id or default_session_id()
        self._log_file_path = Path(log_file_path) if log_file_path is not None else None
        if self._log_file_path is not None:
            self._log_file_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file_path.touch(exist_ok=True)
            self._prune_log_files(max_log_files, max_log_total_bytes)
        self._op_counters: defaultdict[str, int] = defaultdict(int)
        self._last_rate_log_ms: dict[str, int] = {}
        if emit_startup:
            self.info("[BACKEND]", "Hardware backend initialized")

    def _prune_log_files(self, max_log_files: int, max_log_total_bytes: int) -> None:
        if self._log_file_path is None or (max_log_files <= 0 and max_log_total_bytes <= 0):
            return
        log_dir = self._log_file_path.parent
        current = self._log_file_path.resolve()
        candidates = sorted(
            (path for path in log_dir.glob("appstation-m0-*.log") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        kept: list[Path] = []
        for index, path in enumerate(candidates):
            if max_log_files > 0 and index >= max_log_files and path.resolve() != current:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            kept.append(path)
        if max_log_total_bytes <= 0:
            return
        total_bytes = sum(path.stat().st_size for path in kept if path.exists())
        for path in reversed(kept):
            if total_bytes <= max_log_total_bytes:
                break
            if path.resolve() == current:
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                total_bytes -= size
            except OSError:
                pass

    def append(self, channel: LogChannel, level: LogLevel, msg: str) -> LogEntry:
        self._cursor += 1
        entry = LogEntry(id=self._cursor, ts=self._clock_ms(), channel=channel, level=level, msg=msg)
        self._entries.append(entry)
        if self._log_file_path is not None:
            line = f"{entry.ts} {entry.channel} {entry.level} {entry.msg}\n"
            with self._log_file_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return entry

    def event(
        self,
        log_channel: LogChannel,
        level: LogLevel,
        event: str,
        *,
        component: str,
        op_id: str | None = None,
        rate_key: str | None = None,
        rate_ms: int | None = None,
        **fields: Any,
    ) -> LogEntry | None:
        mono = self._monotonic_ms()
        if rate_key is not None and rate_ms is not None:
            last = self._last_rate_log_ms.get(rate_key)
            if last is not None and mono - last < rate_ms:
                return None
            self._last_rate_log_ms[rate_key] = mono
        base_fields: dict[str, Any] = {
            "component": component,
            "event": event,
            "seq": self._cursor + 1,
            "monoMs": mono,
            "session_id": self.session_id,
            "op_id": op_id or "-",
        }
        base_fields.update(fields)
        message = " ".join(f"{key}={_format_log_value(value)}" for key, value in base_fields.items())
        return self.append(log_channel, level, message)

    def new_op_id(self, prefix: str) -> str:
        self._op_counters[prefix] += 1
        return f"{prefix}_{self._op_counters[prefix]}"

    def info(self, channel: LogChannel, msg: str) -> LogEntry:
        return self.append(channel, "INFO", msg)

    def warning(self, channel: LogChannel, msg: str) -> LogEntry:
        return self.append(channel, "WARNING", msg)

    def error(self, channel: LogChannel, msg: str) -> LogEntry:
        return self.append(channel, "ERROR", msg)

    def entries_after(self, last_id: int) -> list[LogEntry]:
        return [entry for entry in self._entries if entry.id > last_id]

    def list_entries(self) -> list[LogEntry]:
        return list(self._entries)
