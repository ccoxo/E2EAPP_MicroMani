from __future__ import annotations

import asyncio
from typing import Any

from backend.core.defaults import default_config
from backend.drivers.gripper_rs485 import GripperResult
from backend.services.gripper_backend import DirectGripperAdapter, NativeGripperAdapter, WorkerGripperAdapter
from backend.services.gripper_router import GripperRouter


class FakeHal:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        self.commands.append((name, body))
        return {"command": name, "payload": body, "message": "hal accepted"}


class FakeTeleopMapper:
    def __init__(self) -> None:
        self.native_status: dict[str, Any] = {}
        self.sources: list[str] = []

    def status(self, _config: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"running": bool(self.sources), "sources": list(self.sources), "nativeStatus": dict(self.native_status)}


class FakeWorkers:
    def __init__(self) -> None:
        self.command_calls: list[tuple[str, str, float | None]] = []

    def is_enabled(self, config: dict[str, Any] | None = None) -> bool:
        active = config or default_config()
        return active["gripper"].get("sampleMode") == "dual_worker"

    def status(self, _config: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "message": "dual gripper workers", "sides": {}}

    def position(self, _config: dict[str, Any], side: str) -> GripperResult:
        return GripperResult(True, f"{side} worker position", 3.0)

    def command(self, _config: dict[str, Any], side: str, command: str, target: float | None) -> GripperResult:
        self.command_calls.append((side, command, target))
        return GripperResult(True, "worker command", target)

    def stop_all(self) -> None:
        return None


class FakeDirectGripper:
    def __init__(self) -> None:
        self.command_calls: list[tuple[str, str, float | None]] = []

    def probe(self, _config: dict[str, Any]) -> GripperResult:
        return GripperResult(True, "jodell RS485 gripper ports open", details={"ports": [{"side": "left"}]})

    def position(self, _config: dict[str, Any], side: str) -> GripperResult:
        return GripperResult(True, f"{side} direct position", 4.0)

    def diagnose(self, _config: dict[str, Any], side: str) -> GripperResult:
        return GripperResult(True, f"{side} direct diagnose", 5.0)

    def command(self, _config: dict[str, Any], side: str, command: str, target: float | None) -> GripperResult:
        self.command_calls.append((side, command, target))
        return GripperResult(True, "direct command", target)


class FakeHardware:
    def __init__(self) -> None:
        self.gripper = FakeDirectGripper()


def build_router() -> tuple[GripperRouter, FakeHal, FakeTeleopMapper, FakeWorkers, FakeHardware]:
    hal = FakeHal()
    teleop = FakeTeleopMapper()
    workers = FakeWorkers()
    hardware = FakeHardware()
    router = GripperRouter(
        native=NativeGripperAdapter(hal, teleop),
        worker=WorkerGripperAdapter(workers),
        direct=DirectGripperAdapter(hardware),
    )
    return router, hal, teleop, workers, hardware


def test_router_selects_worker_before_native() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"

    assert router.backend_name(config) == "dual_worker"
    assert router.is_native(config) is False


def test_router_selects_native_only_when_workers_disabled() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"

    assert router.backend_name(config) == "hal_native"
    assert router.is_native(config) is True


def test_router_falls_back_to_direct() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "direct"

    assert router.backend_name(config) == "python_rs485"
    assert router.is_native(config) is False


def test_router_status_formats_direct_probe() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "direct"

    status = asyncio.run(router.status(config))

    assert status["ok"] is True
    assert status["message"] == "jodell RS485 gripper ports open"
    assert status["ports"] == [{"side": "left"}]


def test_router_native_status_uses_cached_teleop_mapper_payload() -> None:
    router, _hal, teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    teleop.sources = ["manual-gripper"]
    teleop.native_status = {
        "running": True,
        "gripperTargets": [8.0, 9.0],
        "grippers": {
            "left": {"ok": True, "positionMm": 8.0, "targetMm": 8.0, "message": "", "lastCommandTs": 1},
            "right": {"ok": True, "positionMm": 9.0, "targetMm": 9.0, "message": "", "lastCommandTs": 2},
        },
    }

    status = asyncio.run(router.status(config))

    assert status["nativeManaged"] is True
    assert status["running"] is True
    assert status["positionMm"] == {"left": 8.0, "right": 9.0}
    assert status["ports"][1]["port"] == "COM9"


def test_router_native_command_sends_hal_payload() -> None:
    router, hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"

    result = asyncio.run(router.command(config, "left", "target", 7.5))

    assert result.ok is True
    assert result.position_mm == 7.5
    assert result.details["nativeManaged"] is True
    assert hal.commands[0][0] == "teleop.native.gripper_command"
    assert hal.commands[0][1]["targetMm"] == 7.5


def test_router_worker_command_runs_worker_backend() -> None:
    router, _hal, _teleop, workers, hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"

    result = asyncio.run(router.command(config, "right", "target", 6.0))

    assert result.message == "worker command"
    assert workers.command_calls == [("right", "target", 6.0)]
    assert hardware.gripper.command_calls == []
