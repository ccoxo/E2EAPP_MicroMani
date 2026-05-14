from __future__ import annotations

import asyncio
from typing import Any

from backend.core.defaults import default_config
from backend.core.schemas import GripperCommandRequest, ManualAxisMoveRequest
from backend.drivers.gripper_rs485 import GripperResult
from backend.services.command_service import CommandService
from backend.services.gripper_worker_service import GripperWorkerService
from backend.services.telemetry_hub import TelemetryHub


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

    def apply_axis_move(self, side: str, axis: str, direction: int, step: float) -> float:
        _ = (side, axis, direction)
        return step

    def set_motion_enabled(self, side: str, enabled: bool | None) -> None:
        _ = (side, enabled)


class FakeHal:
    def __init__(self, enabled: bool = True, enabled_values: list[bool] | None = None) -> None:
        self.enabled = enabled
        self.enabled_values = enabled_values
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def motion_state(self) -> dict[str, Any]:
        if self.enabled_values is not None:
            return {"enabled": self.enabled_values}
        return {"enabled": [self.enabled] * 12}

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.commands.append((name, payload or {}))
        return {"command": name, "payload": payload or {}}


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
    config["gripper"]["leftEnabled"] = True
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
    config["gripper"]["sampleMode"] = "direct"
    config["gripper"]["rightEnabled"] = True
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


def test_dual_worker_gripper_command_rejects_disabled_side() -> None:
    config = default_config()
    config["gripper"]["sampleMode"] = "dual_worker"
    config["gripper"]["leftEnabled"] = False
    service = GripperWorkerService(FakeSettings(config), FakeLogs())

    result = service.command(config, "left", "open", None)

    assert result.ok is False
    assert "left gripper is disabled" in result.message


def test_gripper_motion_command_rejects_disabled_side() -> None:
    config = default_config()
    settings = FakeSettings(config)
    service = CommandService(settings, FakeTelemetry(), FakeHal(), FakeLogs(), FakeHardware(), FakeWorkers())

    try:
        asyncio.run(service.gripper_command(GripperCommandRequest(side="left", command="open")))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected disabled gripper command to fail")

    assert "left gripper is disabled" in message


def test_manual_axis_move_rejects_disabled_motion_side() -> None:
    config = default_config()
    settings = FakeSettings(config)
    hal = FakeHal(enabled=False)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    try:
        asyncio.run(
            service.manual_axis_move(
                ManualAxisMoveRequest(side="left", axis="X", direction=1, step=100, speedMode="fine")
            )
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected disabled motion side to fail")

    assert "left X motion axis is disabled" in message
    assert hal.commands == []


def test_manual_axis_move_allows_enabled_motion_side() -> None:
    config = default_config()
    settings = FakeSettings(config)
    hal = FakeHal(enabled=True)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    result = asyncio.run(
        service.manual_axis_move(
            ManualAxisMoveRequest(side="right", axis="X", direction=-1, step=100, speedMode="fine")
        )
    )

    assert result["hal"]["command"] == "motion.manual_axis_move"
    assert hal.commands[0][0] == "motion.manual_axis_move"


def test_manual_axis_move_allows_right_roll_without_sevon_feedback_when_right_side_enabled() -> None:
    config = default_config()
    settings = FakeSettings(config)
    enabled_values = [True] * 12
    enabled_values[9] = False
    hal = FakeHal(enabled_values=enabled_values)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    result = asyncio.run(
        service.manual_axis_move(
            ManualAxisMoveRequest(side="right", axis="Roll", direction=1, step=1, speedMode="fine")
        )
    )

    assert result["hal"]["command"] == "motion.manual_axis_move"
    assert hal.commands[0][0] == "motion.manual_axis_move"


def test_manual_axis_move_rejects_right_roll_when_right_side_is_fully_disabled() -> None:
    config = default_config()
    settings = FakeSettings(config)
    enabled_values = [True] * 6 + [False] * 6
    hal = FakeHal(enabled_values=enabled_values)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    try:
        asyncio.run(
            service.manual_axis_move(
                ManualAxisMoveRequest(side="right", axis="Roll", direction=1, step=1, speedMode="fine")
            )
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected disabled right Roll command to fail")

    assert "right Roll motion axis is disabled" in message
    assert hal.commands == []


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


def test_telemetry_uses_dual_worker_sample_timestamps_for_gripper_cache() -> None:
    class FakeWorkerSamples:
        def is_enabled(self, _config: dict[str, Any]) -> bool:
            return True

        def samples(self, _config: dict[str, Any]) -> dict[str, dict[str, Any]]:
            return {
                "left": {"ok": True, "positionMm": 3.0, "sampleHz": 30.0, "monotonicMs": 120000},
                "right": {"ok": True, "positionMm": 4.0, "sampleHz": 30.0, "monotonicMs": 120033},
            }

    telemetry = object.__new__(TelemetryHub)
    telemetry.hardware = object()
    telemetry.gripper_workers = FakeWorkerSamples()
    telemetry.gripper_positions = [-1.0, -1.0]
    telemetry.gripper_samples = {}
    telemetry._last_gripper_sample_at = 0.0

    telemetry.refresh_gripper_positions({"gripper": {"sampleMode": "dual_worker"}}, now=999.0)

    assert telemetry.gripper_positions == [3.0, 4.0]
    assert telemetry.gripper_samples["left"]["sampleHz"] == 30.0
    assert telemetry._last_gripper_sample_at == 120.033
