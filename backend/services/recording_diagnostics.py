"""短时录制诊断：业务线程仅入队，不写盘，不记录图像或局部变量。"""

from __future__ import annotations

import gc
import json
import queue
import sys
import threading
import time
from pathlib import Path


class RecordingDiagnostics:
    def __init__(self, path: Path, *, duration_s: float = 180.0) -> None:
        self.path = path
        self.duration_s = duration_s
        self.events: queue.Queue = queue.Queue(maxsize=8192)
        self.dropped = 0
        self.error = ""
        self._stop = threading.Event()
        self._gc_started: dict[int, float] = {}
        self._thread = threading.Thread(target=self._run, name="recording-diagnostics", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def emit(self, kind: str, **fields) -> None:
        if self._stop.is_set():
            return
        try:
            self.events.put_nowait({"kind": kind, "python_monotonic_s": time.monotonic(), **fields})
        except queue.Full:
            self.dropped += 1

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _gc_callback(self, phase: str, info: dict) -> None:
        generation = info["generation"]
        if phase == "start":
            self._gc_started[generation] = time.monotonic()
        else:
            started = self._gc_started.pop(generation, None)
            if started is not None:
                elapsed = time.monotonic() - started
                if elapsed >= 0.01:
                    self.emit("gc_pause", started_s=started, elapsed_ms=elapsed * 1000,
                              generation=generation, collected=info.get("collected", 0))

    def _stacks(self) -> dict:
        names = {t.ident: t.name for t in threading.enumerate()}
        stacks = {}
        for ident, frame in sys._current_frames().items():
            if ident == threading.get_ident():
                continue
            stack = []
            for _ in range(12):
                if frame is None:
                    break
                stack.append([frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name])
                frame = frame.f_back
            stacks[str(ident)] = {"name": names.get(ident, "native"), "stack": stack}
        return stacks

    def _run(self) -> None:
        started = time.monotonic()
        next_stack = started
        next_flush = started + 1.0
        gc.callbacks.append(self._gc_callback)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8") as output:
                def write(row):
                    output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

                write({"kind": "start", "unix_s": time.time(), "python_monotonic_s": started})
                while not self._stop.is_set() and time.monotonic() - started < self.duration_s:
                    try:
                        write(self.events.get(timeout=0.02))
                    except queue.Empty:
                        pass
                    now = time.monotonic()
                    if now >= next_stack:
                        write({"kind": "stacks", "python_monotonic_s": now,
                               "sampler_lateness_ms": max(0, now - next_stack) * 1000,
                               "threads": self._stacks()})
                        next_stack = now + 0.1
                    if now >= next_flush:
                        output.flush()
                        next_flush = now + 1.0
                self._stop.set()
                while True:
                    try:
                        write(self.events.get_nowait())
                    except queue.Empty:
                        break
                write({"kind": "end", "dropped_events": self.dropped})
        except Exception as exc:
            self.error = str(exc)
        finally:
            self._stop.set()
            gc.callbacks.remove(self._gc_callback)
