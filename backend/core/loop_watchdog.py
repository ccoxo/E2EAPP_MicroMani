"""事件循环卡顿看门狗：循环超过阈值没有心跳时，把各线程调用栈带时间写入独立日志。

只读诊断，不改变调度：心跳协程每 poll_s 更新一次时间，独立线程发现心跳过期就记录现场。
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import sys
import threading
import time
import traceback
from pathlib import Path


class LoopStallWatchdog:
    def __init__(
        self,
        log_path: Path,
        *,
        threshold_s: float = 0.25,
        poll_s: float = 0.05,
        max_dumps_per_stall: int = 4,
        max_log_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.log_path = Path(log_path)
        self.threshold_s = threshold_s
        self.poll_s = poll_s
        self.max_dumps_per_stall = max_dumps_per_stall
        self.max_log_bytes = max_log_bytes
        self._last_beat = time.monotonic()
        self._loop_thread_id: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """在事件循环线程内调用：登记循环线程并启动心跳与看门线程。"""
        self._loop_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()
        self._task = loop.create_task(self._beat(), name="loop-stall-watchdog-beat")
        self._thread = threading.Thread(target=self._watch, name="loop-stall-watchdog", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1.0)

    async def _beat(self) -> None:
        while True:
            self._last_beat = time.monotonic()
            await asyncio.sleep(self.poll_s)

    def _watch(self) -> None:
        stall_started: float | None = None
        dumps = 0
        last_dump = 0.0
        while not self._stop.wait(self.poll_s):
            now = time.monotonic()
            lag = now - self._last_beat
            if lag >= self.threshold_s:
                if stall_started is None:
                    stall_started, dumps = self._last_beat, 0
                if dumps < self.max_dumps_per_stall and now - last_dump >= 0.1:
                    self._dump(lag)
                    dumps += 1
                    last_dump = now
            elif stall_started is not None:
                self._write(f"{self._stamp()} stall end: about {(self._last_beat - stall_started) * 1000:.0f} ms\n\n")
                stall_started = None

    @staticmethod
    def _stamp() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _dump(self, lag_s: float) -> None:
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        frames = sys._current_frames()
        lines = [f"{self._stamp()} loop stalled {lag_s * 1000:.0f} ms\n"]
        # 循环线程放最前且保留完整栈；其他线程只留最内层几帧，空闲等待的线程也照记，便于看谁在抢 GIL。
        ordered = sorted(frames.items(), key=lambda item: item[0] != self._loop_thread_id)
        for ident, frame in ordered:
            if ident == threading.get_ident():
                continue
            is_loop = ident == self._loop_thread_id
            stack = traceback.format_stack(frame)
            if not is_loop:
                stack = stack[-4:]
            label = "LOOP " if is_loop else ""
            lines.append(f"--- {label}thread {names.get(ident, '?')} ({ident})\n")
            lines.extend(stack)
        lines.append("\n")
        self._write("".join(lines))

    def _write(self, text: str) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > self.max_log_bytes:
                return
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass
