from __future__ import annotations

import asyncio
import queue
import time
from typing import Any

from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.core.schemas import GripperCommandRequest, ManualAxisMoveRequest
from backend.drivers.gripper_rs485 import GripperResult
from backend.services.command_service import CommandService
from backend.services.gripper_worker_service import GripperWorkerService
from backend.services.telemetry_hub import TelemetryHub
from backend.workers.gripper_worker import _GripperWorker


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
    def __init__(self) -> None:
        self.motion_axis_enabled: dict[str, list[bool | None]] = {}
        self.motion_enabled: dict[str, bool | None] = {}

    def apply_gripper(self, side: str, command: str, target_mm: float | None) -> float:
        _ = (side, command, target_mm)
        return 0.0

    def apply_axis_move(self, side: str, axis: str, direction: int, step: float) -> float:
        _ = (side, axis, direction)
        return step

    def set_motion_enabled(self, side: str, enabled: bool | None) -> None:
        _ = (side, enabled)

    def set_motion_axis_enabled(self, side: str, values: list[bool | None]) -> None:
        self.motion_axis_enabled[side] = values
        known = [value for value in values if value is not None]
        self.motion_enabled[side] = all(value is True for value in known) if known else None


class FakeHal:
    def __init__(self, enabled: bool = True, enabled_values: list[bool] | None = None) -> None:
        self.enabled = enabled
        self.enabled_values = enabled_values
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def motion_state(self) -> dict[str, Any]:
        base_state = {
            "positions": [0.0] * 12,
            "pulses": [0.0] * 12,
            "estop_active": False,
        }
        if self.enabled_values is not None:
            return {**base_state, "enabled": self.enabled_values}
        return {**base_state, "enabled": [self.enabled] * 12}

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.commands.append((name, payload or {}))
        return {"command": name, "payload": payload or {}}


class FailingHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.commands.append((name, payload or {}))
        raise RuntimeError("serialOperation open failed COM9, ret=-1")


class FakeLogs:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []
        self.events: list[tuple[str, str, str, dict[str, Any]]] = []
        self._next_op_id = 0

    def new_op_id(self, prefix: str) -> str:
        self._next_op_id += 1
        return f"{prefix}-{self._next_op_id}"

    def info(self, channel: str, message: str) -> None:
        self.entries.append((channel, message))

    def error(self, channel: str, message: str) -> None:
        self.entries.append((channel, message))

    def event(self, channel: str, level: str, message: str, **fields: Any) -> None:
        self.events.append((channel, level, message, fields))


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
    config["teleop"]["engine"] = "python_mapper"
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
    config["teleop"]["engine"] = "python_mapper"
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
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "dual_worker"
    config["gripper"]["leftEnabled"] = False
    service = GripperWorkerService(FakeSettings(config), FakeLogs())

    result = service.command(config, "left", "open", None)

    assert result.ok is False
    assert "left gripper is disabled" in result.message


def test_gripper_worker_motion_uses_command_speed_and_torque_overrides() -> None:
    config = default_config()
    config["gripper"]["leftEnabled"] = True

    class FakeDll:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int, int]] = []

        def clawEnable(self, _slave: int, _enabled: bool) -> int:
            return 1

        def runWithParam(self, slave: int, pos: int, speed: int, torque: int) -> int:
            self.calls.append((slave, pos, speed, torque))
            return 1

    fake_dll = FakeDll()
    worker = _GripperWorker("left", config, queue.Queue(), queue.Queue(), queue.Queue())
    worker.dll = fake_dll
    worker.enabled = True

    ok, message, _ = worker._execute_command("target", 7.5, 128, 192)

    assert ok is True
    assert fake_dll.calls[0][2:] == (128, 192)
    assert "speed=128, torque=192" in message


def test_gripper_motion_command_rejects_disabled_side() -> None:
    config = default_config()
    config["teleop"]["engine"] = "python_mapper"
    settings = FakeSettings(config)
    service = CommandService(settings, FakeTelemetry(), FakeHal(), FakeLogs(), FakeHardware(), FakeWorkers())

    try:
        asyncio.run(service.gripper_command(GripperCommandRequest(side="left", command="open")))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected disabled gripper command to fail")

    assert "left gripper is disabled" in message


def test_hal_native_gripper_motion_command_auto_enables_disabled_side() -> None:
    config = default_config()
    config["hal"]["mode"] = "real"
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["rightEnabled"] = False
    settings = FakeSettings(config)
    hal = FakeHal()
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    result = asyncio.run(service.gripper_command(GripperCommandRequest(side="right", command="open")))

    assert result["nativeManaged"] is True
    assert result["targetMm"] == config["gripper"]["strokeMm"]
    assert hal.commands == [
        (
            "gripper.command",
            {
                "side": "right",
                "targetMm": config["gripper"]["strokeMm"],
                "leftPort": "COM8",
                "rightPort": "COM9",
                "leftSlaveId": 10,
                "rightSlaveId": 9,
                "baudrate": 115200,
                "strokeMm": config["gripper"]["strokeMm"],
                "jodellDllPath": config["gripper"]["jodellDllPath"],
                "gripSpeed": 255,
                "gripTorque": 192,
            },
        )
    ]
    assert settings.config["gripper"]["rightEnabled"] is True


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
    assert hal.commands[0][1]["requestedDirection"] == -1
    assert hal.commands[0][1]["direction"] == 1


def test_manual_axis_move_allows_icf_single_step_pulse_limit() -> None:
    config = default_config()
    config["motion"]["origin"]["rightValid"] = False
    settings = FakeSettings(config)
    hal = FakeHal(enabled=True)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    result = asyncio.run(
        service.manual_axis_move(
            ManualAxisMoveRequest(side="right", axis="Y", direction=1, step=10000, speedMode="fine")
        )
    )

    assert result["hal"]["command"] == "motion.manual_axis_move"
    assert result["hal"]["payload"]["step"] == 10000


def test_manual_axis_move_rejects_step_above_icf_single_step_pulse_limit() -> None:
    config = default_config()
    settings = FakeSettings(config)
    service = CommandService(
        settings,
        FakeTelemetry(),
        FakeHal(enabled=True),
        FakeLogs(),
        FakeHardware(),
        FakeWorkers(),
    )

    try:
        asyncio.run(
            service.manual_axis_move(
                ManualAxisMoveRequest(side="left", axis="X", direction=1, step=20001, speedMode="fine")
            )
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected manual axis step above pulse cap to fail")

    assert "100000 pulse cap" in message
    assert "20000.000 um" in message


def test_motion_enabled_refresh_reports_card0_dmc5c10_feedback_as_unknown() -> None:
    config = default_config()
    settings = FakeSettings(config)
    telemetry = FakeTelemetry()
    enabled_values = [True] * 6 + [False] * 6
    hal = FakeHal(enabled_values=enabled_values)
    service = CommandService(settings, telemetry, hal, FakeLogs(), FakeHardware(), FakeWorkers())

    asyncio.run(service.enable_motion_side("right"))

    assert telemetry.motion_axis_enabled["right"] == [None, None, None, None, None, None]
    assert telemetry.motion_enabled["right"] is None


def test_manual_axis_move_allows_right_roll_when_card0_feedback_is_unreadable() -> None:
    config = default_config()
    settings = FakeSettings(config)
    enabled_values = [True] * 12
    enabled_values[9] = False
    hal = FakeHal(enabled_values=enabled_values)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    asyncio.run(
        service.manual_axis_move(
            ManualAxisMoveRequest(side="right", axis="Roll", direction=1, step=1, speedMode="fine")
        )
    )

    assert hal.commands[-1][0] == "motion.manual_axis_move"


def test_manual_axis_move_allows_right_pitch_when_card0_feedback_is_unreadable() -> None:
    config = default_config()
    settings = FakeSettings(config)
    enabled_values = [True] * 12
    enabled_values[10] = False
    hal = FakeHal(enabled_values=enabled_values)
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), FakeHardware(), FakeWorkers())

    asyncio.run(
        service.manual_axis_move(
            ManualAxisMoveRequest(side="right", axis="Pitch", direction=1, step=1, speedMode="fine")
        )
    )

    assert hal.commands[-1][0] == "motion.manual_axis_move"


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
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "dual_worker"
    service = GripperWorkerService(FakeSettings(config), FakeLogs())
    calls: list[str] = []

    service.stop_all = lambda: calls.append("stop")  # type: ignore[method-assign]

    service.sync_config(config)

    assert calls == []


def test_gripper_workers_disabled_when_hal_native_owns_gripper() -> None:
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"
    service = GripperWorkerService(FakeSettings(config), FakeLogs())

    assert service.is_enabled(config) is False


def test_hal_native_gripper_command_routes_manual_target_through_hal() -> None:
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"
    config["gripper"]["leftEnabled"] = True
    settings = FakeSettings(config)
    hardware = FakeHardware()
    workers = FakeWorkers()
    hal = FakeHal()
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), hardware, workers)

    result = asyncio.run(service.gripper_command(GripperCommandRequest(side="left", command="open")))

    assert result["nativeManaged"] is True
    assert result["targetMm"] == 26
    assert hal.commands == [
        (
            "gripper.command",
            {
                "side": "left",
                "targetMm": 26.0,
                "leftPort": "COM8",
                "rightPort": "COM9",
                "leftSlaveId": 10,
                "rightSlaveId": 9,
                "baudrate": 115200,
                "strokeMm": 26.0,
                "jodellDllPath": config["gripper"]["jodellDllPath"],
                    "gripSpeed": 255,
                "gripTorque": 192,
            },
        )
    ]
    assert workers.calls == []
    assert hardware.gripper.calls == []
    assert settings.config["gripper"]["targetLeftMm"] == 26


def test_hal_native_gripper_command_logs_structured_event() -> None:
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"
    config["gripper"]["leftEnabled"] = True
    settings = FakeSettings(config)
    hardware = FakeHardware()
    workers = FakeWorkers()
    hal = FakeHal()
    logs = LogService(emit_startup=False)
    service = CommandService(settings, FakeTelemetry(), hal, logs, hardware, workers)

    asyncio.run(service.gripper_command(GripperCommandRequest(side="left", command="open")))

    message = next(entry.msg for entry in logs.list_entries() if "event=gripper_command" in entry.msg)
    assert "side=left" in message
    assert "backend=hal_native" in message
    assert "port=COM8" in message
    assert "slave=10" in message
    assert "command=open" in message
    assert "pos=26" in message
    assert "runRet=true" in message


def test_hal_native_gripper_command_logs_hal_error_for_com9_failure() -> None:
    config = default_config()
    config["hal"]["mode"] = "real"
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["rightEnabled"] = True
    settings = FakeSettings(config)
    logs = LogService(emit_startup=False)
    service = CommandService(settings, FakeTelemetry(), FailingHal(), logs, FakeHardware(), FakeWorkers())

    try:
        asyncio.run(service.gripper_command(GripperCommandRequest(side="right", command="open")))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected COM9 HAL-native gripper command failure")

    assert "COM9" in message
    entry = next(entry for entry in logs.list_entries() if "event=gripper_command" in entry.msg)
    assert entry.level == "ERROR"
    event = entry.msg
    assert "side=right" in event
    assert "backend=hal_native" in event
    assert "port=COM9" in event
    assert "command=open" in event
    assert "runRet=false" in event
    assert "ipcOk=false" in event
    assert 'error="serialOperation open failed COM9, ret=-1"' in event


def test_hal_native_gripper_enable_updates_state_without_python_com() -> None:
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"
    settings = FakeSettings(config)
    hardware = FakeHardware()
    workers = FakeWorkers()
    hal = FakeHal()
    service = CommandService(settings, FakeTelemetry(), hal, FakeLogs(), hardware, workers)

    result = asyncio.run(service.gripper_command(GripperCommandRequest(side="left", command="enable")))

    assert result["nativeManaged"] is True
    assert hal.commands == [
        (
            "gripper.command",
            {
                "side": "left",
                "targetMm": 13.0,
                "leftPort": "COM8",
                "rightPort": "COM9",
                "leftSlaveId": 10,
                "rightSlaveId": 9,
                "baudrate": 115200,
                "strokeMm": 26.0,
                "jodellDllPath": config["gripper"]["jodellDllPath"],
                    "gripSpeed": 255,
                "gripTorque": 192,
            },
        )
    ]
    assert workers.calls == []
    assert hardware.gripper.calls == []
    assert settings.config["gripper"]["leftEnabled"] is True


def test_telemetry_skips_python_gripper_sampling_when_hal_native_owns_gripper() -> None:
    class FakeWorkerSamples:
        calls = 0

        def is_enabled(self, _config: dict[str, Any]) -> bool:
            return True

        def samples(self, _config: dict[str, Any]) -> dict[str, dict[str, Any]]:
            self.calls += 1
            return {}

    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    worker = FakeWorkerSamples()
    telemetry = object.__new__(TelemetryHub)
    telemetry.hardware = object()
    telemetry.gripper_workers = worker

    telemetry.refresh_gripper_positions(config, now=999.0)

    assert worker.calls == 0


def test_telemetry_shutdown_closes_hardware_executor_and_cameras() -> None:
    class FakeCameras:
        def __init__(self) -> None:
            self.closed = False

        def probe(self, config: dict[str, Any]) -> Any:
            _ = config
            return type("Probe", (), {"cameras": []})()

        def close_all(self) -> None:
            self.closed = True

    cameras = FakeCameras()
    telemetry = TelemetryHub(
        FakeSettings(default_config()),
        type("Hardware", (), {"cameras": cameras})(),
    )

    telemetry._refresh_cameras(default_config(), time.monotonic())  # noqa: SLF001
    telemetry.shutdown()

    assert telemetry._camera_future is None  # noqa: SLF001
    assert cameras.closed is True
    try:
        telemetry._hardware_executor.submit(lambda: None)  # noqa: SLF001
    except RuntimeError:
        pass
    else:
        raise AssertionError("telemetry hardware executor still accepts work after shutdown")


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
