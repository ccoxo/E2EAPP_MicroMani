# 阅读导航 07｜测试与验证
# 职责：回归验证：手动命令、工作原点操作与诊断日志，以及原点切换时的遥操作停止。
# 先看：FakeSettings → FakeTelemetry → FakeHal → RecordingHal。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.core.defaults import default_config
from backend.core.logging import LogService, now_ms
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
    def __init__(self) -> None:
        self.force_tare_calls = 0

    def tare_force(self) -> None:
        self.force_tare_calls += 1

    def apply_axis_move(
        self,
        side: str,
        axis: str,
        direction: int,
        step: float,
        config: dict[str, Any] | None = None,
    ) -> float:
        _ = (side, axis, config)
        return direction * step

    def home_all(self) -> None:
        return None

    def home_side(self, side: str, enabled_axes: list[bool] | None = None) -> None:
        _ = side

    def set_motion_axis_enabled(self, side: str, values: list[bool | None]) -> None:
        _ = (side, values)


class FakeHal:
    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"mode": "test", "command": name, "payload": payload or {}, "response": {"ok": True}}

    async def motion_state(self) -> dict[str, Any]:
        return {
            "timestamp_ms": now_ms(),
            "sample_cached": False,
            "pulses": [0.0] * 12,
            "enabled": [True] * 12,
            "enabled_confirmed": [True] * 12,
            "moving": [False] * 12,
            "estop_active": False,
        }


class RecordingHal(FakeHal):
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        active_payload = payload or {}
        self.commands.append((name, active_payload))
        return {"mode": "test", "command": name, "payload": active_payload, "response": {"ok": True}}


class DisabledMotionHal(RecordingHal):
    async def motion_state(self) -> dict[str, Any]:
        return {
            "timestamp_ms": now_ms(),
            "sample_cached": False,
            "pulses": [0.0] * 12,
            "enabled": [False] * 12,
            "enabled_confirmed": [True] * 12,
            "moving": [False] * 12,
            "estop_active": False,
        }


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
        self.pulses = list(pulses)

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        active = payload or {}
        if name in {"motion.home_origin_side", "motion.return_home_reference"}:
            side = str(active.get("side", "left"))
            target = list(active.get("pulse", []))
            enabled_axes = list(active.get("enabledAxes", [True] * 6))
            offset = 0 if side == "left" else 6
            for index, enabled in enumerate(enabled_axes[:6]):
                if enabled and index < len(target):
                    self.pulses[offset + index] = float(target[index])
        elif name == "motion.home_all":
            left = list(active.get("leftPulse", self.pulses[:6]))
            right = list(active.get("rightPulse", self.pulses[6:]))
            self.pulses = left + right
        return await super().command(name, active)

    async def motion_state(self) -> dict[str, Any]:
        return {
            "timestamp_ms": now_ms(),
            "sample_cached": False,
            "pulses": self.pulses,
            "enabled": [True] * 12,
            "enabled_confirmed": [True] * 12,
            "moving": [False] * 12,
            "estop_active": False,
        }


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


@pytest.mark.parametrize("initially_latched", [False, True])
def test_hkvl_self_check_preserves_latch_and_hal_statistics(monkeypatch: pytest.MonkeyPatch, initially_latched: bool) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    calibration = {"state": "ready_for_ack", "progress": 100, "completedAtUnixMs": 1234,
                   "sides": {"left": {"bias": [1.0] * 6, "residualMean": [0.001] * 6}}}

    class CalibrationHal(RecordingHal):
        async def command(self, name: str, payload=None):
            self.commands.append((name, payload))
            return {"mode": "real", "response": {"ok": True, "calibration": calibration}}

    config = default_config()
    config["force"]["tareSamples"] = 200
    logs = LogService(monotonic_ms=lambda: 123, session_id="s", emit_startup=False)
    hal = CalibrationHal()
    telemetry = FakeTelemetry()
    service = CommandService(FakeSettings(config), telemetry, hal, logs)
    if initially_latched:
        service.safety.interrupt(emergency=True)
    checks = []
    result = asyncio.run(service.tare_force(unloaded_confirmed=True, readiness_check=lambda: checks.append(True)))
    assert hal.commands == [("force.tare", {"side": "all", "samples": 200, "unloadedConfirmed": True})]
    assert result["hal"]["response"]["calibration"] == calibration
    assert telemetry.force_tare_calls == 0
    assert service.safety.latched is initially_latched
    assert len(checks) == 2
    assert any("both sides hkvl_serial tare requested" in item.msg for item in logs.list_entries())


@pytest.mark.parametrize("side,confirmed,reason", [("left", True, "both sensors"), ("right", True, "both sensors"), (None, False, "unloaded")])
def test_hkvl_self_check_rejects_single_side_or_missing_confirmation(monkeypatch, side, confirmed, reason) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    hal = RecordingHal()
    service = _service_with_hal(default_config(), LogService(emit_startup=False), hal)
    with pytest.raises(RuntimeError, match=reason):
        asyncio.run(service.tare_force(side, unloaded_confirmed=confirmed))
    assert hal.commands == []


def test_hkvl_self_check_still_requires_control_lease(monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    hal = RecordingHal()
    service = _service_with_hal(default_config(), LogService(emit_startup=False), hal)
    service.safety.interrupt(emergency=True)

    def reject():
        raise RuntimeError("control lease missing")

    with pytest.raises(RuntimeError, match="control lease missing"):
        asyncio.run(service.tare_force(unloaded_confirmed=True, readiness_check=reject))
    assert hal.commands == []


def test_hkvl_self_check_does_not_accept_result_after_new_estop(monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class InterruptedHal(RecordingHal):
        async def command(self, name, payload=None):
            service.safety.interrupt(emergency=True)
            return {"response": {"ok": True, "calibration": {"state": "ready_for_ack"}}}

    service = _service_with_hal(default_config(), LogService(emit_startup=False), InterruptedHal())
    with pytest.raises(RuntimeError, match="newer stop"):
        asyncio.run(service.tare_force(unloaded_confirmed=True))
    assert service.safety.latched
    assert service.telemetry.force_tare_calls == 0


def test_hkvl_self_check_logs_failure_without_resetting_force(monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    class FailedHal(RecordingHal):
        async def command(self, name, payload=None):
            raise RuntimeError("sensor residual unstable")

    logs = LogService(emit_startup=False)
    service = _service_with_hal(default_config(), logs, FailedHal())
    with pytest.raises(RuntimeError, match="sensor residual unstable"):
        asyncio.run(service.tare_force(unloaded_confirmed=True))
    assert service.telemetry.force_tare_calls == 0
    assert any(item.level == "ERROR" and "sensor residual unstable" in item.msg for item in logs.list_entries())


def test_hkvl_self_check_keeps_motion_resource_exclusive(monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class WaitingHal(RecordingHal):
            async def command(self, name, payload=None):
                started.set()
                await release.wait()
                return {"response": {"ok": True}}

        service = _service_with_hal(default_config(), LogService(emit_startup=False), WaitingHal())
        first = asyncio.create_task(service.tare_force(unloaded_confirmed=True))
        await started.wait()
        try:
            with pytest.raises(RuntimeError, match="already in progress"):
                await service.tare_force(unloaded_confirmed=True)
        finally:
            release.set()
            await first

    asyncio.run(run())


def test_nidaq_single_side_tare_keeps_existing_safety_gate(monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["force"]["source"] = "nidaq"
    service = _service(config, LogService(emit_startup=False))
    assert asyncio.run(service.tare_force("right"))["side"] == "right"
    assert service.telemetry.force_tare_calls == 1
    service.safety.interrupt(emergency=True)
    with pytest.raises(RuntimeError, match="emergency stop active"):
        asyncio.run(service.tare_force("right"))
    assert service.telemetry.force_tare_calls == 1


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
    assert any("event=work_origin_move" in message and "axis=right.Yaw" in message for message in messages)
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


def test_capture_motion_origin_keeps_mechanical_soft_limits_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    config = default_config()
    config["motion"]["leftSoftLimits"] = _wide_motion_soft_limits()
    soft_limits_before = json.loads(json.dumps(config["motion"]["leftSoftLimits"]))
    pulses = list(config["motion"]["origin"]["leftPulse"] + config["motion"]["origin"]["rightPulse"])
    service = _service_with_hal(config, LogService(emit_startup=False), FakeMotionStateHal(pulses))

    asyncio.run(service.capture_motion_origin("left", confirm_large_drift=True))

    assert config["motion"]["leftSoftLimits"] == soft_limits_before
    home_reference = side_home_reference_ui(config, "left")
    assert home_reference is not None
    limits = effective_limits_ui(config, "left")
    assert limits[5].min - home_reference[5] == pytest.approx(-7.0)
    assert limits[5].max - home_reference[5] == pytest.approx(7.0)
    service._validate_work_origin_target(config, "left")


def test_stationary_motion_accepts_recent_cached_controller_sample_and_rejects_old_sample() -> None:
    service = _service(default_config(), LogService(emit_startup=False))
    recent = {
        "timestamp_ms": now_ms(),
        "sample_cached": True,
        "moving": [False] * 12,
    }
    service.require_stationary_motion(recent)

    stale = {**recent, "timestamp_ms": now_ms() - 1_000}
    with pytest.raises(RuntimeError, match="stale or unavailable"):
        service.require_stationary_motion(stale)


def test_manual_axis_effective_direction_matches_site_direction_corrections() -> None:
    service = _service(default_config(), LogService(emit_startup=False))

    assert service._manual_axis_effective_direction("left", "X", 1) == 1
    assert service._manual_axis_effective_direction("left", "Y", 1) == -1
    assert service._manual_axis_effective_direction("left", "Z", 1) == 1
    assert service._manual_axis_effective_direction("right", "X", 1) == -1
    assert service._manual_axis_effective_direction("right", "Y", 1) == -1
    assert service._manual_axis_effective_direction("right", "Z", 1) == -1
