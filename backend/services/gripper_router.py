"""Select the active gripper backend for the current runtime mode."""

from __future__ import annotations

from typing import Any

from backend.drivers.gripper_rs485 import GripperResult
from backend.services.gripper_backend import (
    DirectGripperAdapter,
    GripperBackend,
    NativeGripperAdapter,
    WorkerGripperAdapter,
)


class GripperRouter:
    """Routes gripper reads and commands to native HAL, worker, or direct SDK."""

    def __init__(
        self,
        *,
        native: NativeGripperAdapter,
        worker: WorkerGripperAdapter,
        direct: DirectGripperAdapter,
    ) -> None:
        self._native = native
        self._worker = worker
        self._direct = direct

    def select(self, config: dict[str, Any]) -> GripperBackend:
        if self._worker.is_enabled(config):
            return self._worker
        if self._native.is_enabled(config):
            return self._native
        return self._direct

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
        await self._worker.stop()
