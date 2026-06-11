from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.core.motion_limits import effective_limits_ui, side_home_reference_ui
from backend.core.schemas import ManualAxisMoveRequest
from backend.services.command_service import CommandService


def _wide_motion_soft_limits() -> dict[str, dict[str, float]]:
    return {
        "x": {"min": -1_000_000.0, "max": 1_000_000.0},
        "y": {"min": -1_000_000.0, "max": 1_000_000.0},
        "z": {"min": -1_000_000.0, "max": 1_000_000.0},
        "roll": {"min": -360_000.0, "max": 360_000.0},
        "pitch": {"min": -360_000.0, "max": 360_000.0},
        "yaw": {"min": -360_000.0, "max": 360_000.0},
    }


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


class RecordingHal(FakeHal):
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        active_payload = payload or {}
        self.commands.append((name, active_payload))
        return {"mode": "test", "command": name, "payload": active_payload, "response": {"ok": True}}


class DisabledMotionHal(RecordingHal):
    async def motion_state(self) -> dict[str, Any]:
        return {"pulses": [0.0] * 12, "enabled": [False] * 12}


class FakeTeleop:
    def __init__(self, sources: list[str]) -> None:
        self.sources = sources
        self.stop_calls: list[tuple[str, bool]] = []

    def status(self) -> dict[str, Any]:
        return {"sources": list(self.sources), "armed": bool(self.sources), "running": bool(self.sources)}

    async def stop(self, source: str, *, restart_remaining: bool = True) -> dict[str, Any]:
        self.stop_calls.append((source, restart_remaining))
        self.sources = [item for item in self.sources if item != source]
        return self.status()


class FakeMotionStateHal(FakeHal):
    def __init__(self, pulses: list[float]) -> None:
        self.pulses = pulses

    async def motion_state(self) -> dict[str, Any]:
        return {"pulses": self.pulses, "enabled": [True] * 12}


def _service(config: dict[str, Any], logs: LogService) -> CommandService:
    return CommandService(FakeSettings(config), FakeTelemetry(), FakeHal(), logs)


def _service_with_hal(config: dict[str, Any], logs: LogService, hal: FakeHal) -> CommandService:
    return CommandService(FakeSettings(config), FakeTelemetry(), hal, logs)


def _set_home_reference_to_origin(config: dict[str, Any]) -> None:
    origin = config["motion"]["origin"]
    config["motion"]["homeReference"] = {
        "valid": bool(
            origin.get("leftValid", origin.get("valid", False))
            and origin.get("rightValid", origin.get("valid", False))
        ),
        "leftValid": bool(origin.get("leftValid", origin.get("valid", False))),
        "rightValid": bool(origin.get("rightValid", origin.get("valid", False))),
        "leftPulse": list(origin.get("leftPulse", [0.0] * 6)),
        "rightPulse": list(origin.get("rightPulse", [0.0] * 6)),
        "updatedAt": int(origin.get("updatedAt", 0)),
    }


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
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    logs = LogService(monotonic_ms=lambda: 222, session_id="s", emit_startup=False)
    service = _service(config, logs)

    asyncio.run(service.home_all())

    messages = [entry.msg for entry in logs.list_entries()]
    assert any("event=work_origin_op" in message and "phase=start" in message for message in messages)
    assert any("event=work_origin_move" in message and "axis=left.Yaw" in message for message in messages)
    assert not any("event=work_origin_move" in message and "axis=right.Yaw" in message for message in messages)
    assert any("event=work_origin_op" in message and "phase=complete" in message for message in messages)


def test_return_motion_origin_side_stops_manual_teleop_connect_before_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["origin"]["valid"] = True
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["leftPulse"] = [1, 2, 3, 4, 5, 6]
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    hal = RecordingHal()
    teleop = FakeTeleop(["teleop-connect"])
    service = _service_with_hal(config, LogService(emit_startup=False), hal)
    service.teleop = teleop

    asyncio.run(service.return_motion_origin_side("left"))

    assert teleop.stop_calls == [("teleop-connect", False)]
    assert hal.commands == [
        (
            "motion.home_origin_side",
            {"side": "left", "pulse": [1, 2, 3, 4, 5, 6], "enabledAxes": [True, True, True, True, True, True]},
        )
    ]


def test_return_motion_origin_side_stops_manual_teleop_connect_even_when_axis_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["origin"]["valid"] = True
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["leftPulse"] = [1, 2, 3, 4, 5, 6]
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    teleop = FakeTeleop(["teleop-connect"])
    service = _service_with_hal(config, LogService(emit_startup=False), DisabledMotionHal())
    service.teleop = teleop

    with pytest.raises(RuntimeError, match="left motion axes are disabled"):
        asyncio.run(service.return_motion_origin_side("left"))

    assert teleop.stop_calls == [("teleop-connect", False)]


def test_capture_motion_origin_stops_native_teleop_sources_after_origin_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    teleop = FakeTeleop(["teleop-connect", "manual-gripper"])
    service = _service_with_hal(config, LogService(emit_startup=False), FakeMotionStateHal([0.0] * 12))
    service.teleop = teleop

    asyncio.run(service.capture_motion_origin(confirm_large_drift=True))

    assert teleop.stop_calls == [
        ("teleop-connect", False),
        ("manual-gripper", False),
    ]


def test_restore_previous_motion_origin_stops_native_teleop_sources_after_origin_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["origin"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 100,
        "previousValid": True,
        "previousLeftPulse": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
        "previousRightPulse": [107.0, 108.0, 109.0, 110.0, 111.0, 112.0],
        "previousUpdatedAt": 50,
    }
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
        "rightPulse": [107.0, 108.0, 109.0, 110.0, 111.0, 112.0],
        "updatedAt": 50,
    }
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    config["motion"]["rightSoftLimits"] = _wide_motion_soft_limits()
    teleop = FakeTeleop(["teleop-connect"])
    service = _service_with_hal(config, LogService(emit_startup=False), FakeMotionStateHal([0.0] * 12))
    service.teleop = teleop

    asyncio.run(service.restore_previous_motion_origin())

    assert teleop.stop_calls == [("teleop-connect", False)]


def test_return_motion_origin_side_logs_planned_axis_delta_before_hal_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["origin"]["valid"] = True
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["leftPulse"] = [1, -84, 122, -599940, -11, 571]
    _set_home_reference_to_origin(config)
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    logs = LogService(monotonic_ms=lambda: 333, session_id="s", emit_startup=False)
    hal = FakeMotionStateHal([-32, -102, 178, -561457, -3988, 570] + [0.0] * 6)
    service = _service_with_hal(config, logs, hal)

    asyncio.run(service.return_motion_origin_side("left"))

    messages = [entry.msg for entry in logs.list_entries()]
    assert any(
        "event=work_origin_move" in message
        and "phase=planned" in message
        and "axis=left.Roll" in message
        and "current=-561457" in message
        and "requestedTarget=-599940" in message
        and "deltaPulse=-38483" in message
        for message in messages
    )


def test_capture_motion_origin_reanchors_rotation_limits_to_hardware_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["leftSoftLimits"]["yaw"] = {"min": -9000.0, "max": 9000.0}
    pulses = list(config["motion"]["origin"]["leftPulse"] + config["motion"]["origin"]["rightPulse"])
    service = _service_with_hal(config, LogService(emit_startup=False), FakeMotionStateHal(pulses))

    asyncio.run(service.capture_motion_origin("left", confirm_large_drift=True))

    home_reference = side_home_reference_ui(config, "left")
    assert home_reference is not None
    limits = effective_limits_ui(config, "left")
    assert limits[5].min - home_reference[5] == pytest.approx(-7.0)
    assert limits[5].max - home_reference[5] == pytest.approx(7.0)
    service._validate_work_origin_target(config, "left")


def test_manual_axis_effective_direction_matches_site_direction_corrections() -> None:
    service = _service(default_config(), LogService(emit_startup=False))

    assert service._manual_axis_effective_direction("left", "X", 1) == 1
    assert service._manual_axis_effective_direction("left", "Y", 1) == -1
    assert service._manual_axis_effective_direction("left", "Z", 1) == 1
    assert service._manual_axis_effective_direction("right", "X", 1) == -1
    assert service._manual_axis_effective_direction("right", "Y", 1) == -1
    assert service._manual_axis_effective_direction("right", "Z", 1) == -1
