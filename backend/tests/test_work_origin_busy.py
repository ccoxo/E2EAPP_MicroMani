"""验证原点确认遇到遥测读取争用时的有界恢复与拒绝路径。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.core.motion_safety import MotionSafetyGate
from backend.services import command_service

BUSY = "dds-motion-state channel is busy; request was not queued"


def make_service(side_effect):
    service = object.__new__(command_service.CommandService)
    service.safety = MotionSafetyGate()
    service.hal = SimpleNamespace(motion_state=AsyncMock(side_effect=side_effect))
    return service


def state(**changes):
    return {"timestamp_ms": 1000, "pulses": [0] * 12,
            "moving": [False] * 12, "estop_active": False, **changes}


@pytest.mark.parametrize("invalid", [
    {"timestamp_ms": 0},
    {"moving": [True] * 12},
    {"pulses": [101] * 12},
])
def test_busy_retries_but_still_requires_fresh_stationary_origin(monkeypatch, invalid):
    service = make_service([RuntimeError(BUSY), state(**invalid), state()])
    monkeypatch.setattr(command_service, "now_ms", lambda: 1000)
    asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))
    assert service.hal.motion_state.await_count == 3


def test_persistent_busy_fails_at_existing_deadline(monkeypatch):
    service = make_service(RuntimeError(BUSY))
    clock = iter([0.0, 3.0])
    monkeypatch.setattr(command_service, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(RuntimeError, match="work origin result unconfirmed.*channel is busy"):
        asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))
    assert service.hal.motion_state.await_count == 1


def test_busy_wait_still_obeys_emergency_stop(monkeypatch):
    service = make_service(RuntimeError(BUSY))

    async def stop_during_wait(_delay):
        service.safety.interrupt(emergency=True)

    monkeypatch.setattr(command_service.asyncio, "sleep", stop_during_wait)
    with pytest.raises(RuntimeError, match="emergency stop active"):
        asyncio.run(service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture()))
    assert service.hal.motion_state.await_count == 1
