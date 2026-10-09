from __future__ import annotations

import asyncio
import time
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
        deadline = time.monotonic() + timeout_s
        completed_at: float | None = None

        def execute() -> None:
            nonlocal completed_at
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
                completed_at = time.monotonic()
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
            value = await asyncio.wait_for(asyncio.shield(wrapped), timeout_s)
        except TimeoutError as exc:
            # 事件循环恢复晚不等于原生调用阻塞；只接纳截止前已完成的应答。
            if result.done() and not result.cancelled() and completed_at is not None and completed_at <= deadline:
                return result.result()
            result.cancel()
            raise BlockingCallTimeout(f"{self.name} channel timed out; blocked capacity remains reserved") from exc
        except asyncio.CancelledError:
            result.cancel()
            raise
        # 即使完成回调先于超时回调被调度，真正迟到的结果仍不能成功。
        if completed_at is None or completed_at > deadline:
            raise BlockingCallTimeout(f"{self.name} channel completed after deadline")
        return value
