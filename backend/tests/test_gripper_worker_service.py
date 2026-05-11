from __future__ import annotations

import asyncio
from typing import Any

from backend.core.defaults import default_config
from backend.core.schemas import GripperCommandRequest
from backend.drivers.gripper_rs485 import GripperResult
from backend.services.command_service import CommandService
from backend.services.gripper_worker_service import GripperWorkerService


class FakeSettings:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_config(self) -> dict[str, Any]:
        return self.config

    def save_config(self, config: dict[str, Any], emit_log: bool = True) -> dict[str, Any]:
        _ = emit_log
        self.config = config
        return config


class FakeTelemetry:
    def apply_gripper(self, side: str, command: str, target_mm: float | None) -> float:
        _ = (side, command, target_mm)
        return 0.0


class FakeHal:
    pass


class FakeLogs:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def info(self, channel: str, message: str) -> None:
        self.entries.append((channel, message))

    def error(self, channel: str, message: str) -> None:
        self.entries.append((channel, message))


class FakeDirectGripper:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float | None]] = []

    def command(self, _config: dict[str, Any], side: str, command: str, target_mm: float | None) -> GripperResult:
        self.calls.append((side, command, target_mm))
        return GripperResult(True, "direct command", target_mm)


class FakeHardware:
    def __init__(self) -> None:
        self.gripper = FakeDirectGripper()


class FakeWorkers:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float | None]] = []

    def is_enabled(self, config: dict[str, Any]) -> bool:
        return config["gripper"].get("sampleMode") == "dual_worker"

    def command(self, _config: dict[str, Any], side: str, command: str, target_mm: float | None) -> GripperResult:
        self.calls.append((side, command, target_mm))
        return GripperResult(True, "worker command", target_mm)


def test_gripper_command_uses_dual_worker_when_enabled() -> None:
    config = default_config()
    config["gripper"]["sampleMode"] = "dual_worker"
    settings = FakeSettings(config)
    hardware = FakeHardware()
    workers = FakeWorkers()
    service = CommandService(settings, FakeTelemetry(), FakeHal(), FakeLogs(), hardware, workers)

    result = asyncio.run(
        service.gripper_command(GripperCommandRequest(side="left", command="target", targetMm=7.5))
    )

    assert result["message"] == "worker command"
    assert workers.calls == [("left", "target", 7.5)]
    assert hardware.gripper.calls == []
    assert settings.config["gripper"]["targetLeftMm"] == 7.5


def test_gripper_command_keeps_direct_driver_when_worker_disabled() -> None:
    config = default_config()
    settings = FakeSettings(config)
    hardware = FakeHardware()
    workers = FakeWorkers()
    service = CommandService(settings, FakeTelemetry(), FakeHal(), FakeLogs(), hardware, workers)

    result = asyncio.run(
        service.gripper_command(GripperCommandRequest(side="right", command="target", targetMm=6.0))
    )

    assert result["message"] == "direct command"
    assert workers.calls == []
    assert hardware.gripper.calls == [("right", "target", 6.0)]
    assert settings.config["gripper"]["targetRightMm"] == 6.0


def test_gripper_worker_sync_stops_workers_when_disabled() -> None:
    config = default_config()
    config["gripper"]["sampleMode"] = "direct"
    service = GripperWorkerService(FakeSettings(config), FakeLogs())
    calls: list[str] = []

    service.stop_all = lambda: calls.append("stop")  # type: ignore[method-assign]

    service.sync_config(config)

    assert calls == ["stop"]


def test_gripper_worker_sync_keeps_workers_when_enabled() -> None:
    config = default_config()
    config["gripper"]["sampleMode"] = "dual_worker"
    service = GripperWorkerService(FakeSettings(config), FakeLogs())
    calls: list[str] = []

    service.stop_all = lambda: calls.append("stop")  # type: ignore[method-assign]

    service.sync_config(config)

    assert calls == []
