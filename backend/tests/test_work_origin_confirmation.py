import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.core.motion_safety import MotionSafetyGate
from backend.services import command_service


@pytest.mark.parametrize("error,moving,stamp,estop,accepted", [
    (0, False, 1000, False, True),
    (69, False, 1000, False, True),
    (-100, False, 1000, False, True),
    (100, False, 1000, False, True),
    (101, False, 1000, False, False),
    (69, True, 1000, False, False),
    (69, False, 0, False, False),
    (69, False, 1000, True, False),
])
def test_work_origin_confirmation(error, moving, stamp, estop, accepted, monkeypatch):
    async def exercise():
        service = object.__new__(command_service.CommandService)
        service.safety = MotionSafetyGate()
        service.hal = SimpleNamespace(motion_state=AsyncMock(return_value={
            "timestamp_ms": stamp, "pulses": [error] * 6 + [0] * 6,
            "moving": [moving] * 12, "estop_active": estop,
        }))
        clock = iter([0.0, 3.0])
        monkeypatch.setattr(command_service, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        monkeypatch.setattr(command_service, "now_ms", lambda: 1000)
        operation = service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture())
        if accepted:
            await operation
        else:
            with pytest.raises(RuntimeError, match="unconfirmed"):
                await operation

    asyncio.run(exercise())


def test_work_origin_confirmation_waits_for_fresh_sample_after_command(monkeypatch):
    service = object.__new__(command_service.CommandService)
    service.safety = MotionSafetyGate()
    state = {
        "timestamp_ms": 1000, "pulses": [0] * 12,
        "moving": [False] * 12, "estop_active": False,
    }
    service.hal = SimpleNamespace(motion_state=AsyncMock(side_effect=[
        RuntimeError("DDS motion controller sample timestamp is stale or unavailable"), state,
    ]))
    monkeypatch.setattr(command_service, "now_ms", lambda: 1000)

    asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))

    assert service.hal.motion_state.await_count == 2


def test_work_origin_confirmation_rejects_persistently_stale_sample(monkeypatch):
    service = object.__new__(command_service.CommandService)
    service.safety = MotionSafetyGate()
    service.hal = SimpleNamespace(motion_state=AsyncMock(side_effect=RuntimeError(
        "DDS motion controller sample timestamp is stale or unavailable")))
    clock = iter([0.0, 3.0])
    monkeypatch.setattr(command_service, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    with pytest.raises(RuntimeError, match="work origin result unconfirmed"):
        asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))


def test_work_origin_confirmation_does_not_hide_transport_failure():
    service = object.__new__(command_service.CommandService)
    service.safety = MotionSafetyGate()
    service.hal = SimpleNamespace(motion_state=AsyncMock(side_effect=RuntimeError(
        "DDS control channel isolated")))

    with pytest.raises(RuntimeError, match="DDS control channel isolated"):
        asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))

    assert service.hal.motion_state.await_count == 1


def test_work_origin_confirmation_stops_waiting_after_emergency_stop(monkeypatch):
    service = object.__new__(command_service.CommandService)
    service.safety = MotionSafetyGate()
    service.hal = SimpleNamespace(motion_state=AsyncMock(side_effect=RuntimeError(
        "DDS motion controller sample timestamp is stale or unavailable")))

    async def stop_during_wait(_delay):
        service.safety.interrupt(emergency=True)

    monkeypatch.setattr(command_service.asyncio, "sleep", stop_during_wait)

    with pytest.raises(RuntimeError, match="emergency stop active"):
        asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))


def test_work_origin_tolerance_matches_native_driver():
    source = (Path(__file__).resolve().parents[2] / "hal/src/LTDMCDriver.cpp").read_text(encoding="utf-8")
    native = re.search(r"kWorkOriginSettledPulseTolerance\s*=\s*(\d+)", source)
    assert native is not None
    assert command_service.WORK_ORIGIN_SETTLED_PULSE_TOLERANCE == int(native.group(1))
