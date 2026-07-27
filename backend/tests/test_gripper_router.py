from __future__ import annotations

import asyncio
from typing import Any

from backend.core.defaults import default_config
from backend.services.gripper_backend import NativeGripperAdapter
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


def build_router() -> tuple[GripperRouter, FakeHal, FakeTeleopMapper]:
    hal = FakeHal()
    teleop = FakeTeleopMapper()
    router = GripperRouter(native=NativeGripperAdapter(hal, teleop))
    return router, hal, teleop


def test_router_always_selects_hal_native() -> None:
    router, _hal, _teleop = build_router()
    config = default_config()

    assert router.backend_name(config) == "hal_native"
    assert router.is_native(config) is True


def test_router_ignores_legacy_python_mapper_engine() -> None:
    router, _hal, _teleop = build_router()
    config = default_config()
    config["teleop"]["engine"] = "python_mapper"

    assert router.backend_name(config) == "hal_native"
    assert router.is_native(config) is True


def test_router_status_uses_native_status() -> None:
    router, _hal, teleop = build_router()
    config = default_config()
    teleop.sources = ["teleop-connect"]
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


def test_router_command_sends_hal_native_payload() -> None:
    router, hal, _teleop = build_router()
    config = default_config()

    result = asyncio.run(router.command(config, "left", "target", 7.5))

    assert result.ok is True
    assert result.position_mm == 7.5
    assert result.details["nativeManaged"] is True
    assert hal.commands[0][0] == "teleop.native.gripper_command"
    assert hal.commands[0][1]["targetMm"] == 7.5
