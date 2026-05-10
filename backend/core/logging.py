from __future__ import annotations

import time
from collections import deque

from backend.core.schemas import LogChannel, LogEntry, LogLevel


def now_ms() -> int:
    return int(time.time() * 1000)


class LogService:
    def __init__(self, max_entries: int = 5000) -> None:
        self._max_entries = max_entries
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._cursor = 0
        self.info("[BACKEND]", "Hardware backend initialized")

    def append(self, channel: LogChannel, level: LogLevel, msg: str) -> LogEntry:
        self._cursor += 1
        entry = LogEntry(id=self._cursor, ts=now_ms(), channel=channel, level=level, msg=msg)
        self._entries.append(entry)
        return entry

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
