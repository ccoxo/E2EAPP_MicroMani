# 阅读导航 04｜后端业务与采集
# 职责：把夹爪操作统一路由到 HAL-native 适配器；当前 select 始终返回原生后端。
# 先看：GripperRouter。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

"""Route gripper commands through the HAL-native backend."""

from __future__ import annotations

from typing import Any

from backend.drivers.gripper_rs485 import GripperResult
from backend.services.gripper_backend import (
    GripperBackend,
    NativeGripperAdapter,
)


class GripperRouter:
    """Routes gripper reads and commands to HAL-native."""

    def __init__(
        self,
        *,
        native: NativeGripperAdapter,
    ) -> None:
        self._native = native

    def select(self, config: dict[str, Any]) -> GripperBackend:
        _ = config
        return self._native

    def backend_name(self, config: dict[str, Any]) -> str:
        return self.select(config).name

    def is_native(self, config: dict[str, Any]) -> bool:
        return self.select(config) is self._native

    async def status(self, config: dict[str, Any]) -> dict[str, Any]:
        return await self.select(config).status(config)

    async def position(self, config: dict[str, Any], side: str) -> GripperResult:
        return await self.select(config).position(config, side)

    async def diagnose(self, config: dict[str, Any], side: str) -> GripperResult:
        return await self.select(config).diagnose(config, side)

    async def command(
        self,
        config: dict[str, Any],
        side: str,
        command: str,
        target_mm: float | None,
    ) -> GripperResult:
        return await self.select(config).command(config, side, command, target_mm)

    async def stop(self) -> None:
        return None
