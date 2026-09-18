from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from threading import BoundedSemaphore, Lock, Thread
from typing import TypeVar, cast

T = TypeVar("T")


class BlockingCallTimeout(RuntimeError):
    pass


class BoundedLane:
    """独立、无等待队列的阻塞调用通道；超时任务返回前始终占用容量。"""

    def __init__(self, name: str, capacity: int) -> None:
        self.name = name
        self._slots = BoundedSemaphore(capacity)
        self._lock = Lock()
        self._active = 0
        self._closed = False

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def close(self) -> None:
        with self._lock:
            self._closed = True

    async def run(self, function: Callable[[], T], timeout_s: float) -> T:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"{self.name} channel is closed")
            if not self._slots.acquire(blocking=False):
                raise RuntimeError(f"{self.name} channel is busy; request was not queued")
            self._active += 1
        result: Future[T] = Future()

        def execute() -> None:
            running = False
            error: BaseException | None = None
            value: T | None = None
            try:
                running = result.set_running_or_notify_cancel()
                if running:
                    try:
                        value = function()
                    except BaseException as exc:
                        error = exc
            finally:
                with self._lock:
                    self._active -= 1
                    self._slots.release()
            if running:
                if error is not None:
                    result.set_exception(error)
                else:
                    result.set_result(cast(T, value))

        # 原生代码无法被安全强杀；daemon 保证永久阻塞不挂住 Python 的退出钩子。
        thread = Thread(target=execute, name=self.name, daemon=True)
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._active -= 1
                self._slots.release()
            raise
        wrapped = asyncio.wrap_future(result)
        wrapped.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout_s)
        except TimeoutError as exc:
            result.cancel()
            raise BlockingCallTimeout(f"{self.name} channel timed out; blocked capacity remains reserved") from exc
        except asyncio.CancelledError:
            result.cancel()
            raise
