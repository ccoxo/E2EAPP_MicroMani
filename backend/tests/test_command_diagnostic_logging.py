from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.core.schemas import ManualAxisMoveRequest
from backend.services.command_service import CommandService


class FakeSettings:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_config(self) -> dict[str, Any]:
        return self.config

    def save_config(self, config: dict[str, Any], emit_log: bool = True, **_: Any) -> dict[str, Any]:
        self.config = config
        return config


class FakeTelemetry:
    def apply_axis_move(self, side: str, axis: str, direction: int, step: float) -> float:
        _ = (side, axis)
        return direction * step

    def home_all(self) -> None:
        return None

    def home_side(self, side: str) -> None:
        _ = side

    def set_motion_axis_enabled(self, side: str, values: list[bool | None]) -> None:
        _ = (side, values)


class FakeHal:
    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"mode": "test", "command": name, "payload": payload or {}, "response": {"ok": True}}

    async def motion_state(self) -> dict[str, Any]:
        return {"pulses": [0.0] * 12, "enabled": [True] * 12}


def _service(config: dict[str, Any], logs: LogService) -> CommandService:
    return CommandService(FakeSettings(config), FakeTelemetry(), FakeHal(), logs)


def test_manual_axis_move_logs_structured_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    logs = LogService(monotonic_ms=lambda: 123, session_id="s", emit_startup=False)
    service = _service(default_config(), logs)

    asyncio.run(
        service.manual_axis_move(
            ManualAxisMoveRequest(side="left", axis="Yaw", direction=1, step=0.2, speedMode="fine")
        )
    )

    message = next(entry.msg for entry in logs.list_entries() if "event=manual_move" in entry.msg)
    assert "component=MOTION" in message
    assert "op_id=manual_1" in message
    assert "axis=left.Yaw" in message
    assert "requestedDelta=0.2" in message
    assert "safeDelta=0.2" in message
    assert "dmcRet=not_called" in message
    assert "backend=test" in message


def test_work_origin_home_all_logs_operation_and_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["origin"]["valid"] = True
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["rightValid"] = True
    config["motion"]["origin"]["leftPulse"] = [1, 2, 3, 4, 5, 6]
    config["motion"]["origin"]["rightPulse"] = [7, 8, 9, 10, 11, 12]
    logs = LogService(monotonic_ms=lambda: 222, session_id="s", emit_startup=False)
    service = _service(config, logs)

    asyncio.run(service.home_all())

    messages = [entry.msg for entry in logs.list_entries()]
    assert any("event=work_origin_op" in message and "phase=start" in message for message in messages)
    assert any("event=work_origin_move" in message and "axis=left.Yaw" in message for message in messages)
    assert any("event=work_origin_op" in message and "phase=complete" in message for message in messages)


def test_manual_axis_effective_direction_matches_site_direction_corrections() -> None:
    service = _service(default_config(), LogService(emit_startup=False))

    assert service._manual_axis_effective_direction("left", "X", 1) == 1
    assert service._manual_axis_effective_direction("left", "Y", 1) == -1
    assert service._manual_axis_effective_direction("left", "Z", 1) == 1
    assert service._manual_axis_effective_direction("right", "X", 1) == -1
    assert service._manual_axis_effective_direction("right", "Y", 1) == -1
    assert service._manual_axis_effective_direction("right", "Z", 1) == -1
