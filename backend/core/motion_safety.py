from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any
import asyncio
import logging


def motion_operation(parameter: str | None = None):
    """服务入口共用资源互斥；嵌套调用由同一任务持有，不能跨任务继承。"""
    def decorate(function):
        @wraps(function)
        async def run(self, *args: Any, **kwargs: Any):
            value = args[0] if parameter and args else kwargs.get(parameter) if parameter else None
            side = getattr(value, "side", value)
            token = self.safety.capture(side)
            self.safety.check(token)
            with self.safety.operation(side):
                result = await function(self, *args, **kwargs)
                self.safety.check(token)
                return result
        return run
    return decorate


@dataclass(frozen=True)
class MotionSafetyToken:
    side: str | None
    generation: tuple[int, int, int]


class MotionSafetyGate:
    """事件循环内共享的停止代际；硬件拒绝与锁存仍由 HAL 执行。"""

    def __init__(self) -> None:
        self._generation = 0
        self._side_generation = {"left": 0, "right": 0}
        self.latched = False
        self.readiness_check: Callable[[], None] | None = None
        self.on_emergency: Callable[[], None] | None = None
        self._busy_sides: dict[str, tuple[Any, int]] = {}

    @contextmanager
    def operation(self, side: str | None = None) -> Iterator[None]:
        """事件循环内原子占用运动资源；不排队，停止操作无需占用。"""
        sides = {side} if side is not None else {"left", "right"}
        owner = asyncio.current_task()
        if any(key in self._busy_sides and self._busy_sides[key][0] is not owner for key in sides):
            raise RuntimeError("motion operation already in progress")
        for key in sides:
            self._busy_sides[key] = (owner, self._busy_sides.get(key, (owner, 0))[1] + 1)
        try:
            yield
        finally:
            for key in sides:
                count = self._busy_sides[key][1] - 1
                if count:
                    self._busy_sides[key] = (owner, count)
                else:
                    del self._busy_sides[key]

    def capture(self, side: str | None = None) -> MotionSafetyToken:
        return MotionSafetyToken(
            side,
            (
                self._generation,
                self._side_generation["left"] if side != "right" else 0,
                self._side_generation["right"] if side != "left" else 0,
            ),
        )

    def check(self, token: MotionSafetyToken, *, ignore_side: str | None = None) -> None:
        if self.readiness_check is not None:
            self.readiness_check()
        if self.latched:
            raise RuntimeError("emergency stop active; acknowledge safety before motion")
        current = self.capture(token.side)
        ignored_index = {"left": 1, "right": 2}.get(ignore_side)
        if any(
            old != new
            for index, (old, new) in enumerate(zip(token.generation, current.generation, strict=True))
            if index != ignored_index
        ):
            raise RuntimeError("motion cancelled by a newer stop")

    def check_force_recovery_generation(self, token: MotionSafetyToken) -> None:
        """仅核验恢复配置的停止代际；可用于落盘线程，不授予任何运动权限。"""
        if not self.latched or token != self.capture(token.side):
            raise RuntimeError("force configuration recovery cancelled by a newer safety operation")

    def interrupt(self, side: str | None = None, *, emergency: bool = False) -> None:
        if side is None:
            self._generation += 1
        else:
            self._side_generation[side] += 1
        self.latched = self.latched or emergency
        if emergency and self.on_emergency is not None:
            try:
                self.on_emergency()
            except Exception:
                logging.getLogger(__name__).exception("recording interruption failed; hardware stop must continue")

    def acknowledge(self, token: MotionSafetyToken) -> None:
        if token != self.capture(token.side):
            raise RuntimeError("safety acknowledgement superseded by a newer stop")
        self.latched = False
